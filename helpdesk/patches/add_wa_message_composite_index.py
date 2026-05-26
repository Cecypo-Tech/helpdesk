import frappe


def execute():
	"""Add composite index (line, jid, creation) on tabWA Message for get_wa_conversations query."""
	if not frappe.db.table_exists("WA Message"):
		return

	existing = frappe.db.sql(
		"SHOW INDEX FROM `tabWA Message` WHERE Key_name = 'wa_message_line_jid_creation'",
		as_dict=True,
	)
	if not existing:
		frappe.db.sql(
			"ALTER TABLE `tabWA Message` ADD INDEX `wa_message_line_jid_creation` (`line`, `jid`, `creation`)"
		)
