# Investigation: WABA customer/contact link lost when a customer replies after resolution

Date: 2026-08-05
Integration: WABA (frappe_whatsapp). The WA Line / Evolution path is unaffected.
Status: root cause confirmed by reproduction. No fix applied yet.

## Symptom

"Customer name linked to the WABA contacts keeps disappearing when the customer
replies back after the ticket has been resolved or closed."

## Why it only shows up after resolve/close

`on_whatsapp_message_insert()` (`helpdesk/integrations/wa.py`) routes an inbound
message to an existing ticket only when that ticket is **not** resolved:

```python
status_category = frappe.db.get_value("HD Ticket", candidate, "status_category")
if status_category and status_category != "Resolved":
    ...
    existing_ticket = candidate
```

`status_category == "Resolved"` covers both Resolved and Closed. So while the
ticket is open, replies attach to it and the existing contact/customer linkage
holds. Once it is resolved, the next reply creates a **new** ticket, and the
contact has to be re-derived from scratch. That is the only moment the weak
derivation runs — which is why it looks like the name "keeps disappearing"
rather than never working.

## Root cause

The new-ticket path derives the contact **solely from the phone number**:

```python
contact_name = match_phone_to_contact(phone)
```

and ignores the contact/customer already established on the previous ticket for
the same conversation — even though that ticket has just been located by the
`linked` query a few lines above.

When `match_phone_to_contact()` returns None and `unknown_contact_action` is
`Create Contact and Ticket`, the automation creates a **brand-new Contact** from
the WhatsApp profile name, with no `HD Customer` Dynamic Link. `HD Ticket.set_customer()`
then finds nothing to link, so `customer` stays empty.

### Why matching fails

`_normalize_phone()` is digits-only:

```python
def _normalize_phone(number: str) -> str:
    return re.sub(r"[^\d]", "", number or "")
```

WhatsApp sends the full international number (`254799123456`). A Contact whose
number is stored in local format (`0799123456`) normalizes to a different string,
so the two never match. Any locally-formatted number breaks matching permanently.

## Reproduction

Script: `/tmp/repro_wa_customer.py` (control) and `/tmp/repro_wa_customer2.py`
(failing case). Both build Contact → HD Customer link → ticket → resolve →
inbound reply, then inspect the resulting ticket, and clean up after themselves.

**Control — number stored international (`254799123456`):**

```
match_phone_to_contact: Jane Repro-Repro Customer Ltd
ticket 1: contact=Jane Repro-...  customer=Repro Customer Ltd
after reply: routed to NEW ticket 1665
  ticket.contact  = Jane Repro-Repro Customer Ltd
  ticket.customer = Repro Customer Ltd
  Contacts: 415 -> 415
VERDICT: customer preserved
```

**Failing — same person, number stored local (`0799123456`):**

```
match_phone_to_contact: None
ticket 1: contact=Jane Local-...  customer=Repro Customer Two
after reply: routed to NEW ticket 1667
  ticket.contact  = Jane W                 <- freshly invented from the WA profile name
  ticket.customer = None                   <- lost
  ticket.raised_by = whatsapp+254799123456@whatsapp.placeholder.local
VERDICT: REPRODUCED (customer lost)
```

The only difference between the two runs is the stored phone format.

## Two distinct defects

1. **The new-ticket path throws away known-good linkage.** The previous ticket
   for the same `from` number is already in hand from the `linked` query. Its
   `contact` and `customer` are authoritative — an agent may have set them by
   hand — and are discarded in favour of a phone lookup that can fail.

2. **Phone matching is format-sensitive.** Digits-only comparison cannot match
   national against international format. This also means a duplicate Contact is
   created on *every* such conversation, not only after resolution — so the
   contact list accumulates junk like "Jane W" alongside the real record.

Defect 1 is the reason the symptom is visible; defect 2 is the reason matching
fails in the first place. Fixing only 2 would still leave the linkage fragile for
any other matching failure (no phone on the contact, contact linked manually,
several contacts sharing a number).

## Proposed fix (not yet implemented)

**A. Inherit from the previous ticket.** When creating the follow-up ticket,
carry over `contact` (and `customer`) from the previous ticket found for that
number, preferring it over a phone lookup. Only fall back to
`match_phone_to_contact()` / contact creation when there is no previous ticket.

**B. Make matching tolerant of national vs international format.** Treat two
numbers as the same when one's digits are a suffix of the other's and the
overlap is at least 9 digits. Require the match to be **unique** — if more than
one Contact matches, return None rather than guess, so we never attach a
conversation to the wrong customer.

Both are needed: A fixes the visible symptom regardless of why matching failed,
B stops the duplicate-contact accumulation at source.

## Open question for the user

Whether the follow-up reply should keep creating a **new** ticket at all, or
reopen the resolved one. This investigation assumes the current behaviour (new
ticket) is intended and only fixes the lost linkage. Changing it is a separate
product decision.
