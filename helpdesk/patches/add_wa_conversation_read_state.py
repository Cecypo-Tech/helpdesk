import frappe

from helpdesk.integrations.wa import _mark_conversation_read_for_all_agents


def execute():
	"""Backfill WA Conversation Read State so every agent starts caught up.

	Without this, agents would see every pre-existing incoming WA Line message
	as unread the moment this feature ships, since no cursor row exists yet
	and the default treats "no row" as "unread from the start."
	"""
	if not frappe.db.table_exists("WA Message") or not frappe.db.table_exists("WA Conversation Read State"):
		return

	if not frappe.get_all("HD Agent", limit=1):
		return

	jid_max_creation = frappe.db.sql(
		"""
		SELECT jid, MAX(creation) AS latest
		FROM `tabWA Message`
		WHERE direction = 'Incoming'
		GROUP BY jid
		""",
		as_dict=True,
	)

	for row in jid_max_creation:
		_mark_conversation_read_for_all_agents(row.jid, upto=row.latest)

	frappe.db.commit()
