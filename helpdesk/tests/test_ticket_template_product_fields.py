"""hd_product and support_status appear in the agent ticket sidebar.

No Vue work is needed for this: the sidebar's "Additional Fields" section is
driven by HD Ticket Template Field rows (hd_ticket/api.py get_ticket_customizations),
and TicketField.vue already renders a Link fieldtype as a real doctype picker.
Adding the rows is the whole feature.

Deliberately a patch rather than a fixture. Frappe fixtures OVERWRITE on import,
and the ticket template is user-editable config — shipping it as a fixture would
silently wipe any fields an admin added on the next migrate.
"""

import unittest

import frappe

from helpdesk.patches.add_product_fields_to_ticket_template import execute

TEMPLATE = "Default"


def rows():
    return frappe.get_all(
        "HD Ticket Template Field",
        filters={"parent": TEMPLATE, "parenttype": "HD Ticket Template"},
        fields=["fieldname", "idx"],
        order_by="idx",
    )


class TestTicketTemplateProductFields(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")

    def test_both_fields_are_on_the_default_template(self):
        names = [r["fieldname"] for r in rows()]
        self.assertIn("hd_product", names)
        self.assertIn("support_status", names)

    def test_patch_is_idempotent(self):
        """bench migrate re-runs patches; a second run must not duplicate rows."""
        before = [r["fieldname"] for r in rows()]
        execute()
        frappe.db.commit()
        after = [r["fieldname"] for r in rows()]
        self.assertEqual(before, after)
        self.assertEqual(after.count("hd_product"), 1)
        self.assertEqual(after.count("support_status"), 1)

    def test_patch_preserves_pre_existing_rows(self):
        """An admin's own template fields must survive the patch — the reason
        this is not shipped as a fixture. Uses a real HD Ticket field because
        HD Ticket Template rejects fieldnames that do not exist on the doctype."""
        doc = frappe.get_doc("HD Ticket Template", TEMPLATE)
        doc.append("fields", {"fieldname": "raised_by"})
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        self.addCleanup(self._drop_admin_field)

        execute()
        frappe.db.commit()

        names = [r["fieldname"] for r in rows()]
        self.assertIn("raised_by", names)
        self.assertIn("hd_product", names)

    def _drop_admin_field(self):
        doc = frappe.get_doc("HD Ticket Template", TEMPLATE)
        doc.fields = [f for f in doc.fields if f.fieldname != "raised_by"]
        doc.save(ignore_permissions=True)
        frappe.db.commit()
