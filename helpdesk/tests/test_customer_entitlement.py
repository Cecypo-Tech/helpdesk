"""One HD Customer Product row per (customer, product).

Standalone rather than a child table on HD Customer: sub-project B syncs
entitlements from ERPNext and POS licensing, and an idempotent upsert on a
unique key is far safer than reconciling child rows by idx.
"""

import unittest

import frappe

PREFIX = "_test-ent-"
CUSTOMER = PREFIX + "cust"
PRODUCT = PREFIX + "prod"


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


class TestCustomerEntitlement(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        frappe.get_doc({
            "doctype": "HD Customer", "name": CUSTOMER, "customer_name": CUSTOMER
        }).insert(ignore_permissions=True)
        frappe.get_doc({
            "doctype": "HD Product", "product_name": PRODUCT
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        cleanup()

    def make(self, **kwargs):
        payload = {
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": PRODUCT,
        }
        payload.update(kwargs)
        doc = frappe.get_doc(payload).insert(ignore_permissions=True)
        frappe.db.commit()
        return doc

    def test_entitlement_defaults_to_manual_source(self):
        doc = self.make()
        self.assertEqual(doc.source, "Manual")

    def test_blank_expiry_is_allowed(self):
        doc = self.make()
        self.assertFalse(doc.support_expiry)

    def test_duplicate_customer_product_is_rejected(self):
        """The unique index is what makes sub-project B's upsert safe."""
        self.make()
        with self.assertRaises(Exception):
            self.make()

    def test_same_product_for_a_different_customer_is_allowed(self):
        self.make()
        other = frappe.get_doc({
            "doctype": "HD Customer",
            "name": PREFIX + "cust2",
            "customer_name": PREFIX + "cust2",
        }).insert(ignore_permissions=True)
        doc = frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": other.name,
            "product": PRODUCT,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        self.assertTrue(doc.name)
        frappe.delete_doc("HD Customer Product", doc.name, force=True, ignore_permissions=True)
        frappe.delete_doc("HD Customer", other.name, force=True, ignore_permissions=True)
        frappe.db.commit()

    def test_unique_index_exists(self):
        rows = frappe.db.sql(
            "SHOW INDEX FROM `tabHD Customer Product` WHERE Key_name = %s",
            "hd_customer_product_customer_product",
            as_dict=True,
        )
        self.assertTrue(rows, "composite unique index was not created")
        self.assertEqual(rows[0]["Non_unique"], 0)
