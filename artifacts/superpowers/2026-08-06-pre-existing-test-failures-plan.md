# Plan: clear the pre-existing test failures

Date: 2026-08-06

## Correction to earlier reporting

Through this session I described these as "~37 failures, mostly tests making live
HTTP calls to hd-whatsapp-api.cecypo.tech". That was wrong on both counts. The
actual figure is **28 across 7 modules**, and live HTTP accounts for 2 of them.
The single largest group is a missing dev dependency.

## Root causes

| Count | Module | Cause |
|-------|--------|-------|
| 11 | `hd_ticket.test_hd_ticket` | `ModuleNotFoundError: No module named 'freezegun'` |
| 7 | `integrations.tests.test_wa_webhook` | 5 × `ValueError: too many values to unpack (expected 2, got 3)`; 2 × live HTTP 404 against `hd-whatsapp-api.cecypo.tech` |
| 4 | `tests.test_baileys_standalone` | `DocType Baileys Gateway Settings not found` |
| 3 | `hd_form_script.test_hd_form_script` | `HD Ticket.raised_outside_working_hours` is a Check field with `"default": "False"` |
| 2 | `api.agent_home.test_agent_home` | `AssertionError: 3305 not found in ['3307', '3306', '3305']` — int compared against strings |
| 1 | `wa_api_settings.test_wa_api_settings` | asserts a `default_team` field the doctype does not have |
| 1 | `hd_task.test_hd_task` | `DuplicateEntryError: HD Customer '_Test Create Task Customer'` left by an earlier run |

## The one that is not just a test problem

`helpdesk/helpdesk/doctype/hd_ticket/hd_ticket.json:479`:

```json
{
 "default": "False",
 "fieldname": "raised_outside_working_hours",
 "fieldtype": "Check",
```

Frappe requires a Check default of `"0"` or `"1"` and throws
`ValidationError: The default value for the Check field ... must be either '0'
or '1'` when the doctype is customised. The three `hd_form_script` failures are
just where it surfaces — **Customize Form on HD Ticket fails the same way for a
real user**. This is a live defect, not test noise.

Introduced by upstream commit `047c826a9` ("refactor: refine logic for detecting
tickets outside working hours").

## Fixes

### 1. `freezegun` (11)

Install into the bench env and declare it, so a fresh checkout does not hit this:

```bash
./env/bin/pip install freezegun
```

Add to the app's dev dependencies in `pyproject.toml`. `hd_ticket`'s SLA and
working-hours tests import it at module level, which is why all 11 fail together
rather than individually.

### 2. `raised_outside_working_hours` default (3)

`"default": "False"` → `"0"` in `hd_ticket.json`, then `bench migrate` to push the
corrected DocField. Worth a note to upstream, since the defect is theirs.

### 3. `test_wa_webhook` (7)

- `_extract_edit()` returns `(new_text, is_edit, original_message_id)` — three
  values. Five tests still unpack two, so they were written against an older
  signature and never updated. Fix the call sites in the test.
- Two tests reach `hd-whatsapp-api.cecypo.tech` and fail on a 404. Tests must not
  depend on live infrastructure: patch at the `_evo_session` boundary, the way
  `test_wa_read_receipt` and `test_wa_templates` already do.

### 4. `test_baileys_standalone` (4)

References `Baileys Gateway Settings`, renamed by the
`rename_evolution_doctypes_to_wa` patch. Point the tests at the current
doctype (`WA API Settings` / `WA Line` as appropriate).

### 5. `test_agent_home` (2)

`HD Ticket.name` is an int; the fixture list holds strings. Compare
consistently — cast both sides with `str()` at the assertion.

### 6. `test_wa_api_settings` (1)

The test asserts `default_team` exists on `WA API Settings`; the doctype has no
such field. Determine whether the field was dropped deliberately (in which case
the assertion goes) or never added (in which case the test is describing intent
that was never built) — then make test and doctype agree.

### 7. `test_hd_task` (1)

`_Test Create Task Customer` survives between runs and collides on re-insert.
Make the fixture idempotent: delete-if-exists before creating, and register
cleanup.

## Ownership and merge risk

`hd_ticket.json`, `test_agent_home.py`, `test_hd_task.py` and `test_hd_ticket`
are upstream files. Editing them puts a local diff in the path of the next
`git merge frappe/develop`. Each change here is small and surgical, which keeps
those conflicts trivial, and the alternative is carrying 17 red tests forever.

## Verification

- each module green in isolation
- full suite compared against the current baseline: 28 failures should become 0,
  with test counts unchanged apart from nothing being skipped
- `bench migrate` clean after the doctype change
- Customize Form on HD Ticket opens and saves in the UI — the user-visible half
  of fix 2

## Out of scope

- The `_Test Comm Account 1` bootstrap breakage, which is frappe's own test data
  and recurs only when frappe's email tests run. Documented already; the fix is
  a delete, not a flag flip.
