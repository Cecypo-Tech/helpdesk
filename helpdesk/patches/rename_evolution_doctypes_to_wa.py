import frappe


def execute():
	"""Rename Evolution API Settings → WA API Settings and Evolution Line → WA Line in the database."""
	if frappe.db.exists("DocType", "Evolution API Settings") and not frappe.db.exists("DocType", "WA API Settings"):
		frappe.rename_doc("DocType", "Evolution API Settings", "WA API Settings", force=True)

	if frappe.db.exists("DocType", "Evolution Line"):
		if not frappe.db.exists("DocType", "WA Line"):
			frappe.rename_doc("DocType", "Evolution Line", "WA Line", force=True)
		else:
			# WA Line already exists (module files already renamed); purge the stale Evolution Line record
			frappe.db.sql("DELETE FROM `tabDocType` WHERE `name` = 'Evolution Line'")

	# line_doctype column doesn't exist in tabBaileys Message; nothing to update here
	frappe.db.commit()
