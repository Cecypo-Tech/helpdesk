# Payment Reminders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Chase overdue invoices for opted-in billing contacts on WhatsApp Business, with an owner approval poll on
Raven for every send, reply classification, and an automatic thank-you when paid.

**Architecture:** There is one DocType for cycles (`HD Payment Reminder`), plus a Single for settings. One module,
`helpdesk/integrations/payment_reminders.py`, holds pure decision functions plus a single hourly `run()` that is
idempotent. A small `raven_bridge.py` wraps Raven. Replies hook into `wa_ingest`, and the support bot skips reminder
tickets.

**Tech Stack:** Frappe v16, frappe_whatsapp (WABA), Raven (on prod only), `helpdesk/integrations/llm.py`.

**Spec:** `docs/superpowers/specs/2026-11-26-payment-reminders-design.md`, including "Calls made after the builder check".

## Global Constraints
- Settings `enabled` ships **off**; `auto_send` ships **off**. Defaults: `min_invoice_age_days` 7, `min_gap_days` 2,
  `followup_days` 7, `max_unanswered` 3.
- Reminders and thank-yous only go out Mon–Sat, 08:00–18:00 site time. Acknowledgements go out at any time.
- Before every send, re-check ERPNext. An empty or unreachable snapshot means do nothing; it is never "paid".
- Candidates are only HD Customers that have a Contact with `is_billing_contact = 1` and a set `erpnext_customer`.
- Raven calls go only through `raven_bridge`. Tests mock it, because Raven is not installed on test.localhost.

## Review Focus
1. A customer who paid between the poll and the vote must not be messaged. Test:
   `test_paid_after_poll_sends_thanks_not_reminder`.
2. A reply arriving on Sunday night still gets an acknowledgement. Test: `test_ack_ignores_business_hours`.
3. A reply from a number with a live reminder ticket lands on that ticket, not on an older open support ticket. Test:
   `test_reply_routes_to_reminder_ticket`.
4. ERPNext being unreachable must not close cycles or send thanks. Test: `test_unavailable_snapshot_changes_nothing`.
5. The support bot stays silent on reminder tickets. Test: `test_bot_skips_reminder_ticket`.

---

### Task 1: DocTypes and pure decisions
**Files:**
- Create `helpdesk/helpdesk/doctype/hd_payment_reminder/` and `helpdesk/helpdesk/doctype/hd_payment_reminder_settings/`.
- Create `helpdesk/integrations/payment_reminders.py` (the pure part).
- Create `helpdesk/tests/test_payment_reminders.py`.

**Interfaces (produces):**
- `qualifying(invoices: list[dict], today: date, min_age: int) -> list[dict]`: rows with `outstanding_amount > 0`,
  `due_date < today`, and `posting_date <= today - min_age`.
- `next_action(cycle: dict, today: date, s) -> str`, which returns one of `"wait"`, `"due"` or `"escalate"`:
  - Rules: no sends yet → due. Promised → due once `today >= promise_date + 2`. Otherwise due once
    `days_since(last_sent) >= followup_days`.
  - The gap must always be `>= min_gap_days`.
  - `"escalate"` when `unanswered >= max_unanswered`.
- `in_business_hours(now: datetime) -> bool`: Mon–Sat, 08:00 <= hour < 18:00.
- `parse_classification(text: str, today: date) -> dict`, returning `{"class": promise|paid|dispute_or_question|other,
  "date": date|None}`. Bad JSON, or a promise with no valid date, gives `other`.

- [ ] Write the tests for each function, including edge cases: the gap, the 7-day age, Sunday, a past promise date
  and bad JSON. Run them and see them fail. Implement. Run them and see them pass. Commit.

### Task 2: Cycle building, sending and polls
**Interfaces:**
- `run(now=None) -> None`: the hourly entry point.
- `refresh_customer(hd_customer: str, today: date) -> str | None`: the cycle name.
- `send_reminder(cycle_name: str) -> bool`
- `send_thanks(cycle_name: str) -> None`
- `raven_bridge.create_poll(question: str) -> tuple[str, str]` returns `(poll_id, message_id)`;
  `raven_bridge.votes(message_id: str) -> dict[str, list[str]]`; `raven_bridge.close_poll(poll_id: str) -> None`;
  `raven_bridge.post(text: str) -> None`.

Tests, with `erpnext_remote.call`, `raven_bridge` and the WhatsApp send mocked:
- `test_no_billing_contact_no_cycle`
- `test_advances_hold_cycle`
- `test_poll_then_send_on_approval`
- `test_skip_vote_does_not_send`
- `test_paid_after_poll_sends_thanks_not_reminder`
- `test_unavailable_snapshot_changes_nothing`
- `test_escalates_after_max_unanswered`
- `test_auto_send_skips_poll`
- `test_outside_hours_does_not_send`

- [ ] Run the tests and see them fail. Implement. Run them and see them pass. Commit.

### Task 3: Replies, routing and the bot
**Files:**
- Modify `wa_ingest.process_incoming_message`: call `payment_reminders.on_incoming(doc)`.
- Modify `wa.link_incoming_message`: prefer the reminder ticket.
- Modify `bot.handle_whatsapp_message`: skip reminder tickets.

**Interfaces:**
- `on_incoming(doc) -> None` enqueues `process_reply(msg_name)` on `default`.
- `process_reply(msg_name: str) -> None`
- `live_ticket_for_phone(phone: str, after) -> str | None`
- `is_reminder_ticket(ticket) -> bool`

Tests (LLM mocked):
- `test_promise_reply_records_date_and_acks`
- `test_paid_reply_confirmed_thanks_and_closes`
- `test_paid_reply_unconfirmed_goes_to_team`
- `test_dispute_goes_to_team_no_reply`
- `test_ack_ignores_business_hours`
- `test_reply_routes_to_reminder_ticket`
- `test_bot_skips_reminder_ticket`
- `test_resolved_with_team_ticket_closes_cycle`

- [ ] Run the tests and see them fail. Implement. Run them and see them pass. Commit.

### Task 4: The Contact tab toggle and the scheduler hook
- [ ] `hooks.py`: add `payment_reminders.run` to `hourly`.
- [ ] `TicketContactTab.vue`: an "Is Billing Contact" checkbox, saved with `frappe.client.set_value` on Contact.
  Build it, then check it in the browser on dev.
- [ ] Run the full helpdesk suite. Its failures must match develop's known failures (WhatsApp Account and
  frappe_whatsapp `throw`). Then do a fresh review, merge, and push.
