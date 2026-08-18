"""The snapshot/restore guard protects live credentials — so it gets a test.

Several test modules write to the Helpdesk Bot Settings singleton. Without a
correct restore those writes leak into the real site, including overwriting the
live API key in __Auth with a test value. This exercises the round trip.

The guard was rewritten to update rows in place rather than DELETE-all-then-
INSERT-all, because the suite runs against a live site whose scheduler and
worker touch the same tables and the old pattern deadlocked against them.
These tests pin the behaviour that rewrite had to preserve.
"""

import unittest

import frappe

from helpdesk.tests.settings_guard import restore_bot_settings, snapshot_bot_settings

DOCTYPE = "Helpdesk Bot Settings"


def singles_rows():
    return dict(
        frappe.db.sql("SELECT field, value FROM `tabSingles` WHERE doctype=%s", (DOCTYPE,))
    )


class TestSettingsGuard(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.snapshot = snapshot_bot_settings()
        self.addCleanup(lambda: restore_bot_settings(self.snapshot))

    def test_restore_returns_a_changed_value(self):
        before = singles_rows()
        frappe.db.sql(
            "UPDATE `tabSingles` SET value=%s WHERE doctype=%s AND field=%s",
            ("_test-tampered", DOCTYPE, "llm_provider"),
        )
        frappe.db.commit()

        restore_bot_settings(self.snapshot)
        self.assertEqual(singles_rows().get("llm_provider"), before.get("llm_provider"))

    def test_restore_removes_a_field_a_test_introduced(self):
        """A test that sets a field absent from the snapshot must not leave it
        behind — that is how state leaks between modules."""
        frappe.db.sql(
            "INSERT INTO `tabSingles` (doctype, field, value) VALUES (%s, %s, %s)",
            (DOCTYPE, "_test_stray_field", "1"),
        )
        frappe.db.commit()

        restore_bot_settings(self.snapshot)
        self.assertNotIn("_test_stray_field", singles_rows())

    def test_restore_reinstates_a_deleted_field(self):
        frappe.db.sql(
            "DELETE FROM `tabSingles` WHERE doctype=%s AND field=%s",
            (DOCTYPE, "llm_provider"),
        )
        frappe.db.commit()

        restore_bot_settings(self.snapshot)
        self.assertIn("llm_provider", singles_rows())

    def test_restore_preserves_the_api_key(self):
        """The failure this guard exists to prevent: clobbering the live key."""
        before = frappe.db.sql(
            "SELECT fieldname, password FROM `__Auth` WHERE doctype=%s AND name=%s",
            (DOCTYPE, DOCTYPE),
            as_dict=True,
        )
        if not before:
            self.skipTest("no stored credential on this site")

        frappe.db.sql(
            "UPDATE `__Auth` SET password=%s WHERE doctype=%s AND name=%s",
            ("_test-clobbered", DOCTYPE, DOCTYPE),
        )
        frappe.db.commit()

        restore_bot_settings(self.snapshot)
        after = frappe.db.sql(
            "SELECT fieldname, password FROM `__Auth` WHERE doctype=%s AND name=%s",
            (DOCTYPE, DOCTYPE),
            as_dict=True,
        )
        self.assertEqual(
            {r.fieldname: r.password for r in after},
            {r.fieldname: r.password for r in before},
        )

    def test_restore_is_idempotent(self):
        restore_bot_settings(self.snapshot)
        once = singles_rows()
        restore_bot_settings(self.snapshot)
        self.assertEqual(singles_rows(), once)
