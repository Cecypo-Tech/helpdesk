# Plan: report and merge WhatsApp duplicate contacts

Date: 2026-08-06
Cleans up after: `2026-08-05-waba-customer-link-lost-investigation.md`

## What created the duplicates

`on_whatsapp_message_insert()` creates a Contact when a phone number does not
match an existing one:

```python
c = frappe.get_doc({
    "doctype": "Contact",
    "first_name": profile_name,
    "phone_nos": [{"doctype": "Contact Phone", "phone": phone, "is_primary_mobile_no": 1}],
})
```

Only a name and a number — **no email**, no `HD Customer` link. Matching failed
whenever the real contact stored its number nationally (`0799…`) against
WhatsApp's international form (`254799…`), so a fresh Contact was minted for a
customer who already existed.

Correction to an earlier suggestion in this session: these cannot be found by
`email_id LIKE 'whatsapp+%'`. That placeholder address is written to
`HD Ticket.raised_by`, never to the Contact. That query returns nothing and would
wrongly suggest there is no damage.

## Why "shares a phone number" is not the rule

Run against this dev site, grouping contacts by `phone_suffix` finds two groups
of 162 and 160 contacts — every `_Test Contact For _Test Customer-N` fixture,
all sharing `+91 0000000000`. A tool that merged same-number groups would have
collapsed 162 unrelated records into one, irreversibly.

Real sites have the same hazard: a switchboard number, a shared reception line,
a placeholder someone typed into many records.

## Detection rules

A candidate is a **pair** — exactly two contacts sharing a normalized phone
suffix — where exactly one is *automation-shaped*:

- no `email_id`
- no `Dynamic Link` to `HD Customer`
- no linked `User`

and the other is not. Anything else is reported as `skipped` with a reason:

| Condition | Reason |
|-----------|--------|
| group larger than 2 | `group_too_large` |
| both look automation-shaped | `ambiguous_no_keeper` |
| neither looks automation-shaped | `no_duplicate_shape` |
| suffix has fewer than 4 distinct digits | `placeholder_number` |

The last rule is what excludes the `0000000000` fixtures, and would exclude the
same shape on production.

## Components

New module `helpdesk/integrations/wa_contact_dedupe.py` — separate from `wa.py`,
which is already very large, and because this is a one-off maintenance tool
rather than part of the message path.

### `report_duplicate_contacts() -> dict`

Read-only. Returns `{"candidates": [...], "skipped": [...]}` and prints a table
when run from bench. Each candidate carries the evidence needed to judge it:

- `suffix`, and the raw numbers as stored on each side
- duplicate: name, `first_name`, `creation`, count of HD Tickets referencing it
- keeper: name, `first_name`, its `HD Customer`, count of HD Tickets

Run with:

```bash
bench --site <site> execute helpdesk.integrations.wa_contact_dedupe.report_duplicate_contacts
```

### `merge_duplicate_contacts(dry_run=1, limit=0) -> dict`

Dry run by default — it must be asked explicitly to write. For each candidate it
**re-derives the rules at write time** rather than trusting a stale report, then:

1. repoints `HD Ticket.contact` from the duplicate to the keeper
2. fills `HD Ticket.customer` where empty, from the keeper's `HD Customer` link —
   this repairs the tickets the bug left with no customer, which is the visible
   symptom that started this
3. deletes the duplicate with a **normal** `frappe.delete_doc` — no `force`

Not forcing the delete is deliberate: if anything else still references that
Contact (a Communication, a ToDo, another app's link), frappe refuses and the
row is reported as `failed` with the error, rather than being destroyed along
with whatever pointed at it.

Returns a per-pair record of what was changed, so the run is auditable.

## Verification

New `helpdesk/tests/test_wa_contact_dedupe.py`, building each scenario:

- a bug-shaped pair is reported as a candidate, with the automation-shaped
  contact identified as the duplicate and not the other way round
- a group of three is skipped as `group_too_large`
- a pair where both carry emails is skipped as `no_duplicate_shape`
- a placeholder number shared by many contacts is skipped as
  `placeholder_number` — the `_Test` fixture shape
- `dry_run=1` changes nothing: ticket still points at the duplicate, duplicate
  still exists
- a real merge repoints the ticket, fills the empty `customer`, and deletes the
  duplicate
- a duplicate that another document still references is reported as `failed`
  and survives

Then the full suite against `develop`.

## Risks

- **Not reversible.** Deleting a Contact cannot be undone from the app. Mitigated
  by: report-first workflow, dry-run default, pair-only rule, placeholder filter,
  rules re-derived at write time, and no forced deletes.
- **Cannot be validated against real duplicates here.** This dev site has zero
  automation-shaped contacts, so every scenario is constructed. The report step
  exists precisely so production data is inspected by a human before anything is
  written.
- **Keeper choice.** The rules pick the keeper by shape, not by richness. Where
  both sides carry real data the pair is skipped rather than guessed at.

## Not included

- Merging `Contact Phone` rows from the duplicate onto the keeper. The duplicate's
  number is the one that failed to match; now that suffix matching is in place the
  keeper's own number resolves it. Copying it over would add a redundant row.
- Any change to `on_whatsapp_message_insert`. It stopped creating these once the
  suffix matching landed on 2026-08-05.
