"""hd_product and support_status are Custom Fields, added via fixtures.

They are NOT added to hd_ticket.json: that file belongs to upstream and
editing it conflicts on every `git merge upstream/develop`. Same approach as
baileys_jid / baileys_line.
"""

import json
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

    def test_fixture_file_contains_all_required_custom_fields(self):
        """Verify that helpdesk/fixtures/custom_field.json contains all required
        custom fields. This guards against regression where export operations might
        drop critical fields like baileys_jid/baileys_line (which break WA Line
        on fresh installs) or the new product entitlement fields."""
        import os

        fixture_path = os.path.join(
            frappe.get_app_path("helpdesk"), "fixtures", "custom_field.json"
        )
        with open(fixture_path, "r") as f:
            fixtures = json.load(f)

        # Extract (dt, fieldname) tuples from the fixture
        fixture_fields = {(f["dt"], f["fieldname"]) for f in fixtures}

        # These 5 fields are critical and must be in the fixture file
        required_fields = {
            ("Customer", "helpdesk_notes"),
            ("HD Ticket", "baileys_jid"),
            ("HD Ticket", "baileys_line"),
            ("HD Ticket", "hd_product"),
            ("HD Ticket", "support_status"),
        }

        missing = required_fields - fixture_fields
        self.assertFalse(
            missing,
            f"Fixture file missing required custom fields: {missing}. "
            f"This would break WhatsApp WA Line on fresh installs.",
        )
