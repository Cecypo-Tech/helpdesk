import frappe
from frappe.model.document import Document


class WhatsAppHelpdeskSettings(Document):
	pass


_NUMERIC = ("Int", "Float", "Currency", "Percent", "Check")


def fill_unset_defaults():
	"""Store the default of every field that has no value yet (after_migrate).

	Frappe applies field defaults only to new documents. A field added after
	this Single was first saved has no row in tabSingles; the form shows it
	as 0 and the next save stores that 0, e.g. unread_window_days 30 -> 0
	(no unread limit)."""
	doctype = "WhatsApp Helpdesk Settings"
	stored = dict(frappe.db.sql("SELECT field, value FROM `tabSingles` WHERE doctype = %s", doctype))
	for df in frappe.get_meta(doctype).fields:
		if df.default is None:
			continue
		# A save stores numbers as 0, never NULL, so a NULL number is unset too.
		if df.fieldname not in stored or (stored[df.fieldname] is None and df.fieldtype in _NUMERIC):
			frappe.db.set_single_value(doctype, df.fieldname, df.default, update_modified=False)
	frappe.clear_document_cache(doctype, doctype)
