"""Index WhatsApp Message.message_id, and make the index durable.

flag_duplicate_whatsapp_message looks a wamid up on every inbound delivery with
SELECT ... FOR UPDATE. Unindexed, that read scans tabWhatsApp Message and holds
locks across the scan — on the ingestion hot path, for every message.

The index is created through a Property Setter rather than by ALTER alone.
WhatsApp Message belongs to frappe_whatsapp, so a hand-rolled index on it is not
described by anything helpdesk owns, and frappe's schema sync drops indexes it
cannot account for whenever that doctype is reloaded — the same way a hand-rolled
index on a core doctype was silently dropped by fixture sync before
`search_index: 1` was added to the Contact custom fields. With the Property
Setter in place frappe re-creates the index itself if the table is ever rebuilt.

The ALTER still runs here so the index exists immediately, without waiting for
the next schema sync of a doctype helpdesk does not otherwise touch.

Note there is deliberately no unique constraint. `drop_wa_message_id_unique_index`
removed one from WA Message because forwarded messages legitimately share an ID,
and a constraint here would turn a duplicate delivery into a 500 — which earns a
retry of the delivery being rejected.
"""

import frappe

INDEX_NAME = "message_id"
PROPERTY_SETTER = "WhatsApp Message-message_id-search_index"


def execute():
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return
	if not frappe.db.table_exists("WhatsApp Message"):
		return
	if not frappe.db.has_column("WhatsApp Message", "message_id"):
		return

	if not frappe.db.exists("Property Setter", PROPERTY_SETTER):
		frappe.get_doc(
			{
				"doctype": "Property Setter",
				"doctype_or_field": "DocField",
				"doc_type": "WhatsApp Message",
				"field_name": "message_id",
				"property": "search_index",
				"property_type": "Check",
				"value": "1",
			}
		).insert(ignore_permissions=True)

	existing = frappe.db.sql(
		"SHOW INDEX FROM `tabWhatsApp Message` WHERE Column_name = 'message_id'",
		as_dict=True,
	)
	if existing:
		return

	frappe.db.sql_ddl(
		f"ALTER TABLE `tabWhatsApp Message` ADD INDEX `{INDEX_NAME}` (`message_id`)"
	)
