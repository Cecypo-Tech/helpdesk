import frappe

INDEX_NAME = "wa_message_direction_creation_jid"


def execute():
	"""Add composite index (direction, creation, jid) on tabWA Message for _unread_counts_for_user.

	That query filters `direction = 'Incoming'` and `jid IN (...)`, and (since
	the unread-window floor was added) `creation > :floor`, then joins
	`WA Conversation Read State` on `jid`. It runs inside get_wa_conversations,
	the single largest consumer of site compute. Measured over 19.7k rows with a
	30-day floor: 34.4 ms without this index, 25.2 ms with — and
	get_wa_conversations as a whole 48.5 ms vs 39.0 ms.

	Column order: equality on `direction` first, then the `creation` range, then
	`jid` so the read-state join key is read straight from the index. `line` is
	deliberately left out — it would make the get_wa_lines variant covering but
	pushes the key past 1.6 KB.

	Note this index does *not* help the per-line badge query in `get_wa_lines`,
	which has no `jid IN` predicate: the optimizer prefers the plain `creation`
	index there, and forcing this one measured identically (43 ms either way).
	What fixed that query was the floor bounding how much history it touches, not
	an index. Don't "fix" the plan by adding a hint — there is nothing to win.

	`message_id` is indexed separately via `search_index` on the DocType field
	rather than here: it is a single column, so Frappe's schema sync can create
	it and, unlike a patch, re-create it if the table is ever rebuilt. (Note the
	earlier `drop_wa_message_id_unique_index` patch only removed the *unique*
	constraint, which duplicate forwarded message IDs violated — a plain
	non-unique index is what the webhook lookups actually needed.)
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
		f"ALTER TABLE `tabWA Message` ADD INDEX `{INDEX_NAME}` (`direction`, `creation`, `jid`)"
	)
