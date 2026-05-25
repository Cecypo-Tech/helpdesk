import frappe


def execute():
	"""Ensure WA Message DocTypes are registered in tabDocType after the Baileys rename."""
	frappe.reload_doc("Helpdesk", "doctype", "wa_message_edit_history", force=True)
	frappe.reload_doc("Helpdesk", "doctype", "wa_message", force=True)
	frappe.db.commit()
