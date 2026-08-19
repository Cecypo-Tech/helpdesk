import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Mark which Contacts came from ERPNext.

	This field IS the ownership boundary. A Contact carrying a value here is
	maintained by the sync; a Contact without one belongs to Helpdesk — created
	from WhatsApp, or a salesperson deliberately kept out of ERPNext — and the
	sync must never touch it.

	Data rather than Link: the Contact doctype on the remote bench does not
	exist here, and must not become an install dependency.
	"""
	create_custom_fields(
		{
			"Contact": [
				{
					"fieldname": "erpnext_contact",
					"label": "ERPNext Contact",
					"fieldtype": "Data",
					"insert_after": "company_name",
					"read_only": 1,
					"description": "Set by the ERPNext sync. Empty means this contact belongs to Helpdesk and the sync will not modify it.",
				},
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()
