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

    def test_unknown_ticket_returns_an_empty_payload(self):
        out = get_ticket_entitlement("no-such-ticket")
        self.assertEqual(out["status"], "Unknown")
        self.assertEqual(out["entitlements"], [])
