import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Fields the entitlement sync needs.

	`erpnext_items` maps ERPNext Item codes to a product. Many codes to one
	product, because an annual and a monthly SKU are the same thing to support.

	`renewal_unpaid` records that the order carrying current cover has not been
	invoiced. Auto Repeat submits the renewal about a month BEFORE expiry, so
	without this a reminder order silently grants another year of cover to a
	customer who never renewed.

	`source_document` is the Sales Order the expiry came from, so an agent
	asking "why does it say that" has an answer.
	"""
	create_custom_fields(
		{
			"HD Product": [
				{
					"fieldname": "section_erpnext",
					"label": "ERPNext",
					"fieldtype": "Section Break",
					"insert_after": "default_team",
				},
				{
					"fieldname": "erpnext_items",
					"label": "ERPNext Item Codes",
					"fieldtype": "Table",
					"options": "HD Product Erpnext Item",
					"insert_after": "section_erpnext",
					"description": "Item codes on recurring Sales Orders that mean this product. Unmapped items are ignored by the sync.",
				},
			],
			"HD Customer Product": [
				{
					"fieldname": "renewal_unpaid",
					"label": "Renewal Unpaid",
					"fieldtype": "Check",
					"insert_after": "support_expiry",
					"read_only": 1,
					"description": "The Sales Order carrying this cover has not been fully invoiced.",
				},
				{
					"fieldname": "source_document",
					"label": "Source Document",
					"fieldtype": "Data",
					"insert_after": "source",
					"read_only": 1,
					"description": "The Sales Order this expiry came from.",
				},
			],
		},
		ignore_validate=True,
	)
	frappe.db.commit()
