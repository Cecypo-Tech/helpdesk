import frappe


def execute():
	"""Drop the unique index on WA Message.message_id.

	The index was added in an earlier release but breaks migration on sites
	where duplicate message_id values already exist (e.g. forwarded or
	re-delivered messages). Removing unique=1 from the field definition
	prevents it being re-added; this patch drops the index from the DB first
	so that the schema sync succeeds even when duplicates are present.
	"""
	if not frappe.db.table_exists("WA Message"):
		return

	# Check whether the unique index exists before trying to drop it
	indexes = frappe.db.sql(
		"""
		SELECT INDEX_NAME
		FROM information_schema.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE()
		  AND TABLE_NAME   = 'tabWA Message'
		  AND INDEX_NAME   = 'message_id'
		  AND NON_UNIQUE   = 0
		""",
		as_dict=True,
	)

	if indexes:
		frappe.db.sql("ALTER TABLE `tabWA Message` DROP INDEX `message_id`")
