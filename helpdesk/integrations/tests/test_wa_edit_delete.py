import unittest
from unittest.mock import MagicMock, patch

import frappe

from helpdesk.integrations.wa import (
    _handle_delete,
    delete_wa_message,
    edit_wa_message,
    retry_wa_message,
)

TEST_JID = "_test-ed@s.whatsapp.net"
TEST_LINE = "_test-ed-line"


def _ok_resp():
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = {}
    resp.raise_for_status.return_value = None
    return resp


def _fail_resp():
    resp = MagicMock()
    resp.ok = False
    resp.status_code = 500
    resp.text = "boom"
    resp.raise_for_status.side_effect = Exception("500 Server Error")
    return resp


def _enabled_settings():
    settings = MagicMock()
    settings.enabled = True
    return settings


class _WaEditDeleteBase(unittest.TestCase):
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
        frappe.db.delete("WA Message", {"jid": TEST_JID})
        frappe.db.commit()

    def _msg(self, message_id, direction="Outgoing", content_type="text", text="hello"):
        name = frappe.get_doc({
            "doctype": "WA Message",
            "direction": direction,
            "jid": TEST_JID,
            "message": text,
            "content_type": content_type,
            "message_id": message_id,
            "status": "Sent",
            "line": TEST_LINE,
        }).insert(ignore_permissions=True).name
        frappe.db.commit()
        return name


class TestEditWaMessage(_WaEditDeleteBase):
    """Editing goes to POST /chat/updateMessage — `message/updateMessage` does not exist."""

    def test_edit_posts_to_chat_update_message(self):
        name = self._msg("_test-ed-edit-01", text="before")
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _ok_resp()
            edit_wa_message(message_name=name, new_text="after")

        self.assertTrue(sess.post.called, "edit must use POST, not PUT")
        self.assertFalse(sess.put.called, "PUT /message/updateMessage 404s on Evolution v2")

        url = sess.post.call_args[0][0]
        self.assertIn("chat/updateMessage", url)
        self.assertNotIn("message/updateMessage", url)
        self.assertTrue(url.endswith(f"/{TEST_LINE}"))

        payload = sess.post.call_args[1]["json"]
        self.assertEqual("after", payload["text"])
        self.assertEqual("_test-ed-edit-01", payload["key"]["id"])
        self.assertEqual(TEST_JID, payload["key"]["remoteJid"])
        self.assertTrue(payload["key"]["fromMe"])
        self.assertEqual(TEST_JID, payload["number"])

    def test_edit_applies_locally_with_history(self):
        name = self._msg("_test-ed-edit-02", text="before")
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _ok_resp()
            edit_wa_message(message_name=name, new_text="after")

        doc = frappe.get_doc("WA Message", name)
        self.assertEqual("after", doc.message)
        self.assertTrue(doc.is_edited)
        self.assertEqual(1, len(doc.edit_history))
        self.assertEqual("before", doc.edit_history[0].old_message)

    def test_incoming_cannot_be_edited(self):
        name = self._msg("_test-ed-edit-03", direction="Incoming")
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()):
            with self.assertRaises(frappe.ValidationError):
                edit_wa_message(message_name=name, new_text="nope")

    def test_api_failure_leaves_message_unchanged(self):
        name = self._msg("_test-ed-edit-04", text="before")
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _fail_resp()
            with self.assertRaises(Exception):
                edit_wa_message(message_name=name, new_text="after")

        doc = frappe.get_doc("WA Message", name)
        self.assertEqual("before", doc.message)
        self.assertFalse(doc.is_edited)


class TestDeleteWaMessage(_WaEditDeleteBase):
    """Agent-initiated delete-for-everyone, restricted to own outgoing messages."""

    def _delete(self, name, resp=None):
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess, \
             patch("helpdesk.integrations.wa._publish_wa_delete") as pub:
            sess.delete.return_value = resp or _ok_resp()
            result = delete_wa_message(message_name=name)
            return result, sess, pub

    def test_delete_calls_delete_for_everyone(self):
        name = self._msg("_test-ed-del-01")
        result, sess, pub = self._delete(name)

        self.assertEqual("ok", result["status"])
        url = sess.delete.call_args[0][0]
        self.assertIn("chat/deleteMessageForEveryone", url)
        self.assertTrue(url.endswith(f"/{TEST_LINE}"))

        payload = sess.delete.call_args[1]["json"]
        self.assertEqual("_test-ed-del-01", payload["id"])
        self.assertEqual(TEST_JID, payload["remoteJid"])
        self.assertTrue(payload["fromMe"])

        self.assertTrue(frappe.db.get_value("WA Message", name, "is_deleted"))
        self.assertTrue(pub.called, "open chats must be told to flip the bubble")

    def test_delete_does_not_touch_delivery_status(self):
        """`status` means delivery; a delete must not masquerade as a send failure."""
        name = self._msg("_test-ed-del-02")
        self._delete(name)
        self.assertEqual("Sent", frappe.db.get_value("WA Message", name, "status"))

    def test_incoming_cannot_be_deleted(self):
        name = self._msg("_test-ed-del-03", direction="Incoming")
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()):
            with self.assertRaises(frappe.ValidationError):
                delete_wa_message(message_name=name)
        self.assertFalse(frappe.db.get_value("WA Message", name, "is_deleted"))

    def test_delete_is_idempotent(self):
        name = self._msg("_test-ed-del-04")
        self._delete(name)
        result, sess, _ = self._delete(name)
        self.assertTrue(result.get("already_deleted"))
        self.assertFalse(sess.delete.called, "second delete must not re-hit the API")

    def test_api_failure_does_not_mark_deleted(self):
        name = self._msg("_test-ed-del-05")
        with self.assertRaises(Exception):
            self._delete(name, resp=_fail_resp())
        self.assertFalse(frappe.db.get_value("WA Message", name, "is_deleted"))

    def test_deleted_message_cannot_be_retried(self):
        name = self._msg("_test-ed-del-06")
        self._delete(name)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()):
            with self.assertRaises(frappe.ValidationError):
                retry_wa_message(message_name=name)


class TestHandleDeleteWebhook(_WaEditDeleteBase):
    """Inbound messages.delete — the sender removed a message on their phone."""

    def test_marks_is_deleted_and_publishes(self):
        name = self._msg("_test-ed-wh-01", direction="Incoming")
        line = frappe.get_doc("WA Line", TEST_LINE)
        with patch("helpdesk.integrations.wa._publish_wa_delete") as pub:
            result = _handle_delete({"ids": ["_test-ed-wh-01"]}, line)

        self.assertEqual(1, result["deleted"])
        self.assertTrue(frappe.db.get_value("WA Message", name, "is_deleted"))
        pub.assert_called_once_with(TEST_JID, "_test-ed-wh-01", TEST_LINE)

    def test_does_not_write_failed_status(self):
        """Regression: this used to set status="Failed", offering a Retry on a deleted message."""
        name = self._msg("_test-ed-wh-02", direction="Incoming")
        line = frappe.get_doc("WA Line", TEST_LINE)
        with patch("helpdesk.integrations.wa._publish_wa_delete"):
            _handle_delete({"ids": ["_test-ed-wh-02"]}, line)

        self.assertEqual("Sent", frappe.db.get_value("WA Message", name, "status"))

    def test_accepts_a_bare_string_id(self):
        name = self._msg("_test-ed-wh-03", direction="Incoming")
        line = frappe.get_doc("WA Line", TEST_LINE)
        with patch("helpdesk.integrations.wa._publish_wa_delete"):
            result = _handle_delete({"ids": "_test-ed-wh-03"}, line)

        self.assertEqual(1, result["deleted"])
        self.assertTrue(frappe.db.get_value("WA Message", name, "is_deleted"))

    def test_unknown_id_is_ignored(self):
        line = frappe.get_doc("WA Line", TEST_LINE)
        with patch("helpdesk.integrations.wa._publish_wa_delete") as pub:
            result = _handle_delete({"ids": ["_test-ed-wh-missing"]}, line)

        self.assertEqual(0, result["deleted"])
        self.assertFalse(pub.called)
