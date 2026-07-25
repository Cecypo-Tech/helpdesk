# WA endpoint compute-cost review — 2026-07-25

Reported Frappe Cloud usage:

| Endpoint | Cumulative time |
|---|---|
| `wa.get_wa_conversations` | 141,165 s |
| `wa.get_wa_lines` | 44,025 s |
| `wa.webhook` | 5,796 s |

## Root cause model

Cost = **call volume × per-call cost**, and both factors are pathological.

### Factor 1 — fan-out refetch on every message

`_publish_wa_event()` broadcasts `helpdesk:baileys-message` to the `"all"` room for
**every** WA message, incoming *and* outgoing (`wa.py:697`).

Two global listeners refetch on it, with no debounce and no visibility check:

- `desk/src/components/layouts/Sidebar.vue:781` → `waLinesStore.reload()` → `get_wa_lines`.
  The sidebar mounts on **every agent page**, so every connected agent refetches on
  every message regardless of what they are looking at.
- `desk/src/components/whatsapp/BaileysConversationList.vue:261,270` → `conversations.reload()`
  → `get_wa_conversations`, for every agent with the WA page open.

`waLines` is also `auto: true` (`desk/src/stores/waLines.ts:18`), so it fires again on
every page load / route remount.

Local volume is ~500 WA messages/day. With ~10 connected agents that is ~5,000
`get_wa_lines` calls/day from the socket alone. Over a quarter, ~450k calls × ~300 ms
≈ 135,000 s — which matches the reported 141,165 s. The model holds.

### Factor 2 — both queries are O(entire message history) per call

Measured on local (`tabWA Message` = 19,667 rows):

`get_wa_lines` unread query (`wa.py:1736`):

```
id  table  type  possible_keys  key   rows    Extra
1   m      ALL   creation       NULL  21678   Using where; Using temporary; Using filesort
```

**Full table scan, no index used, on every call.** 43 ms locally warm; production
tables are larger and colder.

`get_wa_conversations` (`wa.py:1848`): the derived `MAX(creation) GROUP BY jid`
subquery scans all 21,678 rows (temporary + filesort), then `_unread_counts_for_user`
(`wa.py:1825`) runs a second aggregate over every incoming row for all 337 JIDs.
No `LIMIT`, no pagination — the whole conversation list is returned every time.

Both scale with total history, so cost per call grows forever even at constant traffic.

### Factor 3 — unread cursor has no floor

`Administrator`'s live unread counts: `SV = 7008`, `KP = 749`. Where a user has no
`WA Conversation Read State` row, `r.last_read IS NULL` makes **all history** unread,
so the count query must touch every incoming row ever received. This is both the
performance worst case and a bad badge ("7008").

### Factor 4 — `WA Message.message_id` has no index

`helpdesk/patches/drop_wa_message_id_unique_index.py` dropped the unique index and
nothing replaced it. Confirmed via `SHOW INDEX`: indexes are PRIMARY, `sender_jid`,
`line`, `creation`, `jid_index`, `wa_message_line_jid_creation` — no `message_id`.

Every `message_id` lookup is therefore a full table scan, and they run on the hottest
webhook paths:

- `_handle_update` (`wa.py:1170`) — status updates fire ~3–4× per message
  (sent/delivered/read), each doing a scan **plus its own `frappe.db.commit()` and
  `publish_realtime` inside the loop**.
- `_handle_upsert` dedup check (`wa.py:1040`), edit path (`wa.py:1006`), `_handle_delete`.

This is the bulk of the 5,796 s webhook time.

### Factor 5 — media download is synchronous in the webhook

`_extract_media_url` (`wa.py:660`) makes a blocking HTTP call to Evolution
(`_download_media_via_wa`) inside the webhook request. A `_retry_media_download`
background job already exists and is used when the inline attempt yields nothing —
the inline attempt is what stretches webhook wall time on media messages.

## Prioritised fixes

Ordered by expected saving per unit of work.

### P0 — stop the fan-out refetch (kills most of 185,000 s)

1. **Sidebar badge**: do not refetch `get_wa_lines` on `helpdesk:baileys-message`.
   The event payload already carries `{jid, is_incoming, line}` — increment the
   line's badge client-side. Refetch only on route entry and after mark-read.
2. **Conversation list**: extend the `_publish_wa_event` payload with
   `message`, `content_type`, `sender_name`, `creation`, `direction` so the list row
   can be patched in place instead of refetching the whole list.
3. **Guard rails for the refetches that remain**: trailing debounce (~3 s) and skip
   when `document.visibilityState === "hidden"`, refetching once on visibility regain.

### P1 — bound unread by a time floor (fixes the query's growth curve)

Add a hard `AND m.creation > :floor` predicate (e.g. 30 days, settings-configurable)
alongside the cursor comparison, so the unread scan is index-rangeable and bounded by
recency rather than by total history. Messages older than the floor count as read.
Fixes the 7008-unread badge as a side effect.

### P2 — indexes

- `WA Message (message_id)` — non-unique. Removes the webhook full scans.
- `WA Message (direction, creation, jid)` — makes the floored unread query a range
  scan and near-covering.

### P3 — paginate `get_wa_conversations`

Return the N most recent conversations with server-side search/filter instead of the
entire list. Note this changes UX: `filteredList` currently searches client-side over
the full set, so search must move server-side in the same change.

### P4 — webhook trimming

- Always enqueue media download; drop the inline Evolution call from the request path.
- In `_handle_update`, hoist `frappe.db.commit()` out of the per-item loop and batch
  the status lookups into one query.

## What shipped

All four priorities landed. Commits on `develop`:

| Commit | Scope |
|---|---|
| `8a27f9b1f` | P0 fan-out + P1 unread floor + P2 indexes |
| `357212ee0` | P4 webhook trimming |
| `7d8ae8bd4` | P3 pagination + server-side search/filters |

### Findings that changed the plan

**The composite index does not help `get_wa_lines`.** The plan assumed an index
would fix that query. It does not: the optimizer prefers the plain `creation`
index, and forcing `(direction, creation, jid)` measured identically (43 ms
either way). What fixed that query was the floor bounding how much history it
touches. The index was kept because it *does* help the per-JID unread query
inside `get_wa_conversations` (25.2 ms vs 34.4 ms), which is the more expensive
endpoint. This is documented in the patch so nobody "fixes" the plan with a hint.

**`message_id` needed no patch.** `search_index: 1` on the DocType field is
enough and is self-healing if the table is rebuilt. Frappe names it
`message_id_index`, which is why it does not collide with the index the old
`drop_wa_message_id_unique_index` patch removed.

**A Single doctype never picks up a new field's default.** `unread_window_days`
had to treat *unset* as 30 rather than 0, or every existing site would have
silently kept the unbounded behaviour.

**A fifth fan-out, not in the original review:** `TicketActivityPanel` refetched
its WhatsApp badge on every WhatsApp message in the system, for every agent with
any ticket open, regardless of whether the message belonged to that ticket.

### Measured (19.7k rows, 30-day floor)

| | before | after |
|---|---|---|
| `get_wa_lines` | 39.2 ms | 26.6 ms |
| `get_wa_conversations` | 54.6 ms | 29.3 ms (paged) |
| `_unread_counts_for_user` | 39.9 ms | 25.2 ms |
| `message_id` lookup | 5.8 ms | 0.18 ms |

Per-call numbers understate the result. The dominant term was call *volume*, and
the automatic per-message refetch is gone entirely — the remaining calls are
page loads, line switches, explicit actions, and a debounced merge for a
genuinely new conversation. This fixture also holds only ~2.5 months of history,
so a 30-day floor cuts about half its rows; production histories are deeper and
the ratio there should be better.

### Deliberate trade-offs

- Incoming messages older than the unread window count as read for everyone.
  Configurable, 0 disables. Note that if an admin later sets it back to 0,
  conversations that `mark_all_wa_messages_read` skipped as pre-floor will
  resurface as unread.
- Search runs an unindexable `message LIKE` scan. It is debounced and
  user-initiated, not on the automatic path. In exchange it now searches the
  whole conversation instead of only the latest message.
- Media now always arrives via the background job rather than inline, so a
  bubble fills in a moment after the message appears.

### Not addressed

`helpdesk.tests.test_baileys` fails on `develop` already — it patches a stale
`helpdesk.integrations.baileys` module. Pre-existing and unrelated; left alone.

## Verification plan

- `EXPLAIN` before/after on both hot queries — assert no `type=ALL` on `tabWA Message`.
- Timing harness over the real local dataset for `get_wa_lines` /
  `get_wa_conversations` before vs after.
- Existing suite: `bench --site dev.localhost run-tests --app helpdesk --module
  helpdesk.tests.test_wa_unread_state`.
- New regression test: unread count respects the time floor and stays per-agent.
- Manual: two browser sessions, send a message, confirm badges update without a
  network refetch and that per-agent read state still diverges correctly.
