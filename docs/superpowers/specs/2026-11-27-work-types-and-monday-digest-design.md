# Work types, chargeable jobs and the Monday digest — design

## Why
Support under a licence or a recurring plan is covered. Network, printer and template-change work is chargeable, but
today nobody records which job was which, so billable work slips through. The owner also has no single weekly view
of money and customer health. This adds a work type and a Charge/Waive call to every ticket at close, and one Raven
message every Monday that the owner reads and acts on.

## Goals
1. **Work type on every ticket closed by an agent.** The types are Software (covered), Network, Printer, Template
   change and Other. They are `HD Ticket Type` records, set in the existing `ticket_type` field.
2. **Charge / Waive.** For any work type other than Software, the agent must pick Charge or Waive before the ticket
   can be resolved or closed. Software never asks.
3. **The bot guesses the work type** from the conversation, so the agent usually just confirms it. It never guesses
   Charge/Waive; that is always a person's call.
4. **Monday digest on Raven**, to the owner, at 08:00 Africa/Nairobi. Every item links to its ticket or record.
   Sections, in this order:
   - **Overdue total**: KES owed past due across all customers on erp.cecypo.com.
   - **Promises due this week**: open `HD Payment Reminder` cycles in Promised whose `promise_date` falls Mon–Sun of
     this week. Shows the customer, the amount and the date.
   - **Unallocated money**: cycles that are Held with the "Unallocated payment" reason. Shows the customer and the
     advance amount.
   - **Chargeable jobs last week**: tickets resolved or closed last Mon–Sun with Charge. Shows the ticket, the
     customer, the work type and the subject. The owner reviews each one and bills it by hand; nothing is created
     on ERPNext.
   - **Tickets open > 3 days**: tickets in the Open status category (Paused excluded) whose `opening_date` is more
     than 3 days ago, excluding payment-reminder tickets (their money is covered by the sections above).
   - **Unhappy customers**: tickets closed last week that the AI flagged as frustrated, with its one-line reason.
5. **Unhappy flag.** Each Monday, before the digest is built, the LLM reads every ticket closed last week and flags
   the ones where the customer was clearly unhappy (complaints, repeated problems, threats to leave), with a short
   reason. Step 5's "Did that fix it? → no" will feed the same section later.

## Non-goals
- Creating invoices or anything else on ERPNext.
- Changing SLA or priority behaviour. Ticket type keeps its existing role there.
- Ratings or feedback forms. `feedback_rating` stays unused.
- A digest on WhatsApp or email. It goes to Raven only.

## Anti-goals
- An automatic or system close (auto-close after N days, or a reminder cycle resolving its ticket) failing because
  no work type was picked. The rule applies only to a person closing a ticket.
- The bot's guess silently becoming Charge or Waive.
- A digest that claims "KES 0 overdue" when ERPNext was unreachable. It must say it couldn't read ERPNext.
- Customer names, amounts or ticket text in commits or test fixtures.

## Constraints
- Lives in the helpdesk app (Cecypo-Tech/helpdesk, `develop`) on support.cecypo.tech. Raven is installed there
  but not on dev or test.
- Raven goes through `helpdesk/integrations/raven_bridge.py`. It currently reads the channel and user from
  `HD Payment Reminder Settings` and needs to take a channel and user from the caller.
- LLM: `helpdesk/integrations/llm.py chat()`. ERPNext: `erpnext_remote.call` as `api@helpdesk.local`, which has
  read on Sales Invoice.
- `HD Ticket Type` already has `disabled`. The existing types are Incident, Bug, Question and Unspecified, with
  Unspecified as the default.

## Approaches
1. **Reuse `ticket_type` (chosen).** The field, the list filters, the Details tab and the reports already exist. Add
   the five types and disable Incident, Bug and Question. It costs nothing in the UI.
2. **A new `work_type` field.** It leaves ticket types alone but duplicates a field that is already 99% Unspecified,
   and needs new UI. Rejected.
3. **A separate billing log DocType.** It is overkill for a weekly list the owner reads. Rejected.

## Decided shape
- **Ticket types.** A patch creates Software, Network, Printer, Template change and Other, and disables Incident, Bug
  and Question (if they exist and aren't `is_system`). Unspecified stays the default for new tickets and counts as
  "not picked".
- **New HD Ticket fields:** `charge` (Select: blank / Charge / Waive), `unhappy` (Check) and `unhappy_reason`
  (Small Text, read-only). `charge` shows in the agent's Details tab, next to ticket type, only when the type is
  set and isn't Software.
- **Close rule (HD Ticket validate).** It applies when `status_category` changes to Resolved and
  `frappe.request and is_agent() and not self.is_merged and not self.flags.skip_work_type`, and the ticket is not a
  payment-reminder ticket. (`frappe.flags.in_request` does not exist in v16. Auto-close already skips validate via
  `flags.ignore_validate`, and payment reminders run in background jobs.) Then:
  - ticket type must be one of the five, otherwise the save fails with "Pick a work type before closing";
  - if the type isn't Software, `charge` must be set, otherwise "Pick Charge or Waive before closing".
  The agent sees the error and fixes it in the Details tab; there is no new dialog.
- **Bot guess.** An hourly job finds open, non-reminder tickets that are still Unspecified, are more than an hour
  old and have at least one message. It asks the LLM for one of the five types from the subject and conversation,
  and sets it. It tries each ticket once (a `work_type_guessed` check), and bad output means no guess. It is a sweep,
  not a hook, because WhatsApp replies don't set `first_responded_on` and its status changes go through
  `db.set_value`.
- **Unhappy check.** This runs inside the Monday digest job, not on close. Every non-reminder ticket resolved or
  closed last week, auto-closed ones included, gets one LLM call returning `{"unhappy": bool, "reason": str}`; bad
  JSON means not unhappy. The result is written to `unhappy` / `unhappy_reason` on the ticket for the record. A
  sweep catches every close path, including WhatsApp's `db.set_value` and auto-close, with no hooks.
- **Advance amount.** `HD Payment Reminder` gets `advance_amount` (Currency), set when a cycle is held for unallocated
  money, so the digest can show the figure.
- **Digest.** A new Single, `HD Weekly Digest Settings`, with `enabled` (ships off), `raven_channel`, `raven_user`
  and `open_days` (3). A cron job runs Monday 08:00 and builds one markdown message. Empty sections are left out.
  An empty week still sends one line ("Quiet week — nothing to flag"), so the owner knows it ran. Links use the
  site's `/helpdesk/tickets/<name>` and the desk form URL for reminder cycles.
- **Overdue total.** One `erpnext_remote.call` that sums `outstanding_amount` on submitted Sales Invoices with
  `due_date < today` and `outstanding_amount > 0`, with no company filter (both companies are KES). If the call
  fails, that section reads "Couldn't read ERPNext".

## Calls made after the builder check (Claude's, for review)
1. Payment-reminder tickets are left out of the close rule, the guess, the unhappy check and "open > 3 days".
2. Paused tickets don't count as open.
3. The unhappy check runs weekly over last week's closes, auto-closes included (silence after a problem is signal too).
4. Old Incident, Bug and Question tickets are blocked at close, the same as Unspecified: only the five types pass.
5. Reminder polls, alerts and the digest all go to the private Raven channel #actions (`erp-support-actions`, workspace ERP-SUPPORT), created 2026-11-27. Members are added by the owner.
6. Merging a ticket is exempt from the close rule.
7. Cron `0 8 * * 1` runs in the site's time zone; support.cecypo.tech is set to Africa/Nairobi (checked).

## Left to the builder
- The overdue sum: sum the rows client-side with `limit_page_length=0` (`erpnext_remote.call` is GET-only).
- `raven_bridge` takes `channel` and `user` arguments, defaulting to the reminder settings.
- The `charge` Select sits next to `ticket_type` in `TicketDetailsTab.vue`.
- The prompts and the message layout.
