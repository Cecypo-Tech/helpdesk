"""One-off housekeeping for System Managers."""

from datetime import datetime, timedelta

import frappe
from frappe.utils import cint, now_datetime


@frappe.whitelist()
def close_stale_tickets(days: int = 2, dry_run: int = 1, now: datetime | None = None) -> dict:
	"""Close open tickets opened and last touched more than `days` ago.

	- Exempt from the work-type rule: nobody can recall what an old "Hii" was about.
	- closed_on = last activity, so learning and the digest treat them as old closes,
	  not as hundreds of closes this week.
	- Quiet: same save path as auto-close; no customer messages are sent.
	"""
	frappe.only_for("System Manager")
	cutoff = (now or now_datetime()) - timedelta(days=cint(days))
	names = frappe.get_all(
		"HD Ticket",
		filters={"status_category": ["!=", "Resolved"], "creation": ["<", cutoff], "modified": ["<", cutoff]},
		pluck="name",
	)
	if cint(dry_run):
		return {"count": len(names), "closed": 0}
	closed = 0
	for name in names:
		doc = frappe.get_doc("HD Ticket", name)
		last_activity = doc.modified
		doc.status = "Closed"
		doc.flags.skip_work_type = True
		try:
			doc.save(ignore_permissions=True)
		except Exception:
			frappe.log_error(title=f"Stale close failed for {name}")
			continue
		frappe.db.set_value("HD Ticket", name, "closed_on", last_activity, update_modified=False)
		frappe.db.commit()  # nosemgrep: keep progress on a long run
		closed += 1
	return {"count": len(names), "closed": closed}
