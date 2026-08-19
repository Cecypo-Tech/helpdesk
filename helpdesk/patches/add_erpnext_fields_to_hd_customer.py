import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Mirror ERPNext identity fields onto HD Customer.

	Data rather than Link on every one of them: these mirror values from a
	REMOTE bench, so the doctypes they would link to (Territory, Customer Group)
	do not exist here and must not become an install dependency.

	Custom Fields rather than an edit to hd_customer.json, for the same
	merge-conflict reason as the rest of this work.
	"""
	create_custom_fields(
		{
			"HD Customer": [
				{
					"fieldname": "tax_id",
					"label": "Tax ID",
					"fieldtype": "Data",
					"insert_after": "domain",
					"description": "KRA PIN, mirrored from ERPNext. Also the key the contact verification flow will match on.",
				},
				{
					"fieldname": "territory",
					"label": "Territory",
					"fieldtype": "Data",
					"insert_after": "tax_id",
				},
				{
					"fieldname": "customer_group",
					"label": "Customer Group",
					"fieldtype": "Data",
					"insert_after": "territory",
				},
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()
