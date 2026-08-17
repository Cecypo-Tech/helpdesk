"""hd_product and support_status are Custom Fields, added via fixtures.

They are NOT added to hd_ticket.json: that file belongs to upstream and
editing it conflicts on every `git merge upstream/develop`. Same approach as
baileys_jid / baileys_line.
"""

import unittest

import frappe


class TestTicketProductFields(unittest.TestCase):
    def test_hd_product_custom_field_exists(self):
        self.assertTrue(
            frappe.db.exists("Custom Field", {"dt": "HD Ticket", "fieldname": "hd_product"})
        )

    def test_support_status_custom_field_exists(self):
        self.assertTrue(
            frappe.db.exists("Custom Field", {"dt": "HD Ticket", "fieldname": "support_status"})
        )

    def test_hd_product_links_to_hd_product(self):
        options = frappe.db.get_value(
            "Custom Field", {"dt": "HD Ticket", "fieldname": "hd_product"}, "options"
        )
        self.assertEqual(options, "HD Product")

    def test_support_status_offers_the_four_states_and_blank(self):
        options = frappe.db.get_value(
            "Custom Field", {"dt": "HD Ticket", "fieldname": "support_status"}, "options"
        )
        self.assertEqual(options, "\nCovered\nExpired\nNot Entitled\nUnknown")

    def test_support_status_is_read_only(self):
        """It is stamped by the system and frozen. An agent editing it by hand
        would destroy the historical record it exists to keep."""
        read_only = frappe.db.get_value(
            "Custom Field", {"dt": "HD Ticket", "fieldname": "support_status"}, "read_only"
        )
        self.assertEqual(read_only, 1)

    def test_upstream_product_select_is_untouched(self):
        """Upstream's dead HD Ticket.product Select must survive intact —
        repurposing it would conflict on every upstream merge."""
        meta = frappe.get_meta("HD Ticket")
        field = meta.get_field("product")
        self.assertIsNotNone(field, "upstream product field was removed")
        self.assertEqual(field.fieldtype, "Select")
