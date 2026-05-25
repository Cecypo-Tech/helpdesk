import frappe


def execute():
	"""Ensure all WA DocTypes are registered in tabDocType.

	Previous rename patches may have renamed physical tables without registering
	the new DocType names in tabDocType (e.g. when the source DocType was already
	absent).  This patch is idempotent and safe to run multiple times.
	"""
	for doctype_dir in [
		"wa_api_settings",
		"wa_line",
		"wa_message",
		"wa_message_edit_history",
	]:
		try:
			frappe.reload_doc("Helpdesk", "doctype", doctype_dir, force=True)
		except Exception as e:
			frappe.log_error(f"reload_all_wa_doctypes: could not reload {doctype_dir}: {e}")
	frappe.db.commit()
