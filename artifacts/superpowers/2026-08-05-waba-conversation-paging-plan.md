# Plan: paginate and filter the WhatsApp Business conversation list

Date: 2026-08-05
Page: `/helpdesk/whatsapp-business` (WABA). The WA Line list is untouched.

## Problem

Two reports on the same page:

1. The list loads 200+ conversations at once and feels slow.
2. The sidebar shows 14 open WABA tickets with no way to filter down to them.

### Current behaviour

`get_whatsapp_conversations()` (`wa.py:3021`) takes no arguments and has no
`LIMIT`. It:

- selects **every** row of `tabWhatsApp Message`
- reduces to latest-per-phone in Python
- scans the whole `Contact` table (plus `Contact Phone`) to build a phone → name map
- returns every conversation

Cost scales with total message history, not with what is displayed.
`WhatsAppConversationList.vue` then filters by search **in the browser**.

The WA Line list already solved this: `get_wa_conversations()` (`wa.py:2204`)
is paginated with server-side search and filters, and
`BaileysConversationList.vue` renders filter chips plus a "Load older
conversations" button. This plan mirrors that, so both WhatsApp pages behave
identically.

### The 14

`helpdesk/api/general.py get_my_open_counts()` counts DISTINCT HD Tickets that
have a WhatsApp Message and whose status is not Resolved/Closed. So the badge
means **open WABA tickets**, not unread messages.

## Decisions taken

| Question | Decision |
|----------|----------|
| Filters | All / Open / Awaiting reply |
| Paging | "Load older conversations" button, mirroring the WA Line list |

`Open` = latest linked ticket not Resolved/Closed (matches the badge).
`Awaiting reply` = latest message is Incoming, i.e. the customer is waiting.

Deliberately excluded: an `Unread` chip. WABA unread state lives in
`localStorage` per browser (`whatsapp_business_last_read_<phone>`, see
`isUnread()`), so the server cannot filter on it. Filtering after the page cut
would silently shrink pages — the failure mode `get_wa_conversations`'s
docstring already warns about.

## Backend — `helpdesk/integrations/wa.py`

Rewrite `get_whatsapp_conversations()` to:

```python
def get_whatsapp_conversations(
    search: str = "",
    conv_filter: str = "all",
    limit: int = CONVERSATIONS_PAGE_SIZE,
    offset: int = 0,
) -> dict:            # {"conversations": [...], "has_more": bool}
```

- Group to one row per phone **in SQL**, not in Python:
  `REGEXP_REPLACE(CASE WHEN type='Incoming' THEN \`from\` ELSE \`to\` END, '[^0-9]', '')`
  as the phone key, `MAX(creation)` as the sort key, ordered descending with
  `LIMIT`/`OFFSET`. (MariaDB 10.6+, which Frappe v16 requires, has
  `REGEXP_REPLACE`.)
- Apply `search` and `conv_filter` inside that grouped query, before the cut,
  so a page always means a full page.
- Enrich **only the phones on the page**: ticket info, task counts and the
  contact-name lookup all take `WHERE ... IN (page phones)` instead of scanning
  whole tables.
- Fetch `limit + 1` rows to derive `has_more` without a second COUNT.
- Keep the returned per-conversation shape byte-identical to today
  (`phone`, `display_name`, `last_message`, `last_message_time`,
  `last_direction`, `ticket_status`, `ticket_priority`, `company`,
  `assigned_to`, `open_task_count`) so the item component needs no changes.

Reuse the existing `CONVERSATIONS_PAGE_SIZE = 50` (`wa.py:2110`).

## Frontend — `desk/src/components/whatsapp/WhatsAppConversationList.vue`

- Accumulate pages in a `loadedList` ref rather than reading `conversations.data`
  directly, mirroring `BaileysConversationList.vue`.
- Filter chips row directly under the header, same pill styling as the WA Line
  list (green when active): All / Open / Awaiting reply.
- "Load older conversations" button at the foot of the list when `has_more`.
- Search and filter changes reset to offset 0 and refetch; search debounced so
  each keystroke is not a query.
- Socket `helpdesk:whatsapp-message` currently calls `conversations.reload()`.
  It must refresh the **first page** and merge, not blow away loaded pages —
  `BaileysConversationList.vue` already does exactly this; copy that handling.
- `isUnread()` and the per-phone localStorage read state stay as they are; they
  drive the unread dot, which is unaffected by paging.

## Verification

- Backend tests (new `helpdesk/tests/test_wa_conversation_page.py`):
  - a page returns at most `limit` conversations and `has_more` is true when
    more exist
  - `offset` returns the next distinct set with no overlap and no gaps
  - `conv_filter="open"` returns only conversations whose ticket is not
    Resolved/Closed, and its count matches `get_my_open_counts()["whatsapp"]`
  - `conv_filter="awaiting"` returns only conversations whose last message is
    Incoming
  - `search` matches on display name and on message text, applied before the cut
  - the per-conversation keys are unchanged from the current implementation
- `bench build --app helpdesk`, then a manual pass: list shows one page, the
  button loads the next, chips narrow the list, and the Open chip count lines up
  with the sidebar badge.
- Full suite compared against `develop` for regressions.

## Risks

- `REGEXP_REPLACE` in a `GROUP BY` cannot use an index, so the grouped query is a
  scan of `tabWhatsApp Message`. That is still far cheaper than today (which
  scans the same table **and** materialises every row in Python **and** scans
  `Contact`), but on a very large history it may warrant a stored normalized
  phone column later. Out of scope here; noted rather than fixed.
- The socket-merge path is the easiest thing to get wrong — a naive `reload()`
  would discard loaded pages and make the list jump. Covered by copying the WA
  Line handling rather than writing new logic.
