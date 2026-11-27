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


class TestTicketSnapshotEndpoint(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.ticket = frappe.get_doc({"doctype": "HD Ticket", "subject": "_SNAP ticket"}).insert(
			ignore_permissions=True
		)
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", self.ticket.name, force=True, ignore_permissions=True
		)

	def test_ticket_without_customer_is_flagged_missing(self):
		from helpdesk.api.entitlement import get_ticket_customer_snapshot

		self.assertTrue(get_ticket_customer_snapshot(self.ticket.name)["missing_customer"])


class TestBillingContactFlag(unittest.TestCase):
	"""The (i) dialog warns before an agent sends figures to a non-billing contact."""

	def setUp(self):
		frappe.set_user("Administrator")
		cust = "ACME Snapshot Billing"
		if not frappe.db.exists("HD Customer", cust):
			frappe.get_doc({"doctype": "HD Customer", "customer_name": cust}).insert(ignore_permissions=True)
		self.contact = frappe.get_doc({"doctype": "Contact", "first_name": "Snap Billing"}).insert(
			ignore_permissions=True
		)
		self.ticket = frappe.get_doc({"doctype": "HD Ticket", "subject": "_SNAP billing", "customer": cust,
		                              "contact": self.contact.name}).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.db.rollback()

	@patch("helpdesk.integrations.erpnext_standing.fetch_snapshot", return_value={"status": "unknown", "snapshot": None})
	def test_reports_billing_contact(self, _):
		from helpdesk.api.entitlement import get_ticket_customer_snapshot

		frappe.db.set_value("HD Ticket", self.ticket.name, "contact", self.contact.name)
		self.assertFalse(get_ticket_customer_snapshot(self.ticket.name)["billing_contact"])
		frappe.db.set_value("Contact", self.contact.name, "is_billing_contact", 1)
		self.assertTrue(get_ticket_customer_snapshot(self.ticket.name)["billing_contact"])
