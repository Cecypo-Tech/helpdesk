import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Add hd_product and support_status to HD Ticket as Custom Fields.

	Custom Fields rather than edits to hd_ticket.json: that file is upstream's
	and every edit conflicts on `git merge upstream/develop`. Same approach as
	baileys_jid / baileys_line.

	Upstream's `product` Select (options "Product A/B/C") is deliberately left
	alone. It is dead — zero references in Python or Vue — but repurposing it
	would conflict on every upstream merge for no benefit.

	create_custom_fields is idempotent, so re-running is safe.
	"""
	create_custom_fields(
		{
			"HD Ticket": [
				{
					"fieldname": "hd_product",
					"label": "Product",
					"fieldtype": "Link",
					"options": "HD Product",
					"insert_after": "ticket_type",
					"description": "What this ticket is about. Defaults to the customer's entitled products, but any active product may be chosen.",
				},
				{
					"fieldname": "support_status",
					"label": "Support Status",
					"fieldtype": "Select",
					"options": "\nCovered\nExpired\nNot Entitled\nUnknown",
					"insert_after": "hd_product",
					"read_only": 1,
					"description": "Stamped once, when the product first becomes known. Frozen afterwards so renewals do not rewrite history.",
				},
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()
