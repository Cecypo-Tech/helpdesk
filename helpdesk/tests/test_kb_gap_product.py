"""A recorded KB gap says which product it was about."""

import unittest
from unittest.mock import patch

import frappe

from helpdesk.integrations import bot

PREFIX = "_test-gap-"


def cleanup():
    for name in frappe.get_all(
        "HD Bot Missing KB Query", filters={"query_text": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc(
            "HD Bot Missing KB Query", name, force=True, ignore_permissions=True
        )
    for name in frappe.get_all(
        "HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class TestKbGapProduct(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        frappe.get_doc({"doctype": "HD Product", "product_name": PREFIX + "eTIMS"}).insert(
            ignore_permissions=True
        )
        frappe.db.commit()

    def tearDown(self):
        cleanup()

    def test_custom_field_exists(self):
        self.assertTrue(
            frappe.db.exists(
                "Custom Field", {"dt": "HD Bot Missing KB Query", "fieldname": "product"}
            )
        )

    def test_gap_records_the_resolved_product(self):
        with patch(
            "helpdesk.entitlement.resolve_product_for_ticket",
            return_value=PREFIX + "eTIMS",
        ):
            bot._record_gap(None, "WABA", PREFIX + "how do I file", "", "")
        frappe.db.commit()

        row = frappe.get_all(
            "HD Bot Missing KB Query",
            filters={"query_text": PREFIX + "how do I file"},
            fields=["product"],
        )
        self.assertTrue(row)
        self.assertEqual(row[0]["product"], PREFIX + "eTIMS")

    def test_gap_without_a_product_is_still_recorded(self):
        """An unresolvable product must never stop the gap being logged — the
        gap is the valuable part."""
        with patch("helpdesk.entitlement.resolve_product_for_ticket", return_value=None):
            bot._record_gap(None, "WABA", PREFIX + "unknown product", "", "")
        frappe.db.commit()

        row = frappe.get_all(
            "HD Bot Missing KB Query",
            filters={"query_text": PREFIX + "unknown product"},
            fields=["product"],
        )
        self.assertTrue(row, "gap was not recorded at all")
        self.assertFalse(row[0]["product"])
