import frappe
import unittest


class TestEvolutionWebhook(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        # Ensure settings exist
        s = frappe.get_single("Evolution API Settings")
        s.enabled = 1
        s.global_api_key = "testkey123"
        s.save(ignore_permissions=True)
        frappe.db.commit()
        # Create test line
        if not frappe.db.exists("Evolution Line", {"instance_name": "_test-evo"}):
            frappe.get_doc({
                "doctype": "Evolution Line",
                "label": "Test",
                "instance_name": "_test-evo",
            }).insert(ignore_permissions=True)
            frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()

    def test_status_update_sets_correct_value(self):
        from helpdesk.integrations.evolution import _handle_update, _line
        line = _line("_test-evo")
        # Create a test outgoing message
        msg = frappe.get_doc({
            "doctype": "Baileys Message",
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

        updated_status = frappe.db.get_value("Baileys Message", msg.name, "status")
        self.assertEqual(updated_status, "Delivered")

    def test_unknown_status_code_is_ignored(self):
        from helpdesk.integrations.evolution import _handle_update, _line
        line = _line("_test-evo")
        # Status 99 should not raise
        result = _handle_update([{
            "key": {"id": "_nonexistent-id", "remoteJid": "x@s.whatsapp.net", "fromMe": True},
            "update": {"status": 99},
        }], line)
        self.assertEqual(result["status"], "ok")

    def test_get_evolution_lines_returns_unread_count(self):
        from helpdesk.integrations.evolution import get_evolution_lines
        line = frappe.get_doc("Evolution Line", {"instance_name": "_test-evo"})
        # Create 2 unread incoming messages
        for i in range(2):
            frappe.get_doc({
                "doctype": "Baileys Message",
                "direction": "Incoming",
                "jid": f"2547{i}@s.whatsapp.net",
                "message": f"msg {i}",
                "content_type": "text",
                "message_id": f"_test-unread-{i}",
                "status": "Pending",
                "line": line.name,
                "is_read": 0,
            }).insert(ignore_permissions=True)

        lines = get_evolution_lines()
        test_line = next((l for l in lines if l["name"] == line.name), None)
        self.assertIsNotNone(test_line)
        self.assertGreaterEqual(test_line["unread"], 2)


class TestExtractEdit(unittest.TestCase):
    def test_shape1_extracts_text(self):
        from helpdesk.integrations.evolution import _extract_edit
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
        from helpdesk.integrations.evolution import _extract_edit
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
        from helpdesk.integrations.evolution import _extract_edit
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
        from helpdesk.integrations.evolution import _extract_edit
        raw = {"conversation": "hello"}
        text, is_edit = _extract_edit(raw)
        self.assertFalse(is_edit)
        self.assertEqual(text, "")

    def test_empty_dict_returns_false(self):
        from helpdesk.integrations.evolution import _extract_edit
        text, is_edit = _extract_edit({})
        self.assertFalse(is_edit)
        self.assertEqual(text, "")


class TestApplyEdit(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        if not frappe.db.exists("Evolution Line", {"instance_name": "_test-evo"}):
            frappe.get_doc({
                "doctype": "Evolution Line",
                "label": "Test",
                "instance_name": "_test-evo",
            }).insert(ignore_permissions=True)
            frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()

    def test_apply_edit_updates_message_and_history(self):
        from helpdesk.integrations.evolution import _apply_edit, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "Baileys Message",
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

        updated = frappe.get_doc("Baileys Message", msg.name)
        self.assertEqual(updated.message, "edited text")
        self.assertEqual(updated.is_edited, 1)
        self.assertEqual(len(updated.edit_history), 1)
        self.assertEqual(updated.edit_history[0].old_message, "original text")
        self.assertEqual(updated.edit_history[0].edited_by, "incoming")

    def test_apply_edit_appends_on_second_edit(self):
        from helpdesk.integrations.evolution import _apply_edit, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "Baileys Message",
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

        updated = frappe.get_doc("Baileys Message", msg.name)
        self.assertEqual(updated.message, "v3")
        self.assertEqual(len(updated.edit_history), 2)
        messages_in_history = [r.old_message for r in updated.edit_history]
        self.assertIn("v1", messages_in_history)
        self.assertIn("v2", messages_in_history)
