import frappe
from frappe.model.document import Document


class WaBotSession(Document):
	pass


def get_or_create(jid: str, line: str) -> "WaBotSession":
	name = frappe.db.get_value("WA Bot Session", {"jid": jid, "line": line}, "name")
	if name:
		return frappe.get_doc("WA Bot Session", name)
	doc = frappe.get_doc({"doctype": "WA Bot Session", "jid": jid, "line": line})
	doc.insert(ignore_permissions=True)
	return doc
