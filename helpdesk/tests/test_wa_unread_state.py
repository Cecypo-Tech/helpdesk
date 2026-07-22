import frappe
from frappe.tests.utils import FrappeTestCase


class TestWAUnreadState(FrappeTestCase):
	def setUp(self):
		# mark_wa_messages_read() commits mid-test, permanently persisting the
		# fixtures created below to the shared site database. Registering this
		# commit first (LIFO execution order) makes it run *last*, after all
		# the addCleanup deletes below have executed, so those deletes are
		# actually committed instead of being rolled back at class teardown.
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		self.agent_a = self._make_agent("wa-unread-test-a@example.com")
		self.agent_b = self._make_agent("wa-unread-test-b@example.com")

	def _make_agent(self, email):
		if not frappe.db.exists("User", email):
			user = frappe.get_doc({
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
			}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "User", email, ignore_permissions=True, force=True)
		else:
			user = frappe.get_doc("User", email)
		user.add_roles("Agent")
		if not frappe.db.exists("HD Agent", {"user": email}):
			agent = frappe.get_doc({
				"doctype": "HD Agent",
				"user": email,
				"agent_name": email,
			}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "HD Agent", agent.name, ignore_permissions=True, force=True)
		return email

	def _make_message(self, jid, message="hello", line=""):
		doc = frappe.get_doc({
			"doctype": "WA Message",
			"jid": jid,
			"direction": "Incoming",
			"message": message,
			"content_type": "text",
			"message_id": frappe.generate_hash(length=10),
			"sender_name": "Test Sender",
			"sender_jid": jid,
			"status": "Delivered",
			"line": line,
		})
		doc.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "WA Message", doc.name, ignore_permissions=True, force=True)
		return doc

	def test_mark_read_does_not_clear_other_agents_badge(self):
		from helpdesk.integrations.wa import get_wa_conversations, mark_wa_messages_read

		jid = "111unreadtest@s.whatsapp.net"
		self._make_message(jid, "first")
		self._make_message(jid, "second")
		self.addCleanup(frappe.db.delete, "WA Conversation Read State", {"jid": jid})

		frappe.set_user(self.agent_a)
		mark_wa_messages_read(jid=jid)

		def unread_count_for(jid):
			convs = get_wa_conversations(line="")
			match = [c for c in convs if c["jid"] == jid]
			return match[0]["unread_count"] if match else None

		self.assertEqual(unread_count_for(jid), 0)

		frappe.set_user(self.agent_b)
		self.assertEqual(unread_count_for(jid), 2)
