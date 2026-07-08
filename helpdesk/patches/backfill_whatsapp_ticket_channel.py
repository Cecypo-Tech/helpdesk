"""Fix HD Tickets that were auto-created from a WhatsApp Business (frappe_whatsapp)
message before on_whatsapp_message_insert() started setting ticket_channel.
Without it, HD Ticket.before_save() defaulted them to "Email"."""

import frappe


def execute():
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return

	tickets = frappe.db.sql(
		"""
		SELECT DISTINCT wm.reference_name AS ticket
		FROM `tabWhatsApp Message` wm
		INNER JOIN `tabHD Ticket` t ON t.name = wm.reference_name
		WHERE wm.reference_doctype = 'HD Ticket'
		  AND t.ticket_channel != 'WhatsApp'
		""",
		as_dict=True,
	)
	for row in tickets:
		frappe.db.set_value("HD Ticket", row.ticket, "ticket_channel", "WhatsApp", update_modified=False)
