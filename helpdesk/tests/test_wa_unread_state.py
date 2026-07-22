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

	def test_ticket_badge_and_mark_all_are_per_agent(self):
		from helpdesk.integrations.wa import (
			get_ticket_wa_unread_count,
			get_wa_lines,
			mark_all_wa_messages_read,
		)

		line_name = "wa-unread-test-line"
		if not frappe.db.exists("WA Line", line_name):
			frappe.get_doc({"doctype": "WA Line", "instance_name": line_name}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "WA Line", line_name, ignore_permissions=True, force=True)

		jid = "222unreadtest@s.whatsapp.net"
		self._make_message(jid, "one", line=line_name)
		self._make_message(jid, "two", line=line_name)
		self._make_message(jid, "three", line=line_name)
		self.addCleanup(frappe.db.delete, "WA Conversation Read State", {"jid": jid})

		ticket = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": "WA unread test ticket",
			"raised_by": "wa-unread-ticket-test@example.com",
			"baileys_jid": jid,
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "HD Ticket", ticket.name, ignore_permissions=True, force=True)

		def sidebar_unread():
			rows = [l for l in get_wa_lines() if l["name"] == line_name]
			return rows[0]["unread"] if rows else None

		frappe.set_user(self.agent_a)
		self.assertEqual(get_ticket_wa_unread_count(str(ticket.name)), 3)
		self.assertEqual(sidebar_unread(), 3)
		marked = mark_all_wa_messages_read(line=line_name)
		self.assertEqual(marked, 3)
		self.assertEqual(get_ticket_wa_unread_count(str(ticket.name)), 0)
		self.assertEqual(sidebar_unread(), 0)

		frappe.set_user(self.agent_b)
		self.assertEqual(get_ticket_wa_unread_count(str(ticket.name)), 3)
		self.assertEqual(sidebar_unread(), 3)

	def test_mark_conversation_read_race_does_not_duplicate_or_raise(self):
		"""Regression test for the (user, jid) unique-index race.

		_mark_conversation_read_for_user does a get-then-insert-or-update that
		isn't atomic: two concurrent callers can both see "no existing row" and
		both attempt an insert. Simulate that by pre-creating the row a
		"winning" concurrent request would have inserted, then forcing the
		function under test to still believe no row exists (as it would have,
		had it read a moment earlier) via a one-shot monkeypatch of
		frappe.db.get_value. The (user, jid) unique index means the resulting
		insert collides; the fix must catch that and fall back to an update
		instead of raising or creating a duplicate row.
		"""
		from helpdesk.integrations.wa import _mark_conversation_read_for_user

		jid = "444unreadtest@race.test"
		user = self.agent_a
		self.addCleanup(frappe.db.delete, "WA Conversation Read State", {"jid": jid})

		# The "winning" concurrent request's insert.
		frappe.get_doc({
			"doctype": "WA Conversation Read State",
			"user": user,
			"jid": jid,
			"last_read": frappe.utils.now_datetime(),
		}).insert(ignore_permissions=True)

		original_get_value = frappe.db.get_value
		state = {"called": False}

		def fake_get_value(doctype, filters=None, fieldname=None, *args, **kwargs):
			if doctype == "WA Conversation Read State" and not state["called"]:
				state["called"] = True
				return None  # simulate the stale read that lost the race
			return original_get_value(doctype, filters, fieldname, *args, **kwargs)

		frappe.db.get_value = fake_get_value
		try:
			# Must not raise.
			_mark_conversation_read_for_user(jid, user=user, upto=frappe.utils.now_datetime())
		finally:
			frappe.db.get_value = original_get_value

		rows = frappe.get_all("WA Conversation Read State", filters={"user": user, "jid": jid})
		self.assertEqual(len(rows), 1)

	def test_historical_sync_does_not_create_unread_for_anyone(self):
		from helpdesk.integrations.wa import _mark_conversation_read_for_all_agents, get_wa_conversations

		jid = "333unreadtest@g.us"
		self._make_message(jid, "imported old message")
		self.addCleanup(frappe.db.delete, "WA Conversation Read State", {"jid": jid})
		_mark_conversation_read_for_all_agents(jid, upto=frappe.utils.now_datetime())

		for user in (self.agent_a, self.agent_b):
			frappe.set_user(user)
			convs = get_wa_conversations(line="")
			match = [c for c in convs if c["jid"] == jid]
			self.assertEqual(match[0]["unread_count"] if match else 0, 0)
