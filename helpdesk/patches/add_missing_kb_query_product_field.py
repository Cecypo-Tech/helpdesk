import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Add HD Bot Missing KB Query.product.

	A gap that names its product tells you what to write next; without it you
	only know the bot failed. Custom Field rather than a doctype edit, for
	consistency with the other fields this feature adds.

	create_custom_fields is idempotent, so re-running is safe.
	"""
	create_custom_fields(
		{
			"HD Bot Missing KB Query": [
				{
					"fieldname": "product",
					"label": "Product",
					"fieldtype": "Link",
					"options": "HD Product",
					"insert_after": "suggested_category",
					"read_only": 1,
					"description": "Product the question was about, when it could be resolved.",
				},
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()
