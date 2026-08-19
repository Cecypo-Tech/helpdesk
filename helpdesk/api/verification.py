"""Agent-facing actions for an unrecognised contact.

Approving is a human decision by design. A KRA PIN is printed on every invoice
and ETR receipt, so a match is evidence, not authorisation — and a wrong link
lets a stranger read another company's support history and account standing.
"""

import frappe

from helpdesk import verification
from helpdesk.integrations.wa_verification import REJECTED, VERIFIED, contact_state
from helpdesk.utils import agent_only


@frappe.whitelist()
@agent_only
def get_contact_claim(ticket: str | int) -> dict:
	"""What this ticket's contact claims, and who it could be.

	Never links anyone: this only reads the claim and its candidate matches.
	Linking happens exclusively through approve_contact_link, on an agent's
	explicit call.

	HD Ticket uses autoincrement naming, so `name` is an int in Python and a str
	over HTTP; the annotation accepts both, as in api/entitlement.py.
	"""
	empty = {
		"contact": None, "status": None, "claimed_company": None,
		"claimed_tax_id": None, "matches": [],
	}

	contact = frappe.db.get_value("HD Ticket", ticket, "contact")
	if not contact:
		return empty

	state = contact_state(contact)
	if not state:
		return empty

	claimed = state.get("hd_claimed_tax_id")
	matches = []
	for name in verification.match_claim(claimed):
		matches.append({
			"name": name,
			"customer_name": _customer_name(name),
		})

	return {
		"contact": contact,
		"status": state.get("hd_verification_status"),
		"claimed_company": state.get("hd_claimed_company"),
		"claimed_tax_id": claimed,
		"matches": matches,
	}


def _customer_name(customer: str) -> str:
	# No missing-column guard here: customer_name is a core field in
	# hd_customer.json and the doctype's autoname (field:customer_name), so it
	# cannot be absent while HD Customer exists. The guard the Custom Field
	# reads need would be inert here, and inert defensive code gets copied
	# somewhere it hides a real error.
	return frappe.db.get_value("HD Customer", customer, "customer_name") or customer


@frappe.whitelist()
@agent_only
def approve_contact_link(contact: str, customer: str) -> dict:
	"""Link the contact to the customer an agent chose.

	Writes the Dynamic Link shape helpdesk/utils.py:91 queries, so every future
	ticket from this contact resolves a customer through the existing path.
	Idempotent: calling this twice for the same pair does not append a second
	link row.
	"""
	if not frappe.db.exists("HD Customer", customer):
		frappe.throw(frappe._("Customer {0} does not exist.").format(customer))

	doc = frappe.get_doc("Contact", contact)
	already = [
		link.link_name for link in doc.get("links", []) if link.link_doctype == "HD Customer"
	]
	if customer not in already:
		doc.append("links", {"link_doctype": "HD Customer", "link_name": customer})
		doc.save(ignore_permissions=True)

	_backfill_ticket_customer(contact, customer)

	frappe.db.set_value("Contact", contact, "hd_verification_status", VERIFIED)
	frappe.db.commit()
	return {"ok": True, "customer": customer}


def _backfill_ticket_customer(contact: str, customer: str) -> None:
	"""Stamp the customer onto this contact's tickets that have none.

	HD Ticket.customer is populated by set_customer() on *save* (hd_ticket.py),
	and get_ticket_entitlement / get_ticket_standing read HD Ticket.customer
	rather than the contact. Without this, the ticket the agent is looking at
	when they click Link shows nothing at all until something happens to re-save
	it — the payoff moment of the whole feature reads as "nothing happened".

	Only ever fills a blank. A ticket already attributed to some other customer
	is somebody's deliberate call and is left exactly as it is.
	"""
	for name in frappe.get_all(
		"HD Ticket",
		filters={"contact": contact, "customer": ["in", [None, ""]]},
		pluck="name",
	):
		frappe.db.set_value("HD Ticket", name, "customer", customer)


@frappe.whitelist()
@agent_only
def reject_contact_claim(contact: str) -> dict:
	"""Dismiss the claim. Stops the automated asking; creates no link."""
	frappe.db.set_value("Contact", contact, "hd_verification_status", REJECTED)
	frappe.db.commit()
	return {"ok": True}
