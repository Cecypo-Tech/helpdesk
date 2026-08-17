"""Entitlement resolution. Advisory only — nothing here refuses service."""

import unittest

import frappe
from frappe.utils import add_days, today

from helpdesk import entitlement

PREFIX = "_test-entl-"
CUSTOMER = PREFIX + "cust"
PRODUCT_A = PREFIX + "prodA"
PRODUCT_B = PREFIX + "prodB"


def cleanup():
    for name in frappe.get_all(
        "HD Customer Product", filters={"customer": CUSTOMER}, pluck="name"
    ):
        frappe.delete_doc("HD Customer Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Customer", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class _Base(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        frappe.get_doc({
            "doctype": "HD Customer", "name": CUSTOMER, "customer_name": CUSTOMER
        }).insert(ignore_permissions=True)
        for p in (PRODUCT_A, PRODUCT_B):
            frappe.get_doc({"doctype": "HD Product", "product_name": p}).insert(
                ignore_permissions=True
            )
        frappe.db.commit()

    def tearDown(self):
        cleanup()

    def entitle(self, product, expiry=None):
        doc = frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": product,
            "support_expiry": expiry,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        return doc


class TestIsExpired(unittest.TestCase):
    def test_blank_expiry_is_never_expired(self):
        """Blank means permanently covered, not expired. Getting this backwards
        would mark every open-ended entitlement as out of contract."""
        self.assertFalse(entitlement.is_expired(None))
        self.assertFalse(entitlement.is_expired(""))

    def test_yesterday_is_expired(self):
        self.assertTrue(entitlement.is_expired(add_days(today(), -1)))

    def test_today_is_not_expired(self):
        """Support lasts through the whole expiry day."""
        self.assertFalse(entitlement.is_expired(today()))

    def test_tomorrow_is_not_expired(self):
        self.assertFalse(entitlement.is_expired(add_days(today(), 1)))


class TestSupportStatus(_Base):
    def test_no_customer_is_unknown(self):
        self.assertEqual(entitlement.compute_support_status(None, PRODUCT_A), "Unknown")

    def test_no_product_is_unknown(self):
        self.assertEqual(entitlement.compute_support_status(CUSTOMER, None), "Unknown")

    def test_entitled_with_no_expiry_is_covered(self):
        self.entitle(PRODUCT_A)
        self.assertEqual(entitlement.compute_support_status(CUSTOMER, PRODUCT_A), "Covered")

    def test_entitled_with_future_expiry_is_covered(self):
        self.entitle(PRODUCT_A, add_days(today(), 30))
        self.assertEqual(entitlement.compute_support_status(CUSTOMER, PRODUCT_A), "Covered")

    def test_entitled_with_past_expiry_is_expired(self):
        self.entitle(PRODUCT_A, add_days(today(), -1))
        self.assertEqual(entitlement.compute_support_status(CUSTOMER, PRODUCT_A), "Expired")

    def test_unentitled_product_is_not_entitled(self):
        self.entitle(PRODUCT_A)
        self.assertEqual(
            entitlement.compute_support_status(CUSTOMER, PRODUCT_B), "Not Entitled"
        )


class TestGetEntitlements(_Base):
    def test_returns_all_products_with_expiry_flag(self):
        self.entitle(PRODUCT_A)
        self.entitle(PRODUCT_B, add_days(today(), -5))
        rows = entitlement.get_entitlements(CUSTOMER)
        by_product = {r["product"]: r for r in rows}
        self.assertEqual(len(rows), 2)
        self.assertFalse(by_product[PRODUCT_A]["expired"])
        self.assertTrue(by_product[PRODUCT_B]["expired"])

    def test_no_customer_returns_empty(self):
        self.assertEqual(entitlement.get_entitlements(None), [])
        self.assertEqual(entitlement.get_entitlements(""), [])


class TestResolveProductForTicket(_Base):
    def make_ticket(self, product=None):
        doc = frappe.get_doc({
            "doctype": "HD Ticket",
            "subject": PREFIX + "ticket",
            "description": "x",
            "customer": CUSTOMER,
            "hd_product": product,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        self.addCleanup(
            frappe.delete_doc, "HD Ticket", doc.name, force=True, ignore_permissions=True
        )
        return doc

    def test_explicit_ticket_product_wins(self):
        self.entitle(PRODUCT_A)
        self.entitle(PRODUCT_B)
        ticket = self.make_ticket(product=PRODUCT_B)
        self.assertEqual(entitlement.resolve_product_for_ticket(ticket.name), PRODUCT_B)

    def test_sole_entitlement_is_inferred(self):
        """A customer who owns only eTIMS gets correctly scoped answers before
        any agent touches the ticket."""
        self.entitle(PRODUCT_A)
        ticket = self.make_ticket()
        self.assertEqual(entitlement.resolve_product_for_ticket(ticket.name), PRODUCT_A)

    def test_multiple_entitlements_are_not_guessed(self):
        self.entitle(PRODUCT_A)
        self.entitle(PRODUCT_B)
        ticket = self.make_ticket()
        self.assertIsNone(entitlement.resolve_product_for_ticket(ticket.name))

    def test_missing_ticket_returns_none(self):
        self.assertIsNone(entitlement.resolve_product_for_ticket("no-such-ticket"))
