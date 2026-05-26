# WA Contact LID/PN Deduplication Design

**Date:** 2026-05-26  
**Status:** Approved  

## Problem

WhatsApp is migrating to LID (Linked Identity) addressing for privacy. The same person now has two JIDs:

- `88184386015384@lid` — anonymous identity assigned by Meta
- `447985251829@s.whatsapp.net` — phone-number-based JID (legacy)

Both JIDs arrive in `contacts.upsert` events from the gateway. `_upsert_contact` (wa.py:423) is keyed on `jid`, so both create separate `WA Contact` rows. The conversation list shows the same person twice, and agent-set metadata (name, company, team) diverges between rows.

## Decisions

- **Canonical row:** PN (`@s.whatsapp.net`) always wins — it is the canonical row.
- **Merge strategy:** Physical — `WA Message.jid` and `HD Ticket.baileys_jid` are re-keyed from LID to PN at merge time.
- **Scope:** Forward-looking only. Existing data is cleared with a one-time wipe + fresh sync.

---

## Section 1 — Schema

Add one field to `WA Contact`:

| Field | Fieldname | Fieldtype | Behaviour |
|-------|-----------|-----------|-----------|
| Canonical JID | `canonical_jid` | Data | Empty = this row is canonical. Non-empty = this row is a dead LID alias pointing to the PN JID. |

- A `Data` field (not `Link`) because the PN row may not exist yet when the field is written.
- Dead alias rows are never shown in the UI but are retained so future incoming messages on the LID JID can be re-routed without recreating the row.

---

## Section 2 — Merge Logic

### `_merge_lid_into_pn(lid_jid: str, pn_jid: str)`

New internal function. Steps:

1. Ensure the PN `WA Contact` row exists (call `_upsert_contact` for `pn_jid` first).
2. Copy `custom_name`, `company`, `assigned_team` from LID row → PN row for any fields that are blank on the PN row.
3. `UPDATE tabWA Message SET jid = pn_jid WHERE jid = lid_jid`
4. `UPDATE tabHD Ticket SET baileys_jid = pn_jid WHERE baileys_jid = lid_jid` (wrapped in `try/except` — column may not exist on all sites).
5. `frappe.db.set_value("WA Contact", {"jid": lid_jid}, "canonical_jid", pn_jid)`
6. `frappe.db.commit()`

**Idempotent:** if `canonical_jid` is already set on the LID row, skip entirely.

### Trigger points

- **`upsert_contact_mapping(lid, phone, name)`:** primary trigger — this is the endpoint the gateway calls with a confirmed LID+phone pair on every message. Call `_merge_lid_into_pn(lid, f"{phone}@s.whatsapp.net")` here.
- **`_upsert_contact(jid, phone, name)`:** if `jid` ends with `@lid` and `phone` is non-empty, call `_merge_lid_into_pn(jid, pn_jid)` instead of a plain upsert. (Catches any callers that already have the pair.)
- **`_handle_contacts_upsert(contacts)`:** for each contact entry where `id` ends with `@lid` and the payload includes a resolvable phone field (e.g. `notify`, `verifiedName`), trigger the merge.

---

## Section 3 — Webhook Re-routing

At the top of the incoming message handler, after resolving `jid`, add:

```python
canonical = frappe.db.get_value("WA Contact", {"jid": jid}, "canonical_jid")
if canonical:
    jid = canonical
```

Applied to both `jid` (conversation JID) and `sender` / `sender_jid` (group participant JID). All downstream processing — ticket lookup, message insert, realtime publish — sees only the PN JID.

---

## Section 4 — Query Changes

**`get_wa_conversations`** — no change needed. Once messages are re-keyed to the PN JID, the LID JID no longer appears in `WA Message` and never enters the conversation list.

**`search_whatsapp_contacts`** — add `AND (canonical_jid IS NULL OR canonical_jid = '')` to both query paths so LID alias rows never surface in the new-chat picker.

**`upsert_wa_contact`** (agent-facing edit endpoint) — if the target JID has `canonical_jid` set, redirect the write to the canonical PN row. Prevents agents accidentally editing a dead alias.

---

## Section 5 — Background Sync

Current behaviour: the "Sync contacts" button chains two blocking HTTP calls in the request thread, causing long waits with a 90 s timeout on `sync_wa_groups`.

### Changes

1. New whitelisted endpoint `enqueue_wa_sync` — enqueues an RQ job and returns `{"status": "queued"}` immediately.
2. RQ job runs `sync_wa_contacts()` then `sync_wa_groups()` in sequence.
3. On completion, job emits realtime event `helpdesk:wa-sync-complete` with result counts `{contacts, groups}`.
4. Frontend: button click calls `enqueue_wa_sync`, shows a "Syncing…" spinner, listens for `helpdesk:wa-sync-complete`, then shows the result toast and reloads the conversation list.

### Bulk upsert for contacts

Replace the N+1 loop in `sync_wa_contacts` with a single SQL:

```sql
INSERT INTO `tabWA Contact` (name, jid, phone, custom_name, ...)
VALUES (...)
ON DUPLICATE KEY UPDATE
  custom_name = IF(custom_name = '' OR custom_name IS NULL, VALUES(custom_name), custom_name),
  phone       = IF(phone = '' OR phone IS NULL, VALUES(phone), phone)
```

This cuts the dominant cost from O(N) round-trips to one query.

---

## Section 6 — Migration

Since existing `WA Contact` data contains duplicate LID/PN rows, a clean slate is preferred:

1. New whitelisted API `wipe_wa_contacts` — executes `DELETE FROM tabWA Contact`, clears all rows.
2. Exposed as a button in WA API Settings (or callable via `bench execute`) — no SSH needed.
3. After wipe, click "Sync contacts" — the updated sync runs with merge logic in place; only PN rows are created, and any LID that arrives with a known phone is merged immediately.
4. Historical `WA Message.jid` rows with LID values become orphaned (no `WA Contact` row) — acceptable since the chat history still exists and new messages re-route correctly.

---

## Affected Files

| File | Change |
|------|--------|
| `helpdesk/helpdesk/doctype/wa_contact/wa_contact.json` | Add `canonical_jid` field |
| `helpdesk/helpdesk/doctype/wa_contact/wa_contact.py` | (controller, no logic change needed) |
| `helpdesk/integrations/wa.py` | `_merge_lid_into_pn`, trigger in `_upsert_contact` + `_handle_contacts_upsert`, webhook re-routing, query guards in `search_whatsapp_contacts` + `upsert_wa_contact`, `enqueue_wa_sync`, `wipe_wa_contacts`, bulk upsert in `sync_wa_contacts` |
| `desk/src/components/whatsapp/BaileysConversationList.vue` | Update sync button to use `enqueue_wa_sync`, listen for `helpdesk:wa-sync-complete` |
