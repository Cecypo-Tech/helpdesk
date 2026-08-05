import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

INDEX_NAME = "wa_message_normalized_phone_creation"

# Must stay in step with _normalize_phone() in helpdesk/integrations/wa.py, which
# strips everything that is not a digit. If that rule ever changes, this column
# needs re-backfilling — bump the ` #<date>` token in patches.txt to re-run.
NORMALIZE_SQL = (
	"REGEXP_REPLACE(CASE WHEN `type`='Incoming' THEN `from` ELSE `to` END, '[^0-9]', '')"
)


def execute():
	"""Store and index the conversation key on WhatsApp Message.

	get_whatsapp_conversations() used to derive the key with REGEXP_REPLACE over
	`from`/`to`, in both the SELECT and the window's PARTITION BY. An expression
	can never use an index, so every conversation page load scanned the whole
	table. Storing the value at write time makes it indexable.

	This patch creates the Custom Field itself rather than waiting for the
	fixture: frappe runs patches in run_schema_updates() but syncs fixtures in
	post_schema_updates(), i.e. afterwards. On a first migrate the column would
	not exist yet, the backfill would skip, and — patches running only once —
	no later migrate would fill it in. The fixture still ships the field so it
	stays in sync from then on; create_custom_field is a no-op when it exists.

	Re-runnable: the backfill only touches empty rows and the index is guarded.
	"""
	if not frappe.db.table_exists("WhatsApp Message"):
		return

	if not frappe.db.has_column("WhatsApp Message", "normalized_phone"):
		create_custom_field(
			"WhatsApp Message",
			{
				"fieldname": "normalized_phone",
				"label": "Normalized Phone",
				"fieldtype": "Data",
				"read_only": 1,
				"no_copy": 1,
				"insert_after": "profile_name",
				"module": "Helpdesk",
			},
		)
		frappe.db.commit()

	# One statement over the table, restricted to rows that have no value yet so
	# a re-run costs nothing.
	frappe.db.sql(
		f"""
		UPDATE `tabWhatsApp Message`
		SET normalized_phone = {NORMALIZE_SQL}
		WHERE IFNULL(normalized_phone, '') = ''
		"""
	)
	# Frappe refuses DDL after DML in one transaction (ImplicitCommitError), so
	# the backfill has to land before the index is added. Doing it in this order
	# also means the bulk UPDATE runs against an unindexed column and the index
	# is built once, rather than maintained row by row.
	frappe.db.commit()

	existing = frappe.db.sql(
		"SHOW INDEX FROM `tabWhatsApp Message` WHERE Key_name = %s",
		INDEX_NAME,
		as_dict=True,
	)
	if not existing:
		# Composite, in this order: the conversation query partitions by
		# normalized_phone and takes the newest row per partition, then pages by
		# creation. A single-column index would serve only the first half.
		frappe.db.sql(
			f"ALTER TABLE `tabWhatsApp Message`"
			f" ADD INDEX `{INDEX_NAME}` (`normalized_phone`, `creation`)"
		)
