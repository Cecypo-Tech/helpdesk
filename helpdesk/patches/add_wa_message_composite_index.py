import frappe

INDEX_NAME = "wa_message_line_jid_creation"


def execute():
	"""Add composite index (line, jid, creation) on tabWA Message for get_wa_conversations query.

	Serves both halves of that query: the `GROUP BY jid / MAX(creation)` subquery
	and the join back onto the latest row. Measured on ~14k rows: 15,221 rows
	scanned and 17ms without it, 6,092 rows and 5ms with — and it runs on every
	page load and after every incoming message.

	Re-runnable on purpose. This patch first ran on 2026-06-02 and its index is
	gone on at least one site: the table was recreated eight days later
	(CREATE_TIME 2026-06-10), taking the index with it, and a patch normally
	never runs twice. Frappe keys Patch Log on the *exact* string from
	patches.txt, so the ` #<date>` token there makes this run again. Bump that
	token if the index ever needs re-applying.

	A DocType `search_index` would be self-healing, but only produces
	single-column indexes — no way to declare a composite — hence a patch.
	"""
	if not frappe.db.table_exists("WA Message"):
		return

	existing = frappe.db.sql(
		"SHOW INDEX FROM `tabWA Message` WHERE Key_name = %s",
		INDEX_NAME,
		as_dict=True,
	)
	if existing:
		return

	frappe.db.sql(
		f"ALTER TABLE `tabWA Message` ADD INDEX `{INDEX_NAME}` (`line`, `jid`, `creation`)"
	)
