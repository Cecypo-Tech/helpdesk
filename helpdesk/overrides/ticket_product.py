"""Stamp HD Ticket.support_status when a product first becomes known."""

import frappe

from helpdesk import entitlement


def stamp_support_status(doc, method=None):
	"""Record coverage once, the first time we know what the ticket is about.

	Runs on before_save, which Frappe calls for both inserts and updates, so a
	single hook covers two cases: a ticket created with a product already set
	(portal and email flows), and a WhatsApp ticket created automatically on the
	first inbound message and tagged with a product minutes or days later.

	Stamping only at creation would leave support_status permanently Unknown on
	the WhatsApp channel, which is exactly where it matters most.

	Once set the value is frozen. It records what coverage WAS when the customer
	asked — the figure that matters for renewal conversations and for measuring
	absorbed out-of-contract support. Recomputing it would silently rewrite
	history the moment somebody renewed.

	Advisory only: this never blocks a save. An unentitled product is a recorded
	outcome, not an error.
	"""
	try:
		if doc.get("support_status"):
			return  # frozen
		product = doc.get("hd_product")
		if not product:
			return  # nothing to stamp yet
		doc.support_status = entitlement.compute_support_status(
			doc.get("customer"), product
		)
	except Exception:
		# A badge is never worth failing a ticket save over.
		frappe.log_error(
			frappe.get_traceback(), "Helpdesk: support_status stamping failed"
		)
