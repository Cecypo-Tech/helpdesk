import frappe
from frappe.tests.utils import FrappeTestCase


class TestBaileysStandalone(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self._orig_enabled = frappe.db.get_single_value("Baileys Gateway Settings", "enabled")
		self._orig_api_key = frappe.db.get_single_value("Baileys Gateway Settings", "api_key")
		self._orig_gateway_url = frappe.db.get_single_value("Baileys Gateway Settings", "gateway_url")
		self.addCleanup(self._restore_settings)
		frappe.db.set_single_value("Baileys Gateway Settings", "enabled", 1)
		frappe.db.set_single_value("Baileys Gateway Settings", "api_key", "testkey")
		frappe.db.set_single_value("Baileys Gateway Settings", "gateway_url", "http://localhost:9999")

	def _restore_settings(self):
		frappe.db.set_single_value("Baileys Gateway Settings", "enabled", self._orig_enabled)
		frappe.db.set_single_value("Baileys Gateway Settings", "api_key", self._orig_api_key or "")
		frappe.db.set_single_value("Baileys Gateway Settings", "gateway_url", self._orig_gateway_url or "")

	def _make_message(self, jid="120363test@g.us", direction="Incoming", message="hello", content_type="text", msg_id=None):
		doc = frappe.get_doc({
			"doctype": "Baileys Message",
			"jid": jid,
			"direction": direction,
			"message": message,
			"content_type": content_type,
			"message_id": msg_id or frappe.generate_hash(length=8),
			"sender_name": "Test Sender",
			"sender_jid": jid,
			"status": "Delivered",
		})
		doc.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Baileys Message", doc.name, ignore_permissions=True, force=True)
		return doc

	def test_get_baileys_conversations_returns_one_per_jid(self):
		jid_a = "111standalone@g.us"
		jid_b = "222standalone@s.whatsapp.net"
		self._make_message(jid=jid_a, message="first")
		self._make_message(jid=jid_a, message="second")
		self._make_message(jid=jid_b, message="dm hello")

		from helpdesk.integrations.baileys import get_baileys_conversations
		result = get_baileys_conversations()
		jids = [r["jid"] for r in result]

		self.assertIn(jid_a, jids)
		self.assertIn(jid_b, jids)
		self.assertEqual(jids.count(jid_a), 1)
		self.assertEqual(jids.count(jid_b), 1)

	def test_get_baileys_conversations_last_message_is_most_recent(self):
		jid = "333standalone@g.us"
		self._make_message(jid=jid, message="earlier")
		import time; time.sleep(0.05)
		self._make_message(jid=jid, message="later one")

		from helpdesk.integrations.baileys import get_baileys_conversations
		result = [r for r in get_baileys_conversations() if r["jid"] == jid]
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]["last_message"], "later one")

	def test_get_baileys_messages_by_jid(self):
		jid = "444standalone@g.us"
		self._make_message(jid=jid, message="msg one")
		self._make_message(jid=jid, message="msg two")

		from helpdesk.integrations.baileys import get_baileys_messages
		result = get_baileys_messages(jid=jid)
		messages = [r["message"] for r in result]
		self.assertIn("msg one", messages)
		self.assertIn("msg two", messages)

	def test_webhook_saves_message_without_creating_ticket(self):
		import unittest.mock as mock

		jid = "555standalone@g.us"
		msg_id = frappe.generate_hash(length=10)

		with mock.patch("frappe.publish_realtime"):
			class FakeRequest:
				data = frappe.as_json({
					"jid": jid,
					"messageId": msg_id,
					"sender": jid,
					"senderName": "Tester",
					"message": "webhook test",
					"contentType": "text",
				}).encode()
				def get(self, key, default=None):
					return {"X-API-Key": "testkey", "x-api-key": "testkey"}.get(key, default)

			original_request = getattr(frappe.local, "request", None)
			frappe.local.request = FakeRequest()
			try:
				# Also mock get_request_header
				with mock.patch("frappe.get_request_header", side_effect=lambda k: "testkey" if k in ("X-API-Key", "x-api-key") else None):
					from helpdesk.integrations.baileys import webhook
					result = webhook()
			finally:
				if original_request is not None:
					frappe.local.request = original_request
				else:
					try:
						del frappe.local.request
					except AttributeError:
						pass

		self.assertEqual(result.get("status"), "ok")
		saved = frappe.db.get_value("Baileys Message", {"message_id": msg_id}, ["jid", "message"], as_dict=True)
		self.assertIsNotNone(saved)
		self.assertEqual(saved.jid, jid)
		tickets = frappe.get_all("HD Ticket", filters={"baileys_jid": jid})
		self.assertEqual(len(tickets), 0)
		frappe.db.delete("Baileys Message", {"message_id": msg_id})
