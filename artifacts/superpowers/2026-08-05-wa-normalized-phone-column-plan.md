# Plan: stored normalized-phone column on WhatsApp Message

Date: 2026-08-05
Follows: `2026-08-05-waba-conversation-paging-plan.md`

## Problem

`get_whatsapp_conversations()` keys every conversation on a phone number that is
computed at query time:

```sql
REGEXP_REPLACE(CASE WHEN `type`='Incoming' THEN `from` ELSE `to` END, '[^0-9]', '')
```

It appears twice in the main query — in the `SELECT` and in the window's
`PARTITION BY` — and again in the ticket-link query. A computed expression can
never use an index, so the query is a full scan of `tabWhatsApp Message` on every
page load and after every incoming message, however small the page.

Storing the value once, at write time, makes it indexable.

## Constraint

`WhatsApp Message` belongs to the vendored `frappe_whatsapp` app, whose only
remote is upstream — we cannot deploy schema changes to it. The fork already
solves this: `helpdesk/fixtures/whatsapp_account_bot_field.json` ships a Custom
Field onto `WhatsApp Account`, another frappe_whatsapp doctype. Same approach here.

## Components

### 1. The field

`helpdesk/fixtures/whatsapp_message_normalized_phone_field.json`:

```json
{
 "doctype": "Custom Field",
 "name": "WhatsApp Message-normalized_phone",
 "dt": "WhatsApp Message",
 "fieldname": "normalized_phone",
 "fieldtype": "Data",
 "label": "Normalized Phone",
 "read_only": 1,
 "insert_after": "profile_name",
 "module": "Helpdesk"
}
```

Register it in `hooks.py` `fixtures` alongside the existing Custom Field entries.

### 2. Population on write

New `before_insert` doc_event for `WhatsApp Message` in `hooks.py`, pointing at a
new `wa.set_wa_message_normalized_phone(doc, method=None)`:

```python
doc.normalized_phone = _normalize_phone(
    doc.get("from") if doc.type == "Incoming" else doc.get("to")
)
```

`before_insert` rather than `after_insert` so it is one write, not two. Setting an
attribute the doctype does not have is harmless on an un-migrated site — frappe
simply does not persist it — so no guard is needed on the write path.

Note the existing `after_insert` hooks stay as they are; this is a separate,
earlier event and does not reorder them.

### 3. Backfill and index

`helpdesk/patches/add_wa_message_normalized_phone.py`, registered in
`patches.txt`, modelled on `add_wa_message_composite_index`:

- return early if the table or the column is absent
- backfill in one statement, only where empty, so it is re-runnable:
  ```sql
  UPDATE `tabWhatsApp Message`
  SET normalized_phone = REGEXP_REPLACE(
      CASE WHEN `type`='Incoming' THEN `from` ELSE `to` END, '[^0-9]', '')
  WHERE IFNULL(normalized_phone, '') = ''
  ```
- add index `wa_message_normalized_phone_creation` on
  `(normalized_phone, creation)`, guarded by `SHOW INDEX` like the existing patch.
  That composite serves both the `PARTITION BY normalized_phone ORDER BY creation
  DESC` window and the `ORDER BY creation DESC` page cut.

### 4. Query switch, with a fallback

`_waba_phone_sql(alias)` currently returns the `REGEXP_REPLACE` expression. It
becomes:

```python
def _waba_phone_sql(alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    if _wa_has_normalized_phone():
        return f"{p}`normalized_phone`"
    return f"REGEXP_REPLACE(CASE WHEN {p}`type`='Incoming' THEN {p}`from` ELSE {p}`to` END, '[^0-9]', '')"
```

`_wa_has_normalized_phone()` wraps `frappe.db.has_column("WhatsApp Message",
"normalized_phone")`, cached per request. The fallback is not optional: CLAUDE.md
records that custom fields are missing on sites that have not run `bench migrate`,
and that every query touching them must tolerate it. Without the fallback the
WhatsApp page would 500 between deploy and migrate.

## Verification

New `helpdesk/tests/test_wa_normalized_phone.py`:

- an Incoming message stores the sender's digits; an Outgoing message stores the
  recipient's digits
- a number written with punctuation/spaces stores digits only, matching
  `_normalize_phone`
- the backfill patch fills rows inserted with the column empty, and leaves
  already-populated rows untouched (re-runnable)
- `get_whatsapp_conversations()` returns identical results with the column in use
  and with the fallback expression forced, over the same seeded data — this is
  the test that matters, since it proves the switch is behaviour-preserving
- the patch is idempotent: running it twice adds one index and changes no rows
  the second time

Plus: `bench --site dev.localhost migrate`, then `EXPLAIN` on the conversation
query before and after to record rows-scanned, mirroring the measurement noted in
`add_wa_message_composite_index`. Then the full suite against `develop`.

## Risks

- **Deploy ordering.** Code ships before `bench migrate` runs. The fallback covers
  that window; the test for it is the identical-results test above.
- **Backfill cost.** One UPDATE over the whole table. On a large history this
  locks the table briefly during migrate. Acceptable for a maintenance window;
  worth mentioning to the user before deploying to production.
- **Divergence.** The stored value and `_normalize_phone()` must agree forever.
  Both derive from the same rule, and the identical-results test pins them
  together, but a future change to `_normalize_phone` would need a re-backfill.
  Noted in the patch docstring.

## Not included

- `match_phone_to_contact()` still scans Contact and Contact Phone in Python.
  That is a different table and a separate optimisation.
- The sidebar badge stays counting tickets (user decision, 2026-08-05).
