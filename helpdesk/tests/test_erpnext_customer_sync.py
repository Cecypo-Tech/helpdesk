"""Mirror ERPNext customers into HD Customer.

Ownership is the point of this module. ERPNext owns identity fields (tax_id,
territory, customer_group); Helpdesk owns everything an agent types
(helpdesk_notes, domain, image). A sync that overwrote the latter would destroy
work, so the tests below pin that boundary as hard as they pin the happy path.

HD Customer is autonamed by customer_name, so the docname IS the name. The sync
therefore never renames: a rename would cascade through every ticket and
entitlement pointing at it.
"""

import unittest
from unittest.mock import patch

import frappe

from helpdesk.integrations import erpnext_sync

PREFIX = "_test-erpsync-"


def payload(customers, has_more=False):
    return {
        "ok": True,
        "error": None,
        "data": {
            "version": 1,
            "synced_at": "2026-08-18 09:00:00",
            "count": len(customers),
            "has_more": has_more,
            "customers": customers,
        },
    }


def customer(name, **kw):
    base = {
        "customer": name,
        "customer_name": name,
        "tax_id": None,
        "territory": None,
        "customer_group": None,
        "currency": "KES",
        "modified": "2026-08-18 08:00:00",
        "contacts": [],
    }
    base.update(kw)
    return base


def cleanup():
    for name in frappe.get_all(
        "HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Customer", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class _Base(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        p = patch.object(erpnext_sync, "is_configured", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        cleanup()

    def hd(self, name, **kw):
        doc = frappe.get_doc(dict({"doctype": "HD Customer", "customer_name": name}, **kw))
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc


class TestCreate(_Base):
    def test_creates_a_customer_that_does_not_exist(self):
        row = customer(PREFIX + "acme", tax_id="P051234567X", territory="Nairobi")
        with patch.object(erpnext_sync, "fetch_page", side_effect=[payload([row])]):
            result = erpnext_sync.sync_customers()

        self.assertTrue(result["ok"])
        self.assertEqual(result["created"], 1)
        doc = frappe.get_doc("HD Customer", PREFIX + "acme")
        self.assertEqual(doc.erpnext_customer, PREFIX + "acme")
        self.assertEqual(doc.tax_id, "P051234567X")
        self.assertEqual(doc.territory, "Nairobi")


class TestMatching(_Base):
    def test_matches_an_already_linked_customer_and_updates_it(self):
        self.hd(PREFIX + "linked", erpnext_customer=PREFIX + "erp-linked")
        row = customer(PREFIX + "erp-linked", customer_name="Renamed In ERP", tax_id="A1")

        with patch.object(erpnext_sync, "fetch_page", side_effect=[payload([row])]):
            erpnext_sync.sync_customers()

        doc = frappe.get_doc("HD Customer", PREFIX + "linked")
        self.assertEqual(doc.tax_id, "A1")

    def test_adopts_an_unlinked_customer_matched_by_tax_id(self):
        """A customer typed in by hand, then recognised by PIN."""
        self.hd(PREFIX + "bypin", tax_id="P0999")
        row = customer(PREFIX + "erp-bypin", customer_name="Other Name", tax_id="P0999")

        with patch.object(erpnext_sync, "fetch_page", side_effect=[payload([row])]):
            result = erpnext_sync.sync_customers()

        doc = frappe.get_doc("HD Customer", PREFIX + "bypin")
        self.assertEqual(doc.erpnext_customer, PREFIX + "erp-bypin")
        self.assertEqual(result["created"], 0)

    def test_adopts_an_unlinked_customer_matched_by_exact_name(self):
        self.hd(PREFIX + "byname")
        row = customer(PREFIX + "byname")

        with patch.object(erpnext_sync, "fetch_page", side_effect=[payload([row])]):
            result = erpnext_sync.sync_customers()

        doc = frappe.get_doc("HD Customer", PREFIX + "byname")
        self.assertEqual(doc.erpnext_customer, PREFIX + "byname")
        self.assertEqual(result["created"], 0)

    def test_does_not_match_on_a_near_name(self):
        """Never fuzzy. Merging two real customers is far worse than creating
        a duplicate somebody can merge deliberately."""
        self.hd(PREFIX + "acme ltd")
        row = customer(PREFIX + "acme limited")

        with patch.object(erpnext_sync, "fetch_page", side_effect=[payload([row])]):
            result = erpnext_sync.sync_customers()

        self.assertEqual(result["created"], 1)
        self.assertTrue(frappe.db.exists("HD Customer", PREFIX + "acme limited"))


class TestOwnership(_Base):
    def test_never_renames_an_existing_customer(self):
        """HD Customer is autonamed by customer_name, so renaming would cascade
        through every ticket and entitlement pointing at it."""
        self.hd(PREFIX + "stable", erpnext_customer=PREFIX + "erp-stable")
        row = customer(PREFIX + "erp-stable", customer_name="Completely Different")

        with patch.object(erpnext_sync, "fetch_page", side_effect=[payload([row])]):
            erpnext_sync.sync_customers()

        self.assertTrue(frappe.db.exists("HD Customer", PREFIX + "stable"))
        self.assertFalse(frappe.db.exists("HD Customer", "Completely Different"))

    def test_never_overwrites_helpdesk_owned_fields(self):
        """helpdesk_notes and domain are what an agent typed. A mirror must not
        destroy them."""
        self.hd(
            PREFIX + "owned",
            erpnext_customer=PREFIX + "erp-owned",
            helpdesk_notes="Call Andrew before escalating",
            domain="acme.co.ke",
        )
        row = customer(PREFIX + "erp-owned", tax_id="A2")

        with patch.object(erpnext_sync, "fetch_page", side_effect=[payload([row])]):
            erpnext_sync.sync_customers()

        doc = frappe.get_doc("HD Customer", PREFIX + "owned")
        self.assertEqual(doc.helpdesk_notes, "Call Andrew before escalating")
        self.assertEqual(doc.domain, "acme.co.ke")
        self.assertEqual(doc.tax_id, "A2")


class TestFailureAndIdempotency(_Base):
    def test_unreachable_erpnext_fails_open_and_changes_nothing(self):
        self.hd(PREFIX + "untouched", tax_id="KEEP")
        failure = {"ok": False, "data": None, "error": "connection refused"}

        with patch.object(erpnext_sync, "fetch_page", side_effect=[failure]):
            result = erpnext_sync.sync_customers()

        self.assertFalse(result["ok"])
        self.assertIn("connection refused", result["error"])
        self.assertEqual(
            frappe.db.get_value("HD Customer", PREFIX + "untouched", "tax_id"), "KEEP"
        )

    def test_unconfigured_is_a_no_op(self):
        with patch.object(erpnext_sync, "is_configured", return_value=False), \
             patch.object(erpnext_sync, "fetch_page") as fetch:
            result = erpnext_sync.sync_customers()

        self.assertFalse(result["ok"])
        fetch.assert_not_called()

    def test_running_twice_changes_nothing_the_second_time(self):
        row = customer(PREFIX + "idem", tax_id="I1")
        with patch.object(erpnext_sync, "fetch_page", side_effect=[payload([row])]):
            first = erpnext_sync.sync_customers()
        with patch.object(erpnext_sync, "fetch_page", side_effect=[payload([row])]):
            second = erpnext_sync.sync_customers()

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 0)

    def test_pages_until_has_more_is_false(self):
        a = customer(PREFIX + "page-a")
        b = customer(PREFIX + "page-b")
        with patch.object(
            erpnext_sync, "fetch_page",
            side_effect=[payload([a], has_more=True), payload([b], has_more=False)],
        ) as fetch:
            result = erpnext_sync.sync_customers()

        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["created"], 2)
