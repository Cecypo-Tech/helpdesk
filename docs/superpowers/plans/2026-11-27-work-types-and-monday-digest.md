# Work Types and Monday Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Every agent-closed ticket carries a work type plus Charge/Waive for non-Software work, and a Monday Raven
digest goes to #actions.

**Architecture:**
- Reuse `ticket_type`, adding the five types through a patch. Add `charge`, `unhappy`, `unhappy_reason` and
  `work_type_guessed` on HD Ticket, and `advance_amount` on HD Payment Reminder.
- The close rule lives in `HDTicket.validate`.
- One new module, `helpdesk/integrations/weekly_digest.py`, holds the hourly guess sweep, the Monday digest and the
  unhappy check.
- `raven_bridge` takes a channel and a user from the caller.

**Tech Stack:** Frappe v16, Raven (prod only, mocked in tests), `helpdesk/integrations/llm.py`, `erpnext_remote`.

**Spec:** `docs/superpowers/specs/2026-11-27-work-types-and-monday-digest-design.md`, including "Calls made after the
builder check".

## Global Constraints
- `WORK_TYPES = ("Software", "Network", "Printer", "Template change", "Other")`. Software is covered and never asks
  for Charge.
- The close rule only fires when
  `frappe.request and is_agent() and not self.is_merged and not self.flags.skip_work_type`, and the ticket is not a
  payment-reminder ticket.
- `HD Weekly Digest Settings`: `enabled` ships 0, `raven_channel`, `raven_user`, `open_days` 3.
- Cron `0 8 * * 1`. Empty sections are left out. An empty week sends "Quiet week — nothing to flag".
- If ERPNext is unreachable, the overdue line reads "Couldn't read ERPNext". It never shows KES 0.
- Bad LLM output means no guess, and not unhappy.
- Tests only on test.localhost. No real customer data in fixtures.

## Review Focus
1. The ticket merge flow and auto-close (`close_tickets_after_n_days`) must not be blocked by the close rule. Tests:
   `test_merge_not_blocked` and `test_auto_close_not_blocked`.
2. An agent's email reply that moves a ticket to a Resolved-category status (through `HD Settings.update_status_to`)
   hits the rule. The rule is accepted here because a person is closing; the test pins the error message so the
   behaviour is deliberate: `test_agent_close_without_type_blocked`.
3. ERPNext unreachable while the digest runs. Test: `test_overdue_unreachable_says_so`.
4. A ticket closed through WhatsApp's `db.set_value` still gets the unhappy check. Test:
   `test_unhappy_covers_db_set_value_close`.
5. The guess sweep never overwrites a type an agent set, and never touches reminder tickets. Test:
   `test_guess_skips_set_and_reminder_tickets`.

---

### Task 1: Types, fields and the close rule
**Files:**
- Create `helpdesk/patches/add_work_types.py` and register it in `patches.txt`.
- Modify `hd_ticket.json` to add `charge` (Select, options `\nCharge\nWaive`), `unhappy` (Check), `unhappy_reason`
  (Small Text, read only) and `work_type_guessed` (Check, hidden).
- Modify `hd_ticket.py` to add `validate_work_type()`, called from `validate`.
- Modify `hd_payment_reminder.json` to add `advance_amount` (Currency). In `payment_reminders.refresh_customer`, set it
  where the cycle is held for unallocated money, and clear it when the hold lifts.
- Modify `TicketDetailsTab.vue` to add a `charge` Select under ticket type, visible when the type is set, is not
  Software and is not Unspecified. It saves through the existing `handleFieldUpdate`.
- Test file: `helpdesk/tests/test_work_types.py`.

**Interfaces (produces):** `helpdesk.integrations.weekly_digest.WORK_TYPES`. The constant lives there so that the
sweep and the rule share it. `hd_ticket.py` imports it.

Tests:
- `test_patch_creates_types_and_disables_old`: the five types exist. Incident, Bug and Question are disabled. The
  patch is idempotent.
- `test_agent_close_without_type_blocked`: with `frappe.request` faked and an agent user, Unspecified → Resolved
  raises "Pick a work type before closing". Bug → Resolved raises the same.
- `test_agent_close_non_software_needs_charge`: Network with no charge raises "Pick Charge or Waive before closing";
  with Charge set, the close passes.
- `test_software_close_needs_no_charge`
- `test_background_close_not_blocked`: no request, so the close passes.
- `test_merge_not_blocked`: run through `hd_ticket.api` merge with an agent and a faked request.
- `test_auto_close_not_blocked`: `close_tickets_after_n_days` closes an Unspecified ticket.
- `test_reminder_ticket_not_blocked`
- `test_advance_amount_set_on_hold`: extends the existing payment-reminder hold path.

- [ ] Write the tests and watch them fail. Implement. Watch them pass. Run `bench build --app helpdesk`. Commit.

### Task 2: Guess sweep, Raven channel, digest and unhappy check
**Files:**
- Create `helpdesk/integrations/weekly_digest.py`.
- Create a new Single, `helpdesk/helpdesk/doctype/hd_weekly_digest_settings/`.
- Modify `raven_bridge.py` so that `post(text, channel=None, user=None)`. `_as_raven_user` takes the channel and user,
  defaulting to `HD Payment Reminder Settings`.
- Modify `hooks.py`: add `"0 8 * * 1": ["helpdesk.integrations.weekly_digest.send"]` and add
  `weekly_digest.guess_work_types` to `hourly`.
- Test file: `helpdesk/tests/test_weekly_digest.py`.

**Interfaces (produces):**
- `guess_work_types(now=None) -> int`: the number of tickets typed. It covers open, non-reminder, Unspecified tickets
  older than 1 hour with `work_type_guessed = 0`, at most 20 per run. It sets `work_type_guessed = 1` whatever the
  outcome, and saves with `flags.skip_work_type`.
- `parse_work_type(text) -> str | None`: an exact match from `WORK_TYPES`, case-insensitive, otherwise None.
- `parse_unhappy(text) -> tuple[bool, str]`: bad JSON gives `(False, "")`.
- `check_unhappy(start, end) -> list[str]`: tickets whose `status_category` is Resolved, excluding reminder tickets,
  with `resolution_date` (or `modified` when it's missing) in `[start, end)`. Each gets one LLM call, and the result
  is written with `db.set_value`. It returns the flagged names.
- `overdue_total() -> float | None`: one `erpnext_remote.call` on `frappe.client.get_list` for Sales Invoice with
  `docstatus = 1`, `outstanding_amount > 0`, `due_date < today` and `limit_page_length = 0`, summed. None on any
  error.
- `build(now) -> str`: the markdown. Sections in spec order. Links use `{frappe.utils.get_url()}/helpdesk/tickets/<n>`
  and `/app/hd-payment-reminder/<n>`.
- `send(now=None) -> None`: does nothing unless enabled with a channel set. Calls `check_unhappy` for last Mon–Sun,
  then `build`, then `raven_bridge.post(text, channel, user)`.

Tests (LLM, `erpnext_remote.call` and `raven_bridge` mocked):
- `test_parse_work_type` and `test_parse_unhappy_bad_json`
- `test_guess_sets_type_once`
- `test_guess_skips_set_and_reminder_tickets`
- `test_unhappy_flags_and_writes_reason`
- `test_unhappy_covers_db_set_value_close`
- `test_digest_sections`: one fixture per section, each appearing with its link, plus a Waive job that does not
  appear.
- `test_digest_excludes_paused_and_reminder_tickets_from_open`
- `test_overdue_unreachable_says_so`
- `test_quiet_week_line`
- `test_send_disabled_does_nothing`
- `test_raven_post_uses_given_channel`

- [ ] Write the tests and watch them fail. Implement. Watch them pass. Commit.

### Task 3: Land
- [ ] Run the full helpdesk suite on test.localhost. Its failures must match develop's known set: 72 WhatsApp
  Account, 7 `throw`, 1 wa_unread_state race. The payment-reminder suite must still pass 52/52.
- [ ] Check the browser on dev: the Charge select shows for Network and hides for Software, and closing without a
  type shows the error toast. Use a throwaway QA agent and delete it afterwards.
- [ ] Do a fresh review on the most capable model, then one fix pass. Merge to develop and push.
