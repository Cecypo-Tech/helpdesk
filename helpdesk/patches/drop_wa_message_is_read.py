import frappe


def execute():
	"""Drop the now-unused WA Message.is_read column.

	Per-agent unread state moved to the WA Conversation Read State cursor
	doctype; nothing reads or writes is_read anymore. The field was removed
	from the WA Message doctype definition, but Frappe's schema sync never
	drops columns for removed fields on its own, so the column would
	otherwise be left behind as a dead orphan. Drop it explicitly here.
	"""
	if not frappe.db.table_exists("WA Message"):
		return

	frappe.model.delete_fields({"WA Message": ["is_read"]}, delete=1)
