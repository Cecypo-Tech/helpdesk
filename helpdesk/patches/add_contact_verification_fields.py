"""Add the unknown-contact verification fields to Contact.

Custom Fields rather than edits to Frappe's Contact doctype, so a
`git merge upstream/develop` cannot drop them.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELDS = {
	"Contact": [
		{
			"fieldname": "hd_verification_status",
			"label": "Helpdesk Verification Status",
			"fieldtype": "Select",
			"options": "Unverified\nAsked\nClaimed\nVerified\nRejected",
			"default": "Unverified",
			"insert_after": "email_id",
			"read_only": 1,
		},
		{
			"fieldname": "hd_claimed_company",
			"label": "Claimed Company",
			"fieldtype": "Data",
			"insert_after": "hd_verification_status",
			"read_only": 1,
			"description": "What the contact typed. A claim, never trusted.",
		},
		{
			"fieldname": "hd_claimed_tax_id",
			"label": "Claimed KRA PIN",
			"fieldtype": "Data",
			"insert_after": "hd_claimed_company",
			"read_only": 1,
			"description": "What the contact typed. A claim, never trusted.",
		},
		{
			"fieldname": "hd_verification_asked_on",
			"label": "Verification Asked On",
			"fieldtype": "Datetime",
			"insert_after": "hd_claimed_tax_id",
			"read_only": 1,
		},
	]
}


def execute():
	create_custom_fields(FIELDS, ignore_validate=True)

	# WhatsApp Helpdesk Settings is a Single. Its schema defaults only apply
	# when the singleton has never been saved (frappe.get_single falls back
	# to frappe.new_doc only if tabSingles has zero rows for it). This site's
	# singleton already has rows from earlier features, so the three new
	# fields would otherwise load as None instead of their JSON defaults.
	frappe.db.set_single_value("WhatsApp Helpdesk Settings", "verification_enabled", 0)
	frappe.db.set_single_value(
		"WhatsApp Helpdesk Settings",
		"verification_prompt",
		"Hi! So we can pull up your account, could you reply with your company name and KRA PIN? Thanks.",
	)
	frappe.db.set_single_value("WhatsApp Helpdesk Settings", "verification_reask_days", 7)
