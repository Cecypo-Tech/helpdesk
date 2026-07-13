"""Snapshot/restore for Helpdesk Bot Settings around test modules.

Tests in this app run against a real site database and several of them write
to the Helpdesk Bot Settings singleton (provider, prompts, API keys, flags).
Without a restore step those writes leak into the live site — including
overwriting the real API keys in __Auth with test values. Every test module
that touches bot settings must snapshot in setUpModule and restore in
tearDownModule.
"""

import frappe

_DOCTYPE = "Helpdesk Bot Settings"


def snapshot_bot_settings() -> dict:
	singles = dict(
		frappe.db.sql(
			"SELECT field, value FROM `tabSingles` WHERE doctype=%s", (_DOCTYPE,)
		)
	)
	auth = frappe.db.sql(
		"SELECT fieldname, password, encrypted FROM `__Auth` WHERE doctype=%s AND name=%s",
		(_DOCTYPE, _DOCTYPE),
		as_dict=True,
	)
	child_rows = frappe.db.get_all(
		"HD Bot Allowed Category",
		filters={"parent": _DOCTYPE},
		fields=["category"],
		order_by="idx",
	)
	return {"singles": singles, "auth": auth, "allowed_categories": child_rows}


def restore_bot_settings(snapshot: dict | None) -> None:
	if not snapshot:
		return

	frappe.db.sql("DELETE FROM `tabSingles` WHERE doctype=%s", (_DOCTYPE,))
	for field, value in snapshot["singles"].items():
		frappe.db.sql(
			"INSERT INTO `tabSingles` (doctype, field, value) VALUES (%s, %s, %s)",
			(_DOCTYPE, field, value),
		)

	frappe.db.sql(
		"DELETE FROM `__Auth` WHERE doctype=%s AND name=%s", (_DOCTYPE, _DOCTYPE)
	)
	for row in snapshot["auth"]:
		frappe.db.sql(
			"INSERT INTO `__Auth` (doctype, name, fieldname, password, encrypted) "
			"VALUES (%s, %s, %s, %s, %s)",
			(_DOCTYPE, _DOCTYPE, row.fieldname, row.password, row.encrypted),
		)

	frappe.db.delete("HD Bot Allowed Category", {"parent": _DOCTYPE})
	settings = frappe.get_doc(_DOCTYPE)
	settings.set(
		"allowed_categories",
		[{"category": r.category} for r in snapshot["allowed_categories"]],
	)
	# Write child rows without triggering validate/save side effects on Singles.
	for row in settings.allowed_categories:
		row.parent = _DOCTYPE
		row.parenttype = _DOCTYPE
		row.parentfield = "allowed_categories"
		row.db_insert()

	frappe.db.commit()
	frappe.clear_document_cache(_DOCTYPE, _DOCTYPE)
