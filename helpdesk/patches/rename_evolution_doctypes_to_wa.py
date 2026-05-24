import frappe


def execute():
	"""Rename Evolution API Settings → WA API Settings and Evolution Line → WA Line in the database."""
	if frappe.db.exists("DocType", "Evolution API Settings") and not frappe.db.exists("DocType", "WA API Settings"):
		frappe.rename_doc("DocType", "Evolution API Settings", "WA API Settings", force=True)

	if frappe.db.exists("DocType", "Evolution Line") and not frappe.db.exists("DocType", "WA Line"):
		frappe.rename_doc("DocType", "Evolution Line", "WA Line", force=True)

	# Update Link field values in Baileys Message that reference "Evolution Line"
	frappe.db.sql(
		"UPDATE `tabBaileys Message` SET `line_doctype` = 'WA Line' WHERE `line_doctype` = 'Evolution Line'"
	)
	frappe.db.commit()
