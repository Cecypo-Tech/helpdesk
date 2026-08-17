"""WhatsApp support must work on a site with no ERPNext installed.

Helpdesk and ERPNext run on separate benches, so production helpdesk has no
`erpnext` app. But `hooks.py` wires doc_events for `User Permission` and
`DocShare` — both *core Frappe* doctypes present on every site — to handlers in
`helpdesk.integrations.erpnext`. Those hooks fire regardless of whether ERPNext
exists. `ALLOWED_DOCTYPES` includes `HD Customer`, which also exists without
ERPNext, so the mirror path is genuinely reachable, not theoretical.

This bench HAS erpnext installed, so the rest of the suite can never exercise
the absent case. Simulating absence by patching `frappe.get_installed_apps`
globally does not work either: Frappe's own hook resolution calls it and raises
AppNotInstalledError for an app the site really does have.

So the guarantee is proved in two halves:

1. `should_sync()` returns False when erpnext is missing from the app list —
   tested directly, with the patch scoped to that one call so no framework
   machinery runs under it.
2. With sync inactive, every reachable path still works — HD Customer writes,
   User Permission on a mirrored doctype, and full WhatsApp ingestion.

Together those say: on a site without ERPNext, the integration is inert and
WhatsApp support is unaffected.
"""

import unittest
from unittest.mock import patch

import frappe

from helpdesk.integrations import wa_ingest
from helpdesk.integrations.erpnext.api import get_sync_info
from helpdesk.integrations.erpnext.utils import should_sync

PREFIX = "_test-noerp-"
NUMBER = "254700333444"


def _apps_without_erpnext():
    return [a for a in frappe.get_installed_apps() if a != "erpnext"]


def _fw_settings():
    return frappe._dict(
        enabled=1,
        unknown_contact_action="Create Contact and Ticket",
        new_conversation_timeout_hours=24,
        placeholder_email_domain="whatsapp.placeholder.local",
        customer_reply_status=None,
        agent_reply_status=None,
        default_ticket_type=None,
        default_team=None,
    )


def _cleanup():
    for ticket in frappe.get_all(
        "HD Ticket", filters={"subject": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc(
            "HD Ticket", ticket, force=True, ignore_permissions=True, delete_permanently=True
        )
    frappe.db.sql("DELETE FROM `tabWhatsApp Message` WHERE message_id LIKE %s", (PREFIX + "%",))
    for contact in frappe.get_all(
        "Contact", filters={"first_name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc(
            "Contact", contact, force=True, ignore_permissions=True, delete_permanently=True
        )
    for perm in frappe.get_all(
        "User Permission", filters={"for_value": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("User Permission", perm, force=True, ignore_permissions=True)
    for cust in frappe.get_all(
        "HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc(
            "HD Customer", cust, force=True, ignore_permissions=True, delete_permanently=True
        )
    frappe.db.commit()


class TestGuardDetectsMissingErpnext(unittest.TestCase):
    """Half one: the guard reads the app list correctly.

    The patch wraps only the guard call. Nothing that touches documents or hooks
    runs inside it, so Frappe's own use of get_installed_apps is unaffected.
    """

    def test_should_sync_false_when_erpnext_absent(self):
        with patch("frappe.get_installed_apps", return_value=_apps_without_erpnext()):
            self.assertFalse(should_sync())

    def test_should_sync_true_on_this_bench(self):
        """Control: proves the assertion above is caused by the missing app and
        not by the integration being disabled for some unrelated reason."""
        if "erpnext" not in frappe.get_installed_apps():
            self.skipTest("bench has no erpnext; the control is meaningless here")
        enabled = frappe.db.get_single_value("ERPNext HD Settings", "enabled")
        self.assertEqual(bool(should_sync()), bool(enabled))

    def test_sync_info_reports_disabled_instead_of_raising(self):
        """The settings UI calls this on page load. Without ERPNext it must
        return a disabled payload, not a 500. get_sync_info returns at its first
        guard, so no document machinery runs under the patch."""
        with patch("frappe.get_installed_apps", return_value=_apps_without_erpnext()):
            info = get_sync_info()
        self.assertFalse(info.get("enabled"))
        self.assertFalse(info.get("in_sync"))


class _SyncInactiveBase(unittest.TestCase):
    """Half two: with sync inactive, every reachable path still works.

    `should_sync` is patched at each module that imported it by name — a
    `from ... import should_sync` binding is not affected by patching the
    definition site.
    """

    def setUp(self):
        frappe.set_user("Administrator")
        _cleanup()
        for target in (
            "helpdesk.integrations.erpnext.mirror_sync.should_sync",
            "helpdesk.integrations.erpnext.utils.should_sync",
        ):
            p = patch(target, return_value=False)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        _cleanup()


class TestIntegrationIsInert(_SyncInactiveBase):
    def test_hd_customer_create_and_update_survive_the_hooks(self):
        """HD Customer is in ALLOWED_DOCTYPES, so it enters the mirror path."""
        doc = frappe.get_doc({
            "doctype": "HD Customer",
            "name": PREFIX + "cust",
            "customer_name": PREFIX + "cust",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

        doc.reload()
        doc.helpdesk_notes = "still fine"
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        self.assertTrue(frappe.db.exists("HD Customer", doc.name))
        self.assertFalse(
            frappe.db.get_value("HD Customer", doc.name, "erpnext_customer"),
            "no ERP link should be written when sync is inactive",
        )

    def test_user_permission_on_hd_customer_survives_the_hooks(self):
        """User Permission is a core doctype whose doc_events point into the
        ERPNext integration, and HD Customer is a mirrored doctype — exactly the
        combination that breaks if the guard is missing."""
        frappe.get_doc({
            "doctype": "HD Customer",
            "name": PREFIX + "perm",
            "customer_name": PREFIX + "perm",
        }).insert(ignore_permissions=True)

        perm = frappe.get_doc({
            "doctype": "User Permission",
            "user": "Administrator",
            "allow": "HD Customer",
            "for_value": PREFIX + "perm",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

        self.assertTrue(frappe.db.exists("User Permission", perm.name))


class TestWabaIngestionWithoutErpnext(_SyncInactiveBase):
    def setUp(self):
        super().setUp()
        p = patch("helpdesk.integrations.wa._fw_settings", return_value=_fw_settings())
        p.start()
        self.addCleanup(p.stop)

    def _insert(self, message_id):
        doc = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "type": "Incoming",
            "from": NUMBER,
            "message": PREFIX + "hello",
            "message_id": message_id,
            "content_type": "text",
            "profile_name": PREFIX + "Sender",
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc

    def test_inbound_message_still_becomes_a_ticket(self):
        """The headline guarantee: WABA support works with ERPNext inactive."""
        with patch("frappe.enqueue"):
            doc = self._insert(PREFIX + "link")

        wa_ingest.process_incoming_message(doc.name)

        ticket = frappe.db.get_value("WhatsApp Message", doc.name, "reference_name")
        self.assertTrue(ticket, "inbound WhatsApp message did not produce a ticket")
        self.assertEqual(
            frappe.db.get_value("WhatsApp Message", doc.name, "reference_doctype"),
            "HD Ticket",
        )

    def test_contact_is_created(self):
        with patch("frappe.enqueue"):
            doc = self._insert(PREFIX + "contact")

        wa_ingest.process_incoming_message(doc.name)

        ticket = frappe.db.get_value("WhatsApp Message", doc.name, "reference_name")
        self.assertTrue(frappe.db.get_value("HD Ticket", ticket, "contact"))

    def test_ingestion_is_still_idempotent(self):
        with patch("frappe.enqueue"):
            doc = self._insert(PREFIX + "idem")

        wa_ingest.process_incoming_message(doc.name)
        first = frappe.db.get_value("WhatsApp Message", doc.name, "reference_name")
        wa_ingest.process_incoming_message(doc.name)
        second = frappe.db.get_value("WhatsApp Message", doc.name, "reference_name")

        self.assertEqual(first, second)
