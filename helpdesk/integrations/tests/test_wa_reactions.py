import unittest
from unittest.mock import MagicMock, patch

import frappe

from helpdesk.integrations.wa import send_wa_reaction

TEST_JID = "_test-react@s.whatsapp.net"
TEST_LINE = "_test-react-line"
TARGET_ID = "_test-react-target-01"


def _fake_resp(sent_id="_test-react-sent-01"):
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = {"key": {"id": sent_id}}
    resp.raise_for_status.return_value = None
    return resp


class TestSendWaReaction(unittest.TestCase):
    """Reacting, replacing, and clearing on the WA Line (Evolution) path.

    Clearing is expressed as an empty emoji: Evolution's `reactionMessageSchema`
    declares `reaction` as a plain string with no isNotEmpty constraint, and
    WhatsApp removes the reaction when it receives one with an empty body.
    """

    def setUp(self):
        frappe.set_user("Administrator")
        self._purge()
        if not frappe.db.exists("WA Line", TEST_LINE):
            frappe.get_doc({
                "doctype": "WA Line",
                "instance_name": TEST_LINE,
                "instance_token": "_test-token",
            }).insert(ignore_permissions=True)
        # The line for a jid is resolved from the newest message carrying one.
        frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Incoming",
            "jid": TEST_JID,
            "message": "react to me",
            "content_type": "text",
            "message_id": TARGET_ID,
            "status": "Delivered",
            "line": TEST_LINE,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        self._purge()

    def _purge(self):
        frappe.db.delete("WA Message", {"jid": TEST_JID})
        frappe.db.commit()

    def _reactions(self):
        return frappe.get_all(
            "WA Message",
            filters={"jid": TEST_JID, "content_type": "reaction"},
            fields=["message", "reply_to_message_id", "direction"],
            order_by="creation asc",
        )

    def _send(self, emoji, sent_id="_test-react-sent-01"):
        settings = MagicMock()
        settings.enabled = True
        with patch("helpdesk.integrations.wa._settings", return_value=settings), \
             patch("helpdesk.integrations.wa._evo_session") as sess, \
             patch("helpdesk.integrations.wa._publish_wa_event"):
            sess.post.return_value = _fake_resp(sent_id)
            result = send_wa_reaction(
                jid=TEST_JID, target_message_id=TARGET_ID, emoji=emoji
            )
            return result, sess

    def test_react_posts_emoji_and_stores_row(self):
        result, sess = self._send("👍")
        self.assertEqual(result["status"], "ok")

        url, kwargs = sess.post.call_args[0][0], sess.post.call_args[1]
        self.assertIn("message/sendReaction", url)
        self.assertEqual(kwargs["json"]["reaction"], "👍")
        self.assertEqual(kwargs["json"]["key"]["id"], TARGET_ID)
        self.assertEqual(kwargs["json"]["key"]["remoteJid"], TEST_JID)

        rows = self._reactions()
        self.assertEqual(1, len(rows))
        self.assertEqual("👍", rows[0].message)
        self.assertEqual(TARGET_ID, rows[0].reply_to_message_id)

    def test_clearing_sends_empty_reaction_and_does_not_throw(self):
        """The regression: an empty emoji used to be rejected as a missing argument."""
        result, sess = self._send("")
        self.assertEqual(result["status"], "ok")
        self.assertEqual("", sess.post.call_args[1]["json"]["reaction"])

    def test_clearing_stores_a_tombstone_row(self):
        """The clear is persisted as an empty-message row the client folds as a removal."""
        self._send("👍", sent_id="_test-react-sent-01")
        self._send("", sent_id="_test-react-sent-02")

        rows = self._reactions()
        self.assertEqual(2, len(rows), "clear is appended, not applied in place")
        self.assertEqual("👍", rows[0].message)
        self.assertEqual("", rows[1].message or "")
        self.assertEqual(TARGET_ID, rows[1].reply_to_message_id)

    def test_replacing_appends_the_newer_emoji(self):
        self._send("👍", sent_id="_test-react-sent-01")
        self._send("❤️", sent_id="_test-react-sent-02")

        rows = self._reactions()
        self.assertEqual(2, len(rows))
        self.assertEqual(["👍", "❤️"], [r.message for r in rows])

    def test_missing_target_still_throws(self):
        settings = MagicMock()
        settings.enabled = True
        with patch("helpdesk.integrations.wa._settings", return_value=settings):
            with self.assertRaises(frappe.ValidationError):
                send_wa_reaction(jid=TEST_JID, target_message_id="", emoji="👍")

    def test_missing_jid_still_throws(self):
        settings = MagicMock()
        settings.enabled = True
        with patch("helpdesk.integrations.wa._settings", return_value=settings):
            with self.assertRaises(frappe.ValidationError):
                send_wa_reaction(jid="", target_message_id=TARGET_ID, emoji="👍")
