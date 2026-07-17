import frappe
import unittest

from helpdesk.integrations.wa import get_wa_message_by_message_id

TEST_JID = "_test-byid@s.whatsapp.net"
OTHER_JID = "_test-byid-other@s.whatsapp.net"


class TestGetWaMessageByMessageId(unittest.TestCase):
    """On-demand resolution of a reply's quoted target by (message_id, jid)."""

    def setUp(self):
        frappe.set_user("Administrator")
        self._purge()
        self._msg(message_id="_test-byid-01", text="the quoted message", jid=TEST_JID)
        # Same message_id under a different conversation — must not leak across jids.
        self._msg(message_id="_test-byid-cross", text="other convo", jid=OTHER_JID)
        frappe.db.commit()

    def tearDown(self):
        self._purge()

    def _purge(self):
        for j in (TEST_JID, OTHER_JID):
            frappe.db.delete("WA Message", {"jid": j})
        frappe.db.commit()

    def _msg(self, message_id, text, jid):
        return frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Incoming",
            "jid": jid,
            "message": text,
            "content_type": "text",
            "message_id": message_id,
            "status": "Delivered",
        }).insert(ignore_permissions=True).name

    def test_found_returns_finalized_row(self):
        row = get_wa_message_by_message_id(message_id="_test-byid-01", jid=TEST_JID)
        self.assertIsNotNone(row)
        self.assertEqual(row["message_id"], "_test-byid-01")
        self.assertEqual(row["message"], "the quoted message")
        # _finalize_wa_rows contract: client-facing fields present.
        self.assertIn(row["type"], ("Incoming", "Outgoing"))
        self.assertIn("attach", row)
        self.assertIn("is_reply", row)

    def test_unknown_message_id_returns_none(self):
        self.assertIsNone(
            get_wa_message_by_message_id(message_id="_test-byid-missing", jid=TEST_JID)
        )

    def test_wrong_jid_returns_none(self):
        # message_id exists, but not in this conversation.
        self.assertIsNone(
            get_wa_message_by_message_id(message_id="_test-byid-01", jid=OTHER_JID)
        )

    def test_missing_args_return_none(self):
        self.assertIsNone(get_wa_message_by_message_id(message_id="", jid=TEST_JID))
        self.assertIsNone(get_wa_message_by_message_id(message_id="_test-byid-01", jid=""))
        self.assertIsNone(get_wa_message_by_message_id())
