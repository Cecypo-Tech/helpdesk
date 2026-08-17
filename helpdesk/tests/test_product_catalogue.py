"""HD Product is a flat catalogue of what we sell, 5-15 rows.

Named by product_name so links read as "eTIMS" rather than a hash.
"""

import unittest

import frappe

PREFIX = "_test-prod-"


def cleanup():
    for name in frappe.get_all(
        "HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class TestProductCatalogue(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()

    def tearDown(self):
        cleanup()

    def test_product_is_named_by_product_name(self):
        doc = frappe.get_doc({
            "doctype": "HD Product",
            "product_name": PREFIX + "eTIMS",
        }).insert(ignore_permissions=True)
        self.assertEqual(doc.name, PREFIX + "eTIMS")

    def test_product_name_is_unique(self):
        frappe.get_doc({
            "doctype": "HD Product",
            "product_name": PREFIX + "POS",
        }).insert(ignore_permissions=True)
        with self.assertRaises(frappe.DuplicateEntryError):
            frappe.get_doc({
                "doctype": "HD Product",
                "product_name": PREFIX + "POS",
            }).insert(ignore_permissions=True)

    def test_disabled_defaults_to_zero(self):
        doc = frappe.get_doc({
            "doctype": "HD Product",
            "product_name": PREFIX + "Frappe",
        }).insert(ignore_permissions=True)
        self.assertEqual(doc.disabled, 0)
