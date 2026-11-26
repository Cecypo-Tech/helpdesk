# Payment reminders over WhatsApp — design

## Why
Cecypo's customers pay late, and chasing them by hand takes staff time and is easy to forget. The owner wants overdue invoices
chased politely and consistently on WhatsApp, the channel customers actually answer. Promises like "nitalipa kesho"
("I'll pay tomorrow") should be followed up, and payments should get a thank-you. The owner wants to approve every
reminder for the first month or two, until they trust it.

## Goals
- Each day, find customers whose invoices are **past due AND at least 7 days old** (posting_date <= today - 7), on
  erp.cecypo.com. This covers cash-sale customers ("current" terms, due the same day) and never chases a credit customer
  before the due date.
- Send a reminder only to helpdesk Contacts with **Is Billing Contact** ticked (the core `Contact.is_billing_contact`
  field, on the support site). As of 2026-11-26 only 1 of 3,847 helpdesk contacts has it ticked, so it is effectively
  an opt-in. If no contact is ticked, the reminder is held and the approval message says "no billing contact".
- **Every outbound reminder needs the owner's approval** through a Raven poll (👍 send / 👎 skip), one poll per reminder,
  posted on the support site's Raven. With no vote, the reminder is not sent; it comes up again the next eligible day.
  A setting `auto_send` (default off) skips the poll later, once the owner turns it on.
- One message per customer covers all of that customer's qualifying invoices: count, total and oldest due date. Never
  send one message per invoice.
- Cadence per customer:
  - At least 2 days between any two reminders.
  - After the first reminder, if the customer doesn't reply, follow up after 7 days, then weekly.
  - If the customer promised a date: send nothing until promise_date + 2 days, then one nudge.
  - After 3 reminders with no reply: stop and alert the owner on Raven to call them.
  - Only send Monday to Saturday, 08:00–18:00 Africa/Nairobi. Never on Sunday.
- Replies: classify the customer's reply with the existing LLM client (`helpdesk/integrations/llm.py chat()`) into
  `promise` (with a date), `paid`, `dispute_or_question`, or `other`.
  - For `promise` and `paid`: record the result and send a short acknowledgement automatically ("Thank you, noted for
    Friday 🙏"). The customer has just written, so the 24-hour window is open and this can be free-form.
  - For `dispute_or_question` and `other`: hand to the team. The ticket stays open in the normal queue. No auto-reply.
  - The existing support bot (`helpdesk/integrations/bot.py`) must not answer messages on a reminder conversation.
- "Paid": when every invoice in the cycle has outstanding 0 on ERPNext, send a thank-you **automatically** (no poll)
  and close the cycle. Use free-form if the 24-hour window is open, otherwise the thank-you template.
  - If the customer says "paid" but ERPNext still shows the money owing: acknowledge ("Thank you, we'll confirm
    shortly"), then flag the ticket for the team to check. Never claim the payment has been applied.

## Non-goals
- Email or SMS reminders. Statements or PDF attachments (maybe later).
- Customers on other ERPNext sites; this is only Cecypo's own customers on erp.cecypo.com.
- Collecting payments or handling M-Pesa in the conversation.
- The weekly Monday digest; that is a separate project.

## Anti-goals (failure even if it "works")
- Messaging a customer who has already paid. Re-check ERPNext right before every send.
- Sending from the Evolution / WA Line number. Only use WABA (Meta Cloud API via `frappe_whatsapp`), because bulk
  outbound messages from a linked device risk getting the number banned.
- More than one message in 2 days to the same customer, for any reason except the acknowledgement or thank-you that
  answers their own message.
- Quoting financial figures to a number that isn't a ticked billing contact. A WhatsApp number can be a shared office
  handset.

## Constraints
- Lives in the helpdesk app (Cecypo-Tech/helpdesk, branch `develop`) on support.cecypo.tech (Frappe Cloud). Its
  scheduler runs there.
- ERP data comes through the existing connector `helpdesk.integrations.erpnext_remote.call`:
  - `helpdesk_customer_standing` (an ERPNext Server Script) gives overdue totals per customer.
  - `cecypo_powerpack.api.get_customer_snapshot` gives per-invoice rows: `name`, `posting_date`, `due_date`,
    `grand_total`, `paid`, `outstanding_amount`, `days_overdue`.
  - The API user `api@helpdesk.local` has the read-only role "Helpdesk Helper" for Sales Invoice.
  - The HD Customer ↔ ERP customer mapping is `HD Customer.erpnext_customer`.
- Outbound template sends go through the existing `helpdesk.integrations.wa.send_template_to_ticket`. Free-form sends
  inside the window use `_send_fw_reply(ticket, text, system=True)`. The `system=True` flag keeps the ticket's status
  and assignment unchanged.
- Meta templates need approval before use. Three templates, using {{n}} variables and filled from fields of the
  reminder record:
  - `payment_reminder`: name, invoice count, total, oldest due date.
  - `payment_promise_nudge`: name, promised date, total.
  - `payment_thank_you`: name.
  The owner approves the wording before it is submitted to Meta.
- Inbound routing: `wa_ingest.process_incoming_message` → `wa.link_incoming_message` → `bot.handle_whatsapp_message`.
  Reply classification hooks in at the same place.

## Approaches considered
1. **Inside helpdesk (chosen).** WhatsApp, contacts, tickets, Raven, the LLM client and the ERP connector are all
   already there.
2. **On erp.cecypo.com.** Rejected: it has no WhatsApp Business integration (`frappe_whatsapp` is not installed there),
   and replies arrive in helpdesk anyway.
3. **An external agent such as MausBot.** Rejected: it has no WhatsApp channel, and it sits outside the data.

## Decided shape
- A new DocType **Payment Reminder**, one row per cycle per customer, with these fields:
  - `hd_customer`, `erp_customer`, `contact`, `ticket`
  - `status`: Draft / Awaiting Approval / Scheduled / Sent / Promised / Paid / Escalated / Held / Closed
  - `invoices` (JSON snapshot), `total`, `oldest_due`, `reminders_sent`, `last_sent_on`, `promise_date`,
    `last_reply_class`, `raven_message`
- Each cycle has one **HD Ticket**: subject "Payment reminder – {customer}", the billing contact, and a check field
  `hd_payment_reminder` pointing to the reminder. Template sends and replies live on that ticket, so the team sees the
  whole conversation in the normal UI.
- A **daily job** at 08:00: build or refresh cycles, apply cadence, post polls or (with `auto_send`) send. An **hourly
  job** in business hours: send approved reminders and detect payments.
- A **Payment Reminder Settings** single: `enabled`, `auto_send`, `min_invoice_age_days` (7), `min_gap_days` (2),
  `followup_days` (7), `max_unanswered` (3), the three template names, the Raven channel for polls and alerts, and
  the owner's Raven user to alert.
- The **Contact tab** in the agent UI gets an "Is Billing Contact" toggle.

## Left to the builder
- How to read Raven poll votes. Reuse the pattern in `cecypo_erp_agent` (it posts polls in `#erp-approvals` and reads
  votes). Here Raven is local to the support site, so no API call is needed.
- The exact prompt for the reply classifier, and how to parse relative dates ("kesho" = tomorrow, "Friday", "end
  month") into a concrete date. Ask for JSON back. If parsing fails, classify as `other`.
- Making sure inbound replies land on the reminder ticket rather than another open ticket for the same contact.

## Calls made after the builder check (the owner said "build it"; these are Claude's calls, listed for review)
1. **Replies and the schedule.**
   - Any customer reply resets the unanswered count.
   - `dispute_or_question`, `other`, and a "paid" claim that ERPNext doesn't confirm all set status **With Team**,
     which pauses all reminders. The cycle closes when an agent resolves the ticket. A new cycle can start
     `followup_days` after the last send.
   - A promise nudge counts as a reminder. An unanswered nudge goes back to weekly follow-ups.
   - After `max_unanswered` reminders in a row with no reply, the status becomes **Escalated**: no more sends, and the
     owner is alerted. It closes like With Team.
2. **Cycle life.** One open cycle per HD Customer.
   - Newly qualifying invoices join the open cycle; figures are refreshed before every poll and send.
   - "Paid" means no qualifying invoice still has money owing, so a partial payment keeps the cycle going.
   - 👎 means "skip today".
3. **Unallocated money.** If the ERP snapshot shows `advances_total > 0`, the cycle is **Held** ("unallocated payment –
   check before chasing") and never sent. An empty snapshot (`{}`, the PowerPack switch is off) or an unreachable
   ERPNext means do nothing today. It is never read as "paid".
4. **Several billing contacts.** Send to one: the ticked contact whose number most recently messaged us; otherwise the
   first one by name. One contact and one ticket per cycle.
5. **Send gates.**
   - Acknowledgements answer the customer's own message, so they go out at once, at any hour.
   - Thank-yous and reminders keep to Mon–Sat 08:00–18:00.
   - Thank-yous ignore the 2-day gap.
6. **Ticket.** Created at the first send, not at draft, so held and skipped cycles make no tickets.
   - Raised with status Replied (waiting on the customer), so it stays out of the Open queue until the customer
     writes.
   - Resolved when the cycle is paid.
   - Inbound routing: a reply from a number goes to its live reminder ticket when a reminder was sent to that number
     after its last inbound message.
7. **Companies.** erp.cecypo.com has two companies (Cecypo Limited and Cecypo International Limited), both in KES.
   Each customer gets one combined message; the snapshot is taken with no company filter.
8. **Raven.** Raven is installed on support.cecypo.tech but not on the dev or test sites. It is wrapped in a small
   module that calls `raven.api.raven_poll.create_poll` / `get_poll` / `close_poll` in-process, using the same calls
   as `cecypo_erp_agent`. Tests mock it. A first live check after deploy is required.
9. **Templates.** Only their names live in Settings; the wording is drafted for the owner to approve on Meta. Nothing
   sends until the templates exist and Settings is enabled, and `enabled` ships off.
