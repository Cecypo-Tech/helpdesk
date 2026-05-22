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
