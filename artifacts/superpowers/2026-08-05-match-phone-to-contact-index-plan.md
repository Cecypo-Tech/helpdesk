# Plan: index the phone → contact lookup

Date: 2026-08-05
Follows: `2026-08-05-wa-normalized-phone-column-plan.md` (same technique, different table)

## Problem

`match_phone_to_contact()` (`wa.py:285`) runs on every inbound WhatsApp message,
from `on_whatsapp_message_insert()`. It loads **every** Contact with a number and
**every** Contact Phone row into Python, then loops over both twice — once for an
exact match, once for the trailing-digit comparison.

Measured on this dev site (486 Contacts, 303 Contact Phone rows):

| case | time |
|------|------|
| hit — exact match, returns early | 1.6 ms |
| miss — unknown number | 4.9 ms |

The miss is both the common case for a new conversation and the worst case, since
it exhausts every loop. Cost is linear in contact count.

## Approach

Same technique that worked for the conversation key: store the comparison key at
write time and index it, then do the precise comparison in Python over a handful
of candidate rows instead of the whole table.

The key is the **trailing subscriber digits** — `_PHONE_SUFFIX_DIGITS = 9`, or the
whole number when shorter. Suffix equality is what `_phones_match()` already
tests, so an indexed equality lookup on it produces exactly the candidate set the
current scan would consider:

- two numbers that are exactly equal have equal suffixes, so exact matches are
  always in the candidate set
- a stored number shorter than 9 digits keeps its whole value as the suffix, and
  `_phones_match()` already refuses to loosely match anything under 9 digits, so
  behaviour is unchanged at that boundary

**The matching semantics do not change.** Exact match still wins outright; a loose
match still has to be unique or the function returns None. The existing tests in
`test_wa_contact_link.py` cover exactly that and must keep passing untouched —
that is the safety net for this change.

## Where the column goes

`Contact Phone` is the canonical store: frappe's `Contact.validate()` derives
`Contact.phone` / `Contact.mobile_no` from the `phone_nos` child table, which is
why CLAUDE.md already tells us to write numbers via `phone_nos`, never directly.

Verified on this site: 303 Contacts carry a number, there are 303 Contact Phone
rows, and **zero** Contacts have a number with no child row.

That said, a legacy row on production with `mobile_no` set and no child row would
silently stop matching if we indexed only the child table — a contact that used to
resolve would start creating duplicates. So the field goes on **both**:

- `Contact Phone.phone_suffix` — every number a contact holds
- `Contact.phone_suffix` — suffix of `mobile_no or phone`, covering the legacy shape

Both are looked up; the union is the candidate set. The patch reports how many
Contacts fall into the legacy shape so we learn whether production differs from
dev.

## Components

### 1. Fields

Two Custom Field fixtures, following `whatsapp_message_normalized_phone_field.json`:

- `helpdesk/fixtures/contact_phone_suffix_field.json` → `Contact Phone.phone_suffix`
- `helpdesk/fixtures/contact_phone_suffix_parent_field.json` → `Contact.phone_suffix`

Both `Data`, `read_only`, `no_copy`, registered in `hooks.py` `fixtures`.

### 2. Population on write

New `Contact` doc_event in `hooks.py`:

```python
"Contact": {"before_save": "helpdesk.integrations.wa.set_contact_phone_suffix"},
```

`before_save`, not `validate`: frappe runs `before_validate → validate →
before_save`, and `Contact.validate()` is what populates `mobile_no`/`phone` from
the children. Running earlier would read stale values.

The handler sets the suffix on the parent and on each `phone_nos` row. Assigning a
field the doctype lacks is a no-op in frappe, so this is safe pre-migrate.

### 3. Backfill and index

`helpdesk/patches/add_contact_phone_suffix.py`, modelled on
`add_wa_message_normalized_phone` — including its two hard-won details:

- **create the Custom Fields in the patch**, not via the fixture. Patches run in
  `run_schema_updates()`, fixtures sync in `post_schema_updates()` — afterwards.
  A fixture-dependent patch would find no column on first migrate, skip the
  backfill, and never run again.
- **commit between the backfill and the `ALTER`**. Frappe rejects DDL after DML
  in one transaction (`ImplicitCommitError`), and this order also lets the bulk
  UPDATE run unindexed with the index built once.

Backfill, restricted to empty rows so a re-run is free:

```sql
UPDATE `tabContact Phone`
SET phone_suffix = RIGHT(REGEXP_REPLACE(phone, '[^0-9]', ''), 9)
WHERE IFNULL(phone_suffix, '') = '' AND IFNULL(phone, '') != ''
```

`RIGHT(x, 9)` returns the whole string when it is shorter than 9, matching the
Python rule. Same for `Contact`, keyed off `IFNULL(mobile_no, phone)`.

Then a plain index on `phone_suffix` for each table.

Also log the legacy-shape count:

```sql
SELECT COUNT(*) FROM tabContact c
WHERE (IFNULL(c.phone,'')!='' OR IFNULL(c.mobile_no,'')!='')
  AND NOT EXISTS (SELECT 1 FROM `tabContact Phone` p WHERE p.parent = c.name)
```

### 4. The lookup

```python
def match_phone_to_contact(phone: str) -> str | None:
    normalized = _normalize_phone(phone)
    if not normalized:
        return None
    if not _contact_has_phone_suffix():
        return _match_phone_to_contact_scan(normalized)   # today's code, unchanged
    suffix = normalized[-_PHONE_SUFFIX_DIGITS:]
    candidates = <indexed lookup on Contact Phone and Contact by suffix>
    # exact first, then a unique loose match — same rules as now, over few rows
```

The scan is kept as `_match_phone_to_contact_scan()` and used when the columns are
absent. That is not optional: code deploys before `bench migrate` runs, and
querying a missing column would throw on every inbound message during that window.

## Verification

New `helpdesk/tests/test_contact_phone_suffix.py`:

- saving a Contact stores the suffix on the parent and on every `phone_nos` row
- a number with punctuation stores digits only; a number under 9 digits stores
  whole
- the backfill fills empty rows, leaves populated rows alone, and is idempotent
- indexes exist after the patch
- **the indexed path and the scan return identical results** across: exact match,
  national-vs-international match, ambiguous match (must be None), no match, and
  empty input — the test that actually proves the optimisation is safe

`test_wa_contact_link.py` must pass **unchanged**; it already encodes the matching
semantics, so any behaviour drift shows up there.

Then: re-run the benchmark above and record hit/miss timings, and the full suite
against `develop`.

## Risks

- **Semantics drift.** Mitigated by the identical-results test plus the untouched
  `test_wa_contact_link.py`.
- **Contact is a core doctype** shared with every other app on the bench. Adding a
  read-only custom field is additive and does not alter existing behaviour, but it
  does mean a `before_save` hook now runs on every Contact save bench-wide. The
  handler is a couple of string operations with no queries.
- **Backfill cost**: two UPDATEs over `tabContact` and `tabContact Phone`. Smaller
  tables than `tabWhatsApp Message`, but the same brief lock applies.

## Not included

- Deduplicating the Contacts already created by the earlier customer-link bug
  (user decision: fix forward).
- `get_contact_phone()`, which resolves the other direction and is already a
  keyed lookup.
