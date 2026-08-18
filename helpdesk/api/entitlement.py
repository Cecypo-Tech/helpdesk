"""Agent-facing entitlement payload for the ticket sidebar."""

import frappe

from helpdesk import entitlement
from helpdesk.utils import agent_only


@frappe.whitelist()
@agent_only
def get_ticket_entitlement(ticket: str | int) -> dict:
	"""Live coverage for a ticket, plus everything the customer holds.

	Live on purpose. HD Ticket.support_status is a frozen record of coverage
	when the ticket was raised; this answers the different question of whether
	the customer is covered right now. Both are useful and neither replaces the
	other.

	HD Ticket uses autoincrement naming, so `name` is a Python int, not a str
	(only turned into a string once it crosses HTTP as a query/body param).
	`@frappe.whitelist()` runs pydantic validation on every call, direct or
	over HTTP, and does not coerce int -> str, so the annotation accepts both.

	The hd_product read is wrapped in try/except because it is a Custom Field:
	on a site that has not migrated since the fixtures landed the column does
	not exist and Frappe raises OperationalError. Same hazard as baileys_jid.
	"""
	empty = {
		"customer": None,
		"product": None,
		"status": entitlement.STATUS_UNKNOWN,
		"stamped_status": None,
		"support_expiry": None,
		"entitlements": [],
	}
	if not ticket:
		return empty

	try:
		row = frappe.db.get_value(
			"HD Ticket", ticket, ["customer", "hd_product", "support_status"], as_dict=True
		)
	except Exception:
		return empty

	if not row:
		return empty

	product = entitlement.resolve_product_for_ticket(ticket)
	current = entitlement.get_entitlement(row.get("customer"), product)

	# Every row carries its own status so the side panel can render the whole
	# company's coverage. Derived here rather than in the Vue component: doing
	# it client-side would put coverage logic in a second language, which is
	# exactly the drift helpdesk/entitlement.py exists to prevent.
	entitlements = entitlement.get_entitlements(row.get("customer"))
	for held in entitlements:
		held["status"] = (
			entitlement.STATUS_EXPIRED if held["expired"] else entitlement.STATUS_COVERED
		)

	return {
		"customer": row.get("customer"),
		"product": product,
		"status": entitlement.compute_support_status(row.get("customer"), product),
		"stamped_status": row.get("support_status") or None,
		"support_expiry": current.get("support_expiry") if current else None,
		"entitlements": entitlements,
	}
