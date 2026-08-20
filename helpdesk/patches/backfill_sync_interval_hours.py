"""Give sync_interval_hours its default on singletons that predate scheduling.

A Frappe Single only takes its JSON defaults when tabSingles has no row for it.
ERPNext Sync Settings was saved before the scheduled sync existed, so the field
reads back as 0 -- and 0 is the value that means "never schedule". The result is
a scheduler that ticks every hour and deliberately does nothing.

Backfills when the value is absent OR zero. Treating zero as "needs the
default" is safe exactly once, here: until this release nothing read
sync_interval_hours at all, so no operator can have chosen 0 to mean anything.
Any zero on an existing site is an artifact of Frappe writing every field of a
Single on save, not a decision.

After this patch, 0 does mean "never schedule" and is honoured -- run_scheduled_sync
checks it on every tick. This patch runs once and will not revisit the choice.
"""

import frappe

DOCTYPE = "ERPNext Sync Settings"
FIELD = "sync_interval_hours"
DEFAULT = 6


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	# order_by=None because tabSingles has no creation column for the ORM's
	# default ordering to sort on. A row can exist holding "0" purely because a
	# full save of the Single writes every field, so the row's presence is not
	# evidence anybody chose the value.
	raw = frappe.db.get_value(
		"Singles", {"doctype": DOCTYPE, "field": FIELD}, "value", order_by=None
	)
	if raw is not None and str(raw).strip() not in ("", "0"):
		return

	frappe.db.set_single_value(DOCTYPE, FIELD, DEFAULT)
