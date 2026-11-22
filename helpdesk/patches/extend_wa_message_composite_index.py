import frappe

INDEX_NAME = "wa_message_line_jid_creation"
COLUMNS = ["line", "jid", "creation", "content_type"]


def execute():
	"""Add `content_type` to the end of the (line, jid, creation) index on tabWA Message.

	get_wa_conversations leaves reaction rows out of each chat's latest message
	(the thread doesn't show them). With content_type outside the index, that
	filter turned the GROUP BY jid / MAX(creation) subquery from an index-only
	read into row lookups: 7.5 ms -> 22.6 ms on a 28.6k-row line. With it as a
	trailing column the read is index-only again, and the (line, jid, creation)
	prefix still serves every query the old index did.

	Idempotent: a site already carrying these columns is left alone.
	"""
	if not frappe.db.table_exists("WA Message"):
		return

	# SHOW INDEX takes no ORDER BY, so order the columns here.
	rows = frappe.db.sql(
		"SHOW INDEX FROM `tabWA Message` WHERE Key_name = %s", INDEX_NAME, as_dict=True
	)
	current = [r.Column_name for r in sorted(rows, key=lambda r: r.Seq_in_index)]
	if current == COLUMNS:
		return

	drop = f"DROP INDEX `{INDEX_NAME}`, " if current else ""
	cols = ", ".join(f"`{c}`" for c in COLUMNS)
	frappe.db.sql(f"ALTER TABLE `tabWA Message` {drop}ADD INDEX `{INDEX_NAME}` ({cols})")
