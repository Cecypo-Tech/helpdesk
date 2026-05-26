import frappe
import unittest


class TestWaWebhook(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        # Ensure settings exist
        s = frappe.get_single("WA API Settings")
        s.enabled = 1
        s.global_api_key = "testkey123"
        s.save(ignore_permissions=True)
        frappe.db.commit()
        # Create test line
        if not frappe.db.exists("WA Line", {"instance_name": "_test-evo"}):
            frappe.get_doc({
                "doctype": "WA Line",
                "label": "Test",
                "instance_name": "_test-evo",
            }).insert(ignore_permissions=True)
            frappe.db.commit()
        # Clean up any committed test records from previous runs
        for test_msg_id in [
            "_test-apply-edit-001",
            "_test-apply-edit-002",
            "_test-incoming-edit-001",
            "_test-edit-no-original-001",
            "_test-out-edit-001",
            "_test-in-reject-001",
            "_test-media-reject-001",
            "_test-empty-reject-001",
        ]:
            frappe.db.delete("WA Message", {"message_id": test_msg_id})
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()

    def test_status_update_sets_correct_value(self):
        from helpdesk.integrations.wa import _handle_update, _line
        line = _line("_test-evo")
        # Create a test outgoing message
        msg = frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Outgoing",
            "jid": "254712345678@s.whatsapp.net",
            "message": "test",
            "content_type": "text",
            "message_id": "_test-msg-id-001",
            "status": "Sent",
            "line": line.name,
            "is_read": 1,
        }).insert(ignore_permissions=True)

        _handle_update([{
            "key": {"id": "_test-msg-id-001",
                    "remoteJid": "254712345678@s.whatsapp.net", "fromMe": True},
            "update": {"status": 3},   # DELIVERY_ACK → Delivered
        }], line)

        updated_status = frappe.db.get_value("WA Message", msg.name, "status")
        self.assertEqual(updated_status, "Delivered")

    def test_unknown_status_code_is_ignored(self):
        from helpdesk.integrations.wa import _handle_update, _line
        line = _line("_test-evo")
        # Status 99 should not raise
        result = _handle_update([{
            "key": {"id": "_nonexistent-id", "remoteJid": "x@s.whatsapp.net", "fromMe": True},
            "update": {"status": 99},
        }], line)
        self.assertEqual(result["status"], "ok")

    def test_incoming_edit_updates_existing_message(self):
        from helpdesk.integrations.wa import _handle_upsert, _line, _settings
        line = _line("_test-evo")
        # Create the original message
        frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Incoming",
            "jid": "254799000003@s.whatsapp.net",
            "message": "original from customer",
            "content_type": "text",
            "message_id": "_test-incoming-edit-001",
            "status": "Pending",
            "line": line.name,
            "is_read": 0,
        }).insert(ignore_permissions=True)

        edit_data = {
            "key": {
                "remoteJid": "254799000003@s.whatsapp.net",
                "fromMe": False,
                "id": "_test-incoming-edit-001",
            },
            "message": {
                "editedMessage": {
                    "message": {
                        "protocolMessage": {
                            "type": 14,
                            "editedMessage": {"conversation": "edited by customer"},
                        }
                    }
                }
            },
            "pushName": "Test Customer",
        }
        result = _handle_upsert(edit_data, line, _settings())
        self.assertEqual(result, {"status": "ok", "edited": True})

        msgs = frappe.db.get_all(
            "WA Message",
            filters={"message_id": "_test-incoming-edit-001"},
            fields=["name", "message", "is_edited"],
        )
        self.assertEqual(len(msgs), 1)  # No duplicate created
        self.assertEqual(msgs[0]["message"], "edited by customer")
        self.assertEqual(msgs[0]["is_edited"], 1)

    def test_edit_before_original_falls_through_as_new_message(self):
        from helpdesk.integrations.wa import _handle_upsert, _line, _settings
        line = _line("_test-evo")
        # No original message exists — should fall through to normal insert
        edit_data = {
            "key": {
                "remoteJid": "254799000004@s.whatsapp.net",
                "fromMe": False,
                "id": "_test-edit-no-original-001",
            },
            "message": {
                "editedMessage": {
                    "message": {
                        "protocolMessage": {
                            "type": 14,
                            "editedMessage": {"conversation": "orphan edit"},
                        }
                    }
                }
            },
            "pushName": "Ghost",
        }
        result = _handle_upsert(edit_data, line, _settings())
        # Falls through — a new message record is created
        self.assertIn(result.get("status"), ("ok", "duplicate"))

    def test_edit_wa_message_updates_record(self):
        from unittest.mock import patch, MagicMock
        from helpdesk.integrations.wa import edit_wa_message, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Outgoing",
            "jid": "254799000005@s.whatsapp.net",
            "message": "original agent text",
            "content_type": "text",
            "message_id": "_test-out-edit-001",
            "status": "Read",
            "line": line.name,
            "is_read": 1,
        }).insert(ignore_permissions=True)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {}

        with patch("helpdesk.integrations.wa._requests") as mock_req:
            mock_req.put.return_value = mock_resp
            result = edit_wa_message(msg.name, "updated agent text")

        self.assertEqual(result["status"], "ok")
        updated = frappe.get_doc("WA Message", msg.name)
        self.assertEqual(updated.message, "updated agent text")
        self.assertEqual(updated.is_edited, 1)
        self.assertEqual(len(updated.edit_history), 1)
        self.assertEqual(updated.edit_history[0].old_message, "original agent text")

        # Verify correct WA API endpoint was called
        call_args = mock_req.put.call_args
        self.assertIn("updateMessage", call_args[0][0])
        payload = call_args[1]["json"]
        self.assertEqual(payload["number"], "254799000005@s.whatsapp.net")
        self.assertEqual(payload["key"]["id"], "_test-out-edit-001")
        self.assertEqual(payload["text"], "updated agent text")

    def test_edit_wa_message_rejects_incoming(self):
        from helpdesk.integrations.wa import edit_wa_message, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Incoming",
            "jid": "254799000006@s.whatsapp.net",
            "message": "customer text",
            "content_type": "text",
            "message_id": "_test-in-reject-001",
            "status": "Pending",
            "line": line.name,
            "is_read": 0,
        }).insert(ignore_permissions=True)

        with self.assertRaises(frappe.ValidationError):
            edit_wa_message(msg.name, "attempt to edit incoming")

    def test_edit_wa_message_rejects_media(self):
        from helpdesk.integrations.wa import edit_wa_message, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Outgoing",
            "jid": "254799000007@s.whatsapp.net",
            "message": "",
            "content_type": "image",
            "message_id": "_test-media-reject-001",
            "status": "Sent",
            "line": line.name,
            "is_read": 1,
        }).insert(ignore_permissions=True)

        with self.assertRaises(frappe.ValidationError):
            edit_wa_message(msg.name, "try to edit image")

    def test_edit_wa_message_rejects_empty_text(self):
        from helpdesk.integrations.wa import edit_wa_message, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Outgoing",
            "jid": "254799000008@s.whatsapp.net",
            "message": "some text",
            "content_type": "text",
            "message_id": "_test-empty-reject-001",
            "status": "Sent",
            "line": line.name,
            "is_read": 1,
        }).insert(ignore_permissions=True)

        with self.assertRaises(frappe.ValidationError):
            edit_wa_message(msg.name, "")

    def test_get_wa_lines_returns_unread_count(self):
        from helpdesk.integrations.wa import get_wa_lines
        line = frappe.get_doc("WA Line", {"instance_name": "_test-evo"})
        # Create 2 unread incoming messages
        for i in range(2):
            frappe.get_doc({
                "doctype": "WA Message",
                "direction": "Incoming",
                "jid": f"2547{i}@s.whatsapp.net",
                "message": f"msg {i}",
                "content_type": "text",
                "message_id": f"_test-unread-{i}",
                "status": "Pending",
                "line": line.name,
                "is_read": 0,
            }).insert(ignore_permissions=True)

        lines = get_wa_lines()
        test_line = next((l for l in lines if l["name"] == line.name), None)
        self.assertIsNotNone(test_line)
        self.assertGreaterEqual(test_line["unread"], 2)

    def test_merge_lid_into_pn_rekeys_messages_and_sets_canonical(self):
        from helpdesk.integrations.wa import _merge_lid_into_pn
        lid_jid = "99999000001@lid"
        pn_jid = "447900000001@s.whatsapp.net"

        # Create LID contact row with metadata
        if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
            frappe.get_doc({
                "doctype": "WA Contact",
                "jid": lid_jid,
                "phone": "",
                "custom_name": "Merge Test",
                "company": "",
                "assigned_team": "",
            }).insert(ignore_permissions=True)

        # Create a WA Message on the LID JID
        msg = frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Incoming",
            "jid": lid_jid,
            "sender_jid": lid_jid,
            "message": "test merge",
            "content_type": "text",
            "message_id": "_test-merge-lid-001",
            "status": "Delivered",
            "line": "_test-evo",
            "is_read": 0,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

        _merge_lid_into_pn(lid_jid, pn_jid)

        # LID row should have canonical_jid set
        canonical = frappe.db.get_value("WA Contact", {"jid": lid_jid}, "canonical_jid")
        self.assertEqual(canonical, pn_jid)

        # PN row should exist
        self.assertTrue(frappe.db.exists("WA Contact", {"jid": pn_jid}))

        # WA Message should now be keyed to PN JID
        new_jid = frappe.db.get_value("WA Message", msg.name, "jid")
        self.assertEqual(new_jid, pn_jid)

        # Cleanup
        frappe.db.delete("WA Message", {"message_id": "_test-merge-lid-001"})
        frappe.db.delete("WA Contact", {"jid": lid_jid})
        frappe.db.delete("WA Contact", {"jid": pn_jid})
        frappe.db.commit()

    def test_merge_lid_into_pn_is_idempotent(self):
        from helpdesk.integrations.wa import _merge_lid_into_pn
        lid_jid = "99999000002@lid"
        pn_jid = "447900000002@s.whatsapp.net"

        if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
            frappe.get_doc({
                "doctype": "WA Contact",
                "jid": lid_jid,
                "phone": "",
                "custom_name": "Idempotent Test",
            }).insert(ignore_permissions=True)
            frappe.db.commit()

        _merge_lid_into_pn(lid_jid, pn_jid)
        _merge_lid_into_pn(lid_jid, pn_jid)  # second call must not raise

        canonical = frappe.db.get_value("WA Contact", {"jid": lid_jid}, "canonical_jid")
        self.assertEqual(canonical, pn_jid)

        # Cleanup
        frappe.db.delete("WA Contact", {"jid": lid_jid})
        frappe.db.delete("WA Contact", {"jid": pn_jid})
        frappe.db.commit()

    def test_wa_contact_has_canonical_jid_field(self):
        meta = frappe.get_meta("WA Contact")
        field_names = [f.fieldname for f in meta.fields]
        self.assertIn("canonical_jid", field_names)

    def test_upsert_contact_triggers_merge_for_lid_with_phone(self):
        from helpdesk.integrations.wa import _upsert_contact
        lid_jid = "99999000003@lid"
        phone = "447900000003"
        pn_jid = f"{phone}@s.whatsapp.net"

        # Pre-create LID row
        if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
            frappe.get_doc({
                "doctype": "WA Contact",
                "jid": lid_jid,
                "phone": "",
                "custom_name": "Trigger Test",
            }).insert(ignore_permissions=True)
            frappe.db.commit()

        _upsert_contact(lid_jid, phone, "Trigger Test")

        canonical = frappe.db.get_value("WA Contact", {"jid": lid_jid}, "canonical_jid")
        self.assertEqual(canonical, pn_jid)

        # Cleanup
        frappe.db.delete("WA Contact", {"jid": lid_jid})
        frappe.db.delete("WA Contact", {"jid": pn_jid})
        frappe.db.commit()

    def test_upsert_contact_mapping_merges_lid(self):
        from helpdesk.integrations.wa import upsert_contact_mapping
        lid_jid = "99999000004@lid"
        phone = "447900000004"
        pn_jid = f"{phone}@s.whatsapp.net"

        if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
            frappe.get_doc({
                "doctype": "WA Contact",
                "jid": lid_jid,
                "phone": "",
                "custom_name": "",
            }).insert(ignore_permissions=True)
            frappe.db.commit()

        result = upsert_contact_mapping(lid=lid_jid, phone=phone, name="Gateway User")

        self.assertEqual(result.get("status"), "ok")
        canonical = frappe.db.get_value("WA Contact", {"jid": lid_jid}, "canonical_jid")
        self.assertEqual(canonical, pn_jid)

        # Cleanup
        frappe.db.delete("WA Contact", {"jid": lid_jid})
        frappe.db.delete("WA Contact", {"jid": pn_jid})
        frappe.db.commit()

    def test_incoming_message_on_lid_is_rerouted_to_pn(self):
        from helpdesk.integrations.wa import _handle_upsert, _line, _settings
        line = _line("_test-evo")
        settings = _settings()
        lid_jid = "99999000005@lid"
        pn_jid = "447900000005@s.whatsapp.net"

        # Pre-create a merged LID alias row (canonical_jid already set)
        if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
            frappe.get_doc({
                "doctype": "WA Contact",
                "jid": lid_jid,
                "phone": "",
                "custom_name": "Reroute Test",
                "canonical_jid": pn_jid,
            }).insert(ignore_permissions=True)
            frappe.db.commit()

        frappe.set_user("Administrator")
        _handle_upsert({
            "key": {
                "remoteJid": lid_jid,
                "fromMe": False,
                "id": "_test-reroute-lid-001",
            },
            "pushName": "Reroute Test",
            "message": {"conversation": "hello reroute"},
        }, line, settings)

        # Message should be stored under the PN JID, not the LID
        msg = frappe.db.get_value(
            "WA Message", {"message_id": "_test-reroute-lid-001"}, ["jid", "name"], as_dict=True
        )
        self.assertIsNotNone(msg)
        self.assertEqual(msg["jid"], pn_jid)

        # Cleanup
        frappe.db.delete("WA Message", {"message_id": "_test-reroute-lid-001"})
        frappe.db.delete("WA Contact", {"jid": lid_jid})
        frappe.db.commit()


class TestExtractEdit(unittest.TestCase):
    def test_shape1_extracts_text(self):
        from helpdesk.integrations.wa import _extract_edit
        raw = {
            "editedMessage": {
                "message": {
                    "protocolMessage": {
                        "type": 14,
                        "editedMessage": {"conversation": "new text shape1"}
                    }
                }
            }
        }
        text, is_edit = _extract_edit(raw)
        self.assertTrue(is_edit)
        self.assertEqual(text, "new text shape1")

    def test_shape2_extracts_text(self):
        from helpdesk.integrations.wa import _extract_edit
        raw = {
            "protocolMessage": {
                "type": 14,
                "editedMessage": {"conversation": "new text shape2"}
            }
        }
        text, is_edit = _extract_edit(raw)
        self.assertTrue(is_edit)
        self.assertEqual(text, "new text shape2")

    def test_shape2_extended_text_message(self):
        from helpdesk.integrations.wa import _extract_edit
        raw = {
            "protocolMessage": {
                "type": 14,
                "editedMessage": {
                    "extendedTextMessage": {"text": "new text extended"}
                }
            }
        }
        text, is_edit = _extract_edit(raw)
        self.assertTrue(is_edit)
        self.assertEqual(text, "new text extended")

    def test_regular_message_returns_false(self):
        from helpdesk.integrations.wa import _extract_edit
        raw = {"conversation": "hello"}
        text, is_edit = _extract_edit(raw)
        self.assertFalse(is_edit)
        self.assertEqual(text, "")

    def test_empty_dict_returns_false(self):
        from helpdesk.integrations.wa import _extract_edit
        text, is_edit = _extract_edit({})
        self.assertFalse(is_edit)
        self.assertEqual(text, "")


class TestApplyEdit(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        if not frappe.db.exists("WA Line", {"instance_name": "_test-evo"}):
            frappe.get_doc({
                "doctype": "WA Line",
                "label": "Test",
                "instance_name": "_test-evo",
            }).insert(ignore_permissions=True)
            frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()

    def test_apply_edit_updates_message_and_history(self):
        from helpdesk.integrations.wa import _apply_edit, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Incoming",
            "jid": "254799000001@s.whatsapp.net",
            "message": "original text",
            "content_type": "text",
            "message_id": "_test-apply-edit-001",
            "status": "Pending",
            "line": line.name,
            "is_read": 0,
        }).insert(ignore_permissions=True)

        _apply_edit(msg.name, "edited text", "incoming", msg.jid, line)

        updated = frappe.get_doc("WA Message", msg.name)
        self.assertEqual(updated.message, "edited text")
        self.assertEqual(updated.is_edited, 1)
        self.assertEqual(len(updated.edit_history), 1)
        self.assertEqual(updated.edit_history[0].old_message, "original text")
        self.assertEqual(updated.edit_history[0].edited_by, "incoming")

    def test_apply_edit_appends_on_second_edit(self):
        from helpdesk.integrations.wa import _apply_edit, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Incoming",
            "jid": "254799000002@s.whatsapp.net",
            "message": "v1",
            "content_type": "text",
            "message_id": "_test-apply-edit-002",
            "status": "Pending",
            "line": line.name,
            "is_read": 0,
        }).insert(ignore_permissions=True)

        _apply_edit(msg.name, "v2", "incoming", msg.jid, line)
        _apply_edit(msg.name, "v3", "incoming", msg.jid, line)

        updated = frappe.get_doc("WA Message", msg.name)
        self.assertEqual(updated.message, "v3")
        self.assertEqual(len(updated.edit_history), 2)
        messages_in_history = [r.old_message for r in updated.edit_history]
        self.assertIn("v1", messages_in_history)
        self.assertIn("v2", messages_in_history)
