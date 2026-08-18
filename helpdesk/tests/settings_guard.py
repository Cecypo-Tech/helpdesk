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
	"""Put the singleton back exactly as it was, holding as few locks as possible.

	Deliberately NOT `DELETE FROM tabSingles WHERE doctype=...` followed by a
	bulk re-INSERT, which is what this used to do. That pattern takes next-key
	locks across the whole (doctype, field) index range and then inserts back
	into the same gap, held until commit — a classic deadlock shape.

	It matters here because the suite runs against a live site: `frappe
	schedule` and `frappe worker` are hitting the same database, firing Outline
	sync, embedding and WhatsApp jobs that read the very same table. A deadlock
	needs two transactions, and those are the second one.

	Updating each field in place touches only the rows that actually changed and
	takes narrow record locks instead. This reduces the collision window; it
	cannot remove it, because the competing transaction is outside our control.
	The real fix is to stop the scheduler and worker while testing, or use a
	dedicated test site.
	"""
	if not snapshot:
		return

	wanted = snapshot["singles"]
	current = dict(
		frappe.db.sql(
			"SELECT field, value FROM `tabSingles` WHERE doctype=%s", (_DOCTYPE,)
		)
	)

	for field, value in wanted.items():
		if field in current:
			if current[field] != value:
				frappe.db.sql(
					"UPDATE `tabSingles` SET value=%s WHERE doctype=%s AND field=%s",
					(value, _DOCTYPE, field),
				)
		else:
			frappe.db.sql(
				"INSERT INTO `tabSingles` (doctype, field, value) VALUES (%s, %s, %s)",
				(_DOCTYPE, field, value),
			)

	# Fields a test introduced that were not in the snapshot.
	for field in set(current) - set(wanted):
		frappe.db.sql(
			"DELETE FROM `tabSingles` WHERE doctype=%s AND field=%s", (_DOCTYPE, field)
		)

	# __Auth is small and keyed per fieldname; same in-place approach.
	current_auth = {
		r.fieldname: r
		for r in frappe.db.sql(
			"SELECT fieldname, password, encrypted FROM `__Auth` "
			"WHERE doctype=%s AND name=%s",
			(_DOCTYPE, _DOCTYPE),
			as_dict=True,
		)
	}
	for row in snapshot["auth"]:
		if row.fieldname in current_auth:
			frappe.db.sql(
				"UPDATE `__Auth` SET password=%s, encrypted=%s "
				"WHERE doctype=%s AND name=%s AND fieldname=%s",
				(row.password, row.encrypted, _DOCTYPE, _DOCTYPE, row.fieldname),
			)
		else:
			frappe.db.sql(
				"INSERT INTO `__Auth` (doctype, name, fieldname, password, encrypted) "
				"VALUES (%s, %s, %s, %s, %s)",
				(_DOCTYPE, _DOCTYPE, row.fieldname, row.password, row.encrypted),
			)
	for fieldname in set(current_auth) - {r.fieldname for r in snapshot["auth"]}:
		frappe.db.sql(
			"DELETE FROM `__Auth` WHERE doctype=%s AND name=%s AND fieldname=%s",
			(_DOCTYPE, _DOCTYPE, fieldname),
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
