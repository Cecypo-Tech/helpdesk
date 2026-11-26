"""The (i) overdue dialog reads powerpack's customer snapshot from ERPNext."""

import unittest
from unittest.mock import patch

import frappe

from helpdesk.integrations import erpnext_standing

SNAP = {
	"customer": "ACME Ltd",
	"currency": "KES",
	"overdue_total": 1200.0,
	"overdue_count": 1,
	"invoices": [{"name": "ACC-SINV-1", "days_overdue": 9, "outstanding_amount": 1200.0}],
}


class TestFetchSnapshot(unittest.TestCase):
	def setUp(self):
		self.clear()
		self.addCleanup(self.clear)

	def clear(self):
		frappe.cache().delete_value(erpnext_standing.snapshot_cache_key("ACME Ltd"))
		erpnext_standing.reset_breaker()

	def test_no_erp_customer_is_unknown(self):
		self.assertEqual(erpnext_standing.fetch_snapshot(None)["status"], "unknown")

	@patch("helpdesk.integrations.erpnext_standing.call")
	def test_ok_returns_snapshot(self, call):
		call.return_value = {"ok": True, "data": SNAP, "error": None}
		out = erpnext_standing.fetch_snapshot("ACME Ltd")
		self.assertEqual(out["status"], "ok")
		self.assertEqual(out["snapshot"]["invoices"][0]["name"], "ACC-SINV-1")
		self.assertIn("erp_url", out)  # invoice links in the dialog point here
		call.assert_called_once_with(
			"cecypo_powerpack.api.get_customer_snapshot", {"customer": "ACME Ltd"}
		)

	@patch("helpdesk.integrations.erpnext_standing.call")
	def test_remote_failure_is_unavailable_not_raise(self, call):
		call.return_value = {"ok": False, "data": None, "error": "HTTP 403"}
		out = erpnext_standing.fetch_snapshot("ACME Ltd")
		self.assertEqual(out["status"], "unavailable")
		self.assertIsNone(out["snapshot"])

	@patch("helpdesk.integrations.erpnext_standing.call")
	def test_empty_snapshot_is_unknown(self, call):
		# powerpack returns {} when its warnings switch is off
		call.return_value = {"ok": True, "data": {}, "error": None}
		self.assertEqual(erpnext_standing.fetch_snapshot("ACME Ltd")["status"], "unknown")

	@patch("helpdesk.integrations.erpnext_standing.call")
	def test_second_call_is_cached(self, call):
		call.return_value = {"ok": True, "data": SNAP, "error": None}
		erpnext_standing.fetch_snapshot("ACME Ltd")
		erpnext_standing.fetch_snapshot("ACME Ltd")
		call.assert_called_once()

	@patch("helpdesk.integrations.erpnext_standing.frappe.log_error")
	@patch("helpdesk.integrations.erpnext_standing.call")
	def test_failure_is_cached_briefly(self, call, log_error):
		# A lasting refusal (e.g. a 403) must not cost a remote call and an
		# Error Log row on every ticket view.
		call.return_value = {"ok": False, "data": None, "error": "HTTP 403"}
		erpnext_standing.fetch_snapshot("ACME Ltd")
		erpnext_standing.fetch_snapshot("ACME Ltd")
		call.assert_called_once()
		log_error.assert_called_once()

	@patch("helpdesk.integrations.erpnext_standing.call")
	def test_unknown_is_cached(self, call):
		call.return_value = {"ok": True, "data": {}, "error": None}
		erpnext_standing.fetch_snapshot("ACME Ltd")
		erpnext_standing.fetch_snapshot("ACME Ltd")
		call.assert_called_once()
