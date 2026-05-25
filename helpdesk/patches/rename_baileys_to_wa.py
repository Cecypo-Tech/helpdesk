import frappe


def execute():
	"""Rename Baileys Message → WA Message and Baileys Message Edit History → WA Message Edit History."""
	if frappe.db.exists("DocType", "Baileys Message Edit History") and not frappe.db.exists(
		"DocType", "WA Message Edit History"
	):
		frappe.rename_doc("DocType", "Baileys Message Edit History", "WA Message Edit History", force=True)
	elif frappe.db.exists("DocType", "Baileys Message Edit History"):
		frappe.db.sql("DELETE FROM `tabDocType` WHERE `name` = 'Baileys Message Edit History'")
		frappe.db.sql("DELETE FROM `tabDocField` WHERE `parent` = 'Baileys Message Edit History'")
		frappe.db.sql("DELETE FROM `tabDocPerm` WHERE `parent` = 'Baileys Message Edit History'")

	if frappe.db.exists("DocType", "Baileys Message") and not frappe.db.exists("DocType", "WA Message"):
		frappe.rename_doc("DocType", "Baileys Message", "WA Message", force=True)
	elif frappe.db.exists("DocType", "Baileys Message"):
		frappe.db.sql("DELETE FROM `tabDocType` WHERE `name` = 'Baileys Message'")
		frappe.db.sql("DELETE FROM `tabDocField` WHERE `parent` = 'Baileys Message'")
		frappe.db.sql("DELETE FROM `tabDocPerm` WHERE `parent` = 'Baileys Message'")

	# Ensure model sync registers these DocTypes regardless of rename order
	for module, doctype in [("Helpdesk", "wa_message_edit_history"), ("Helpdesk", "wa_message")]:
		frappe.reload_doc(module, "doctype", doctype, force=True)

	frappe.db.commit()
