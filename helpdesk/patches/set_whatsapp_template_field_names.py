"""Backfill field_names on the WhatsApp templates helpdesk seeds.

seed_whatsapp_templates set sample_values but never field_names, so every
seeded template declared {{1}} and {{2}} with no mapping to HD Ticket fields.
_render_template_for_ticket refuses to guess and throws:

    Template hd_service_rating-en has variables but no Field Names are
    configured.

which made every seeded template unusable — visible the moment one is picked,
since the picker previews on selection.

The seed itself now sets field_names, but it skips templates that already
exist, so anything already created needs this.
"""

import frappe

# {{1}} is the person's name, {{2}} the ticket number, in all three bodies.
# contact.first_name greets the person by name; plain `contact` would render
# the Contact document key (first_name-company_name). `name` is the ticket id.
FIELD_NAMES = "contact.first_name,name"

SEEDED = (
	"hd_tech_support_welcome-en",
	"hd_service_rating-en",
	"hd_tech_support_followup-en",
)


def execute():
	if not frappe.db.exists("DocType", "WhatsApp Templates"):
		return

	for name in SEEDED:
		if not frappe.db.exists("WhatsApp Templates", name):
			continue
		current = frappe.db.get_value("WhatsApp Templates", name, "field_names")
		if (current or "").strip():
			# An admin has already mapped this one; their mapping wins.
			continue
		frappe.db.set_value(
			"WhatsApp Templates", name, "field_names", FIELD_NAMES, update_modified=False
		)

	# Only the seeded three are touched. Any other template carrying variables
	# without field_names was authored by an admin, and only they know which
	# HD Ticket field belongs in each slot — guessing would send wrong data to
	# a customer. Report them so they can be fixed by hand.
	unmapped = [
		row.name
		for row in frappe.get_all(
			"WhatsApp Templates", fields=["name", "sample_values", "field_names"]
		)
		if (row.sample_values or "").strip() and not (row.field_names or "").strip()
	]
	if unmapped:
		print(
			"  WhatsApp templates with variables but no Field Names"
			f" (set these by hand): {', '.join(unmapped)}"
		)
