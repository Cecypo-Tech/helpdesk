import frappe

INDEX_NAME = "wa_conversation_read_state_user_jid"


def execute():
	"""Add a UNIQUE index on (user, jid) to tabWA Conversation Read State.

	`_mark_conversation_read_for_user` does a get-then-insert-or-update that is
	not atomic: two concurrent requests for the same agent+conversation (double
	click, multiple tabs) can both find no existing row and both insert,
	creating duplicate cursor rows. Every unread-count query LEFT JOINs against
	this table with no dedup, so duplicate rows silently inflate COUNT(*) —
	a badge-count bug that doesn't self-heal.

	A DocType-level `unique: 1` flag only works for a single field; this needs
	a composite constraint, hence a patch — same pattern as
	`add_wa_message_composite_index`.
	"""
	if not frappe.db.table_exists("WA Conversation Read State"):
		return

	existing = frappe.db.sql(
		"SHOW INDEX FROM `tabWA Conversation Read State` WHERE Key_name = %s",
		INDEX_NAME,
		as_dict=True,
	)
	if existing:
		return

	frappe.db.sql(
		f"ALTER TABLE `tabWA Conversation Read State` ADD UNIQUE INDEX `{INDEX_NAME}` (`user`, `jid`)"
	)
