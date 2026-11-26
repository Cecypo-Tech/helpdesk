# Ticket customer: stop asking for a PIN, link contacts from the ticket

## Why
The PIN request annoyed customers. We usually know who the customer is, so the
agent should fill it in once and helpdesk should remember it.

## Goals
- No PIN request is sent to customers. Done 2026-11-26: `verification_enabled = 0`
  on support.cecypo.tech. The code stays in place but does nothing.
- When an agent sets `customer` on a ticket whose contact has no HD Customer link,
  link the contact to that customer. Every later ticket from that number then
  fills itself through `HDTicket.set_customer`.
- The Customer field in the agent ticket sidebar gets a red border while it is empty.

## Non-goals
- Deleting the verification code, its fields or the approval screen. Do that later if asked.
- Changing the customer on contacts that are already linked. If a contact already has
  one or more customer links, we leave it alone, because an office handset can serve
  several customers.

## Approach
Hook `HD Ticket.on_update`: if the `customer` value changed and is now set, and the
contact has no HD Customer link, call the existing
`helpdesk.api.verification.approve_contact_link` logic. It is idempotent and also
backfills the customer on that contact's other open tickets. Reuse it rather than
writing a second linker. Alternative considered: linking only from the WhatsApp page.
Rejected because email tickets have the same gap.

Red border: in `TicketDetailsTab.vue`, add a class to the customer field when
`ticket.doc.customer` is empty. Use a Tailwind `border-red-500` style on the wrapper.
No new component.

## Acceptance
- Test: a ticket from an unlinked contact, with customer set by an agent, leaves the
  contact linked, and a new ticket from that contact gets the customer.
- Test: a contact that already has a link keeps its links unchanged.
- Browser check on dev: an empty customer field shows a red border, and it goes away
  once the field is filled.
