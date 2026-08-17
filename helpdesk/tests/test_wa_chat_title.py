"""Chat titles must come from the customer, never from whoever spoke last.

Outgoing WA Message rows carry a sender_name describing our own side: the
agent's full name for replies sent from the desk, and the literal "(via phone)"
for messages mirrored from the line's handset. Using the latest message
regardless of direction titled unnamed chats "(via phone)" or with an agent's
name — both of which read as a contact name in the conversation list.
"""

import unittest

import frappe

from helpdesk.integrations.wa import (
    _latest_incoming_names,
    get_contact_info_for_jid,
    get_wa_conversations,
)

TEST_LINE = "_test-title-line"
JID = "254700009001@s.whatsapp.net"


class TestChatTitleDirection(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self._purge()
        if not frappe.db.exists("WA Line", TEST_LINE):
            frappe.get_doc({
                "doctype": "WA Line",
                "instance_name": TEST_LINE,
                "instance_token": "_test-token",
            }).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        self._purge()

    def _purge(self):
        frappe.db.delete("WA Message", {"jid": JID})
        frappe.db.delete("WA Contact", {"jid": JID})
        frappe.db.commit()

    def _msg(self, direction, sender_name="", profile_name="", text="hi"):
        doc = frappe.get_doc({
            "doctype": "WA Message",
            "direction": direction,
            "jid": JID,
            "sender_jid": JID if direction == "Incoming" else "",
            "sender_name": sender_name,
            "profile_name": profile_name,
            "message": text,
            "content_type": "text",
            "status": "Delivered",
            "line": TEST_LINE,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        return doc

    def _title(self):
        convos = get_wa_conversations(line=TEST_LINE, limit=50)["conversations"]
        row = next((c for c in convos if c["jid"] == JID), None)
        self.assertIsNotNone(row, "test conversation missing from the list")
        return row["display_name"]

    # ── the reported bug ────────────────────────────────────────────────────

    def test_phone_mirrored_reply_does_not_become_the_title(self):
        self._msg("Incoming", profile_name="")
        self._msg("Outgoing", sender_name="(via phone)", text="Dear ANDREW, your quota…")
        self.assertNotEqual(self._title(), "(via phone)")

    def test_agent_name_does_not_become_the_title(self):
        self._msg("Incoming", profile_name="")
        self._msg("Outgoing", sender_name="Kushal Agent", text="on it")
        self.assertNotEqual(self._title(), "Kushal Agent")

    def test_unnamed_chat_falls_back_to_the_number(self):
        self._msg("Incoming", profile_name="")
        self._msg("Outgoing", sender_name="(via phone)")
        self.assertIn("254700009001", self._title())

    # ── the fallback still works when a real name exists ────────────────────

    def test_incoming_name_is_still_used(self):
        self._msg("Incoming", sender_name="Mary W", profile_name="Mary W")
        self.assertEqual(self._title(), "Mary W")

    def test_incoming_name_survives_a_later_outgoing_message(self):
        self._msg("Incoming", sender_name="Mary W", profile_name="Mary W")
        self._msg("Outgoing", sender_name="(via phone)", text="thanks")
        self.assertEqual(self._title(), "Mary W")

    def test_contact_custom_name_still_wins(self):
        frappe.get_doc({
            "doctype": "WA Contact",
            "jid": JID,
            "phone": "254700009001",
            "custom_name": "Jenga Taifa Hardware",
            "company": "",
            "assigned_team": "",
        }).insert(ignore_permissions=True)
        self._msg("Incoming", sender_name="Mary W", profile_name="Mary W")
        frappe.db.commit()
        self.assertEqual(self._title(), "Jenga Taifa Hardware")

    # ── a blank recent message must not bury an older name ──────────────────

    def test_recent_nameless_message_does_not_bury_an_earlier_name(self):
        self._msg("Incoming", sender_name="Mary W", profile_name="Mary W")
        self._msg("Incoming", sender_name="", profile_name="")
        self.assertEqual(_latest_incoming_names([JID]).get(JID), "Mary W")
        self.assertEqual(self._title(), "Mary W")

    def test_header_lookup_agrees_with_the_list(self):
        self._msg("Incoming", sender_name="Mary W", profile_name="Mary W")
        self._msg("Outgoing", sender_name="(via phone)")
        self.assertEqual(get_contact_info_for_jid(JID)["display_name"], "Mary W")

    def test_header_lookup_ignores_outgoing_names(self):
        self._msg("Incoming", profile_name="")
        self._msg("Outgoing", sender_name="(via phone)")
        self.assertNotEqual(get_contact_info_for_jid(JID)["display_name"], "(via phone)")

    # ── helper contract ─────────────────────────────────────────────────────

    def test_helper_ignores_outgoing_rows_entirely(self):
        self._msg("Outgoing", sender_name="(via phone)")
        self._msg("Outgoing", sender_name="Kushal Agent")
        self.assertEqual(_latest_incoming_names([JID]), {})

    def test_helper_handles_empty_input(self):
        self.assertEqual(_latest_incoming_names([]), {})
