"""Live account standing, read from ERPNext per request.

Standing is deliberately NOT mirrored: it changes daily, it is advisory, and a
stale copy is worse than a live read that degrades visibly. That makes latency
and failure the design problems, so this module is mostly cache and circuit
breaker.

The binding rule: standing NEVER blocks. An ERPNext outage costs the badge, not
the ticket.
"""

import unittest
from unittest.mock import patch

import frappe

from helpdesk.integrations import erpnext_standing as standing

CUSTOMER = "_test-standing-cust"


def ok_payload(**over):
    row = {
        "customer": CUSTOMER,
        "outstanding": 1000.0,
        "overdue": 250.0,
        "overdue_count": 2,
        "oldest_due_date": "2026-07-01",
        "days_overdue": 48,
        "currency": "KES",
        "is_overdue": True,
        "invoices": [],
    }
    row.update(over)
    return {"ok": True, "error": None,
            "data": {"version": 1, "as_of": "2026-08-19 00:00:00", "standing": [row]}}


class _Base(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        standing.reset_breaker()
        frappe.cache().delete_value(standing.cache_key(CUSTOMER))
        self.addCleanup(standing.reset_breaker)


class TestFetch(_Base):
    def test_returns_the_customers_standing(self):
        with patch.object(standing, "call", return_value=ok_payload()) as c:
            out = standing.fetch(CUSTOMER)

        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["overdue"], 250.0)
        self.assertEqual(out["days_overdue"], 48)
        self.assertTrue(out["is_overdue"])
        c.assert_called_once()

    def test_no_customer_is_not_an_error(self):
        """A ticket with no linked ERPNext customer is ordinary, not a failure."""
        with patch.object(standing, "call") as c:
            out = standing.fetch("")
        self.assertEqual(out["status"], "unknown")
        c.assert_not_called()

    def test_second_call_is_served_from_cache(self):
        with patch.object(standing, "call", return_value=ok_payload()) as c:
            standing.fetch(CUSTOMER)
            standing.fetch(CUSTOMER)
        self.assertEqual(c.call_count, 1, "a ticket view must not re-hit ERPNext")

    def test_customer_absent_from_the_response_reports_no_balance(self):
        empty = {"ok": True, "error": None, "data": {"version": 1, "standing": []}}
        with patch.object(standing, "call", return_value=empty):
            out = standing.fetch(CUSTOMER)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["outstanding"], 0)
        self.assertFalse(out["is_overdue"])


class TestFailureIsAdvisoryOnly(_Base):
    def test_failure_degrades_and_never_raises(self):
        fail = {"ok": False, "data": None, "error": "connection refused"}
        with patch.object(standing, "call", return_value=fail):
            out = standing.fetch(CUSTOMER)

        self.assertEqual(out["status"], "unavailable")
        self.assertIn("connection refused", out["error"])

    def test_an_exception_inside_the_client_still_degrades(self):
        with patch.object(standing, "call", side_effect=RuntimeError("boom")):
            out = standing.fetch(CUSTOMER)
        self.assertEqual(out["status"], "unavailable")

    def test_a_failure_is_not_cached_as_a_result(self):
        """Caching a failure would hide a recovered ERPNext for the whole TTL."""
        fail = {"ok": False, "data": None, "error": "boom"}
        with patch.object(standing, "call", return_value=fail):
            standing.fetch(CUSTOMER)
        with patch.object(standing, "call", return_value=ok_payload()) as c:
            out = standing.fetch(CUSTOMER)
        self.assertEqual(out["status"], "ok")
        c.assert_called_once()


class TestCircuitBreaker(_Base):
    def test_opens_after_the_threshold_and_stops_calling(self):
        """Without this, every ticket view pays a full timeout while ERPNext is
        down — turning an outage there into a slow helpdesk here."""
        fail = {"ok": False, "data": None, "error": "timeout"}
        with patch.object(standing, "call", return_value=fail) as c:
            for _ in range(standing.BREAKER_THRESHOLD):
                standing.fetch(CUSTOMER)
            calls_before = c.call_count
            out = standing.fetch(CUSTOMER)

        self.assertEqual(calls_before, standing.BREAKER_THRESHOLD)
        self.assertEqual(c.call_count, calls_before, "breaker must stop further calls")
        self.assertEqual(out["status"], "unavailable")

    def test_a_success_clears_the_failure_count(self):
        fail = {"ok": False, "data": None, "error": "timeout"}
        with patch.object(standing, "call", return_value=fail):
            standing.fetch(CUSTOMER)
        frappe.cache().delete_value(standing.cache_key(CUSTOMER))
        with patch.object(standing, "call", return_value=ok_payload()):
            standing.fetch(CUSTOMER)

        self.assertEqual(standing.failure_count(), 0)

    def test_breaker_is_global_not_per_customer(self):
        """ERPNext being down is not a property of one customer."""
        fail = {"ok": False, "data": None, "error": "timeout"}
        with patch.object(standing, "call", return_value=fail):
            for _ in range(standing.BREAKER_THRESHOLD):
                standing.fetch(CUSTOMER)

        with patch.object(standing, "call", return_value=ok_payload()) as c:
            out = standing.fetch("_test-standing-other")
        c.assert_not_called()
        self.assertEqual(out["status"], "unavailable")


class TestTicketStandingApi(unittest.TestCase):
    """The agent-facing endpoint.

    Deliberately separate from get_ticket_entitlement: standing is a network
    call, and bundling it would make the whole coverage panel wait on ERPNext.
    """

    def setUp(self):
        frappe.set_user("Administrator")
        standing.reset_breaker()
        self._cleanup()
        self.customer = frappe.get_doc({
            "doctype": "HD Customer",
            "customer_name": "_test-standapi-cust",
            "erpnext_customer": "_test-standapi-erp",
        }).insert(ignore_permissions=True).name
        self.ticket = frappe.get_doc({
            "doctype": "HD Ticket",
            "subject": "_test-standapi-t",
            "description": "x",
            "customer": self.customer,
        }).insert(ignore_permissions=True).name
        frappe.db.commit()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        frappe.set_user("Administrator")
        for t in frappe.get_all("HD Ticket", filters={"subject": ["like", "_test-standapi-%"]}, pluck="name"):
            frappe.delete_doc("HD Ticket", t, force=True, ignore_permissions=True, delete_permanently=True)
        for c in frappe.get_all("HD Customer", filters={"name": ["like", "_test-standapi-%"]}, pluck="name"):
            frappe.delete_doc("HD Customer", c, force=True, ignore_permissions=True)
        frappe.db.commit()

    def test_resolves_the_erpnext_customer_from_the_ticket(self):
        from helpdesk.api.entitlement import get_ticket_standing

        with patch.object(standing, "fetch", return_value={"status": "ok"}) as f:
            get_ticket_standing(self.ticket)

        f.assert_called_once_with("_test-standapi-erp")

    def test_a_customer_with_no_erpnext_link_reports_unknown(self):
        from helpdesk.api.entitlement import get_ticket_standing

        frappe.db.set_value("HD Customer", self.customer, "erpnext_customer", "")
        frappe.db.commit()

        with patch.object(standing, "fetch") as f:
            out = get_ticket_standing(self.ticket)

        self.assertEqual(out["status"], "unknown")
        f.assert_not_called()

    def test_unknown_ticket_does_not_raise(self):
        from helpdesk.api.entitlement import get_ticket_standing

        out = get_ticket_standing("no-such-ticket")
        self.assertEqual(out["status"], "unknown")
