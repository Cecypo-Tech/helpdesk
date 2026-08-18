"""Agent-facing entitlement payload.

The badge is computed live and answers "is this customer covered right now",
which is a different question from HD Ticket.support_status — that is a frozen
record of coverage when the ticket was raised. Both exist on purpose.
"""

import unittest

import frappe
from frappe.utils import add_days, today

from helpdesk.api.entitlement import get_ticket_entitlement

PREFIX = "_test-entapi-"
CUSTOMER = PREFIX + "cust"
PRODUCT = PREFIX + "prod"


def cleanup():
    for name in frappe.get_all(
        "HD Ticket", filters={"subject": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Ticket", name, force=True, ignore_permissions=True,
                          delete_permanently=True)
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


class TestEntitlementApi(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        frappe.get_doc({
            "doctype": "HD Customer", "name": CUSTOMER, "customer_name": CUSTOMER
        }).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "HD Product", "product_name": PRODUCT}).insert(
            ignore_permissions=True
        )
        frappe.db.commit()

    def tearDown(self):
        cleanup()

    def ticket(self, **kwargs):
        payload = {
            "doctype": "HD Ticket",
            "subject": PREFIX + "t",
            "description": "x",
            "customer": CUSTOMER,
        }
        payload.update(kwargs)
        doc = frappe.get_doc(payload).insert(ignore_permissions=True)
        frappe.db.commit()
        return doc

    def test_returns_covered_for_an_entitled_product(self):
        frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": PRODUCT,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        doc = self.ticket(hd_product=PRODUCT)

        out = get_ticket_entitlement(doc.name)
        self.assertEqual(out["status"], "Covered")
        self.assertEqual(out["product"], PRODUCT)
        self.assertEqual(out["customer"], CUSTOMER)

    def test_badge_is_live_while_stamped_status_is_frozen(self):
        """Renewing changes the live badge but must not change the stamp."""
        frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": PRODUCT,
            "support_expiry": add_days(today(), -1),
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        doc = self.ticket(hd_product=PRODUCT)
        self.assertEqual(doc.support_status, "Expired")

        row = frappe.get_all(
            "HD Customer Product",
            filters={"customer": CUSTOMER, "product": PRODUCT},
            pluck="name",
        )[0]
        frappe.db.set_value("HD Customer Product", row, "support_expiry", add_days(today(), 30))
        frappe.db.commit()

        out = get_ticket_entitlement(doc.name)
        self.assertEqual(out["status"], "Covered", "badge must be live")
        doc.reload()
        self.assertEqual(doc.support_status, "Expired", "stamp must stay frozen")

    def test_lists_every_product_the_customer_holds(self):
        frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": PRODUCT,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        doc = self.ticket()

        out = get_ticket_entitlement(doc.name)
        self.assertEqual([e["product"] for e in out["entitlements"]], [PRODUCT])

    def test_every_entitlement_row_carries_a_status(self):
        """The panel lists every product the company holds, each with its own
        status. Deriving Covered/Expired in JavaScript instead would duplicate
        coverage logic in a second language — the drift helpdesk/entitlement.py
        exists to prevent."""
        frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": PRODUCT,
        }).insert(ignore_permissions=True)
        other = frappe.get_doc({
            "doctype": "HD Product", "product_name": PREFIX + "lapsed"
        }).insert(ignore_permissions=True)
        frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": other.name,
            "support_expiry": add_days(today(), -1),
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        doc = self.ticket()

        rows = {r["product"]: r for r in get_ticket_entitlement(doc.name)["entitlements"]}
        self.assertEqual(rows[PRODUCT]["status"], "Covered")
        self.assertEqual(rows[other.name]["status"], "Expired")

    def test_entitlements_are_returned_even_with_no_ticket_product(self):
        """The panel is gated on the customer, not the ticket's product — a
        company holding three products must not render blank just because
        nobody tagged the ticket."""
        second = frappe.get_doc({
            "doctype": "HD Product", "product_name": PREFIX + "second"
        }).insert(ignore_permissions=True)
        for prod in (PRODUCT, second.name):
            frappe.get_doc({
                "doctype": "HD Customer Product",
                "customer": CUSTOMER,
                "product": prod,
            }).insert(ignore_permissions=True)
        frappe.db.commit()
        doc = self.ticket()

        out = get_ticket_entitlement(doc.name)
        # Two entitlements, so the single-entitlement inference deliberately
        # does not fire and no product resolves — the panel must still list both.
        self.assertIsNone(out["product"])
        self.assertEqual(len(out["entitlements"]), 2)
        self.assertEqual(out["customer"], CUSTOMER)

    def test_customer_with_no_entitlements_still_reports_the_customer(self):
        """A known customer holding nothing is information, not an empty panel."""
        doc = self.ticket()
        out = get_ticket_entitlement(doc.name)
        self.assertEqual(out["customer"], CUSTOMER)
        self.assertEqual(out["entitlements"], [])

    def test_unknown_ticket_returns_an_empty_payload(self):
        out = get_ticket_entitlement("no-such-ticket")
        self.assertEqual(out["status"], "Unknown")
        self.assertIsNone(out["stamped_status"])
        self.assertEqual(out["entitlements"], [])

    def test_non_agent_is_refused(self):
        """A logged-in portal contact must not be able to read another
        customer's entitlement data (name, product, expiry, source) by
        POSTing an arbitrary ticket id."""
        frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": PRODUCT,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        doc = self.ticket(hd_product=PRODUCT)

        # No roles assigned -> Frappe defaults User.user_type to "Website
        # User" (a portal contact), not "System User". That's the profile a
        # logged-in portal contact actually has - no Agent/Agent Manager role
        # and no HD Agent record, which is exactly what is_agent() checks.
        user_email = PREFIX + "portal@example.com"
        if not frappe.db.exists("User", user_email):
            frappe.get_doc({
                "doctype": "User",
                "email": user_email,
                "first_name": "Portal",
                "send_welcome_email": 0,
            }).insert(ignore_permissions=True)
            frappe.db.commit()

        def _delete_test_user():
            frappe.set_user("Administrator")
            if frappe.db.exists("User", user_email):
                frappe.delete_doc("User", user_email, force=True, ignore_permissions=True)
                frappe.db.commit()

        self.addCleanup(_delete_test_user)
        self.addCleanup(lambda: frappe.set_user("Administrator"))

        frappe.set_user(user_email)
        self.assertRaises(frappe.PermissionError, get_ticket_entitlement, doc.name)
