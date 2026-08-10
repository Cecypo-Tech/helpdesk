"""Drop the leftover tabWA Bot Session table.

The WA Bot Session doctype was superseded by Redis-backed session state (see
BotSession in helpdesk/integrations/bot.py, keyed line:jid with a 7-day TTL) but
the doctype kept shipping unused. frappe's migrate then reported it on every run:

    Orphaned DocType(s) found: WA Bot Session

because get_controller() resolved it to frappe.core.doctype.wa_bot_session rather
than helpdesk's. frappe removes the DocType record but deliberately leaves the
table ("Deleting the entry doesn't delete any data"), so the table outlives the
doctype on every site.

The doctype is now removed from the app, and this clears the table it left.
"""

import frappe

TABLE = "tabWA Bot Session"


def execute():
	if not frappe.db.table_exists("WA Bot Session"):
		return

	rows = frappe.db.sql(f"SELECT COUNT(*) FROM `{TABLE}`")[0][0]
	if rows:
		# Never drop data. Nothing has written here since the move to Redis, so
		# rows mean a site used the doctype before that and someone should look
		# before it goes.
		print(
			f"  {TABLE} still holds {rows} row(s) — leaving it in place."
			" Drop it by hand once you have confirmed the data is not needed."
		)
		return

	frappe.db.sql_ddl(f"DROP TABLE `{TABLE}`")
