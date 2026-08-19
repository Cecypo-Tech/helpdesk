"""Ask unknown contacts for the KRA PIN alone, not the company name.

The company name was never used for matching — `verification.match_claim`
compares tax_id only, and the company's identity comes from the mirrored
ERPNext record once the PIN resolves. Asking for it gave the customer a second
thing to get wrong for no benefit, so the prompt now asks for the PIN, says
what it is for, and says it is a one-off.

Rewrites the stored value ONLY when it is still the original default, so a
prompt someone has reworded is left exactly as they wrote it.
"""

import frappe

DOCTYPE = "WhatsApp Helpdesk Settings"
FIELD = "verification_prompt"

PREVIOUS_DEFAULT = (
	"Hi! So we can pull up your account, could you reply with your company name "
	"and KRA PIN? Thanks."
)
NEW_DEFAULT = (
	"Hi! Please reply with your company KRA PIN so we can link this WhatsApp "
	"number to your account. We only need this once."
)


def execute():
	# The raw Singles row, not get_single_value: that casts a missing row to ""
	# rather than None, which would make "never written" indistinguishable from
	# "deliberately blanked". order_by=None because tabSingles has no creation
	# column for the ORM's default ordering to sort on.
	current = frappe.db.get_value(
		"Singles", {"doctype": DOCTYPE, "field": FIELD}, "value", order_by=None
	)

	if current is None:
		# Never written. add_contact_verification_fields will stamp the new
		# default when it runs; nothing to do here.
		return

	if (current or "").strip() != PREVIOUS_DEFAULT:
		# Reworded by an operator, or already the new text. Leave it alone.
		return

	frappe.db.set_single_value(DOCTYPE, FIELD, NEW_DEFAULT)
