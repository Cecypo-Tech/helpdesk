"""Bulk-closing stale tickets: quiet, exempt from the work-type rule, invisible to this week's jobs."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import frappe

from helpdesk.api import maintenance
from helpdesk.test_utils import make_ticket

NOW = datetime(2026, 11, 27, 20, 0)


class TestCloseStale(unittest.TestCase):
	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.delete("HD Ticket")

	def tearDown(self):
		frappe.local.request = None
		frappe.db.rollback()

	def ticket(self, opened_days, active_days):
		t = make_ticket(subject="Hii")
		frappe.db.set_value("HD Ticket", t.name, {
			"opening_date": (NOW - timedelta(days=opened_days)).date(),
			"creation": NOW - timedelta(days=opened_days),
			"modified": NOW - timedelta(days=active_days),
		}, update_modified=False)
		return t.name

	def test_dry_run_counts_only(self):
		self.ticket(10, 10)
		self.assertEqual(maintenance.close_stale_tickets(days=2, dry_run=1, now=NOW), {"count": 1, "closed": 0})
		self.assertEqual(frappe.get_all("HD Ticket", pluck="status_category"), ["Open"])

	def test_closes_old_idle_tickets_only(self):
		old = self.ticket(10, 9)
		recent_activity = self.ticket(10, 1)
		new = self.ticket(1, 1)
		frappe.local.request = frappe._dict(path="/api/method/x", method="POST", headers={})  # agent request
		res = maintenance.close_stale_tickets(days=2, dry_run=0, now=NOW)
		self.assertEqual(res, {"count": 1, "closed": 1})
		self.assertEqual(frappe.db.get_value("HD Ticket", old, "status"), "Closed")
		self.assertEqual(frappe.db.get_value("HD Ticket", recent_activity, "status_category"), "Open")
		self.assertEqual(frappe.db.get_value("HD Ticket", new, "status_category"), "Open")

	def test_closed_on_is_last_activity(self):
		old = self.ticket(10, 9)
		maintenance.close_stale_tickets(days=2, dry_run=0, now=NOW)
		self.assertEqual(frappe.db.get_value("HD Ticket", old, "closed_on"), NOW - timedelta(days=9))

	def test_system_manager_only(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				maintenance.close_stale_tickets(days=2, dry_run=1)
		finally:
			frappe.set_user("Administrator")
