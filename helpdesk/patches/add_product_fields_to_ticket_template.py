import frappe

TEMPLATE = "Default"
FIELDS = ("hd_product", "support_status")


def execute():
	"""Show hd_product and support_status in the agent ticket sidebar.

	The sidebar's "Additional Fields" section is driven entirely by
	HD Ticket Template Field rows — `get_ticket_customizations`
	(hd_ticket/api.py) reads them, and TicketField.vue already renders a Link
	fieldtype as a real doctype picker and a read-only Select as a disabled
	one. So adding these two rows IS the feature; no frontend change is needed.

	Without this, hd_product is settable only from /app, which meant everything
	built on it — bot product scoping, the coverage panel, the frozen
	support_status — sat inert on any site nobody had hand-configured.

	Deliberately a patch and NOT a fixture. Frappe fixtures overwrite on import,
	and this template is user-editable config: shipping it as a fixture would
	silently wipe any fields an admin had added the next time they migrated.
	Appending only what is missing cannot do that.
	"""
	if not frappe.db.exists("HD Ticket Template", TEMPLATE):
		return

	doc = frappe.get_doc("HD Ticket Template", TEMPLATE)
	existing = {row.fieldname for row in (doc.fields or [])}
	meta = frappe.get_meta("HD Ticket")

	added = False
	for fieldname in FIELDS:
		if fieldname in existing:
			continue
		# HD Ticket Template validates that the fieldname exists on HD Ticket and
		# throws otherwise. hd_product and support_status are Custom Fields created
		# by an earlier patch, so on a site where that has not run yet this save
		# would abort the whole migrate. A missing sidebar row is a far better
		# outcome than a failed upgrade, so skip rather than throw.
		if not meta.get_field(fieldname):
			continue
		doc.append("fields", {"fieldname": fieldname})
		added = True

	if not added:
		return

	doc.save(ignore_permissions=True)
	frappe.db.commit()
