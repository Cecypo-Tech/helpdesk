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
		# Identifiers are derived per run rather than fixed. With literals, any
		# run that died before its cleanup left rows behind that the next run
		# counted as its own -- which is exactly how this module came to fail
		# with "6 != 3" and "4 != 2", the expected count doubled by one stale
		# generation. test_wa_contact_link already derives its identifiers for
		# the same reason.
		self.suffix = frappe.generate_hash(length=6)
		self._purge_strays()
		self.agent_a = self._make_agent("wa-unread-test-a@example.com")
		self.agent_b = self._make_agent("wa-unread-test-b@example.com")

	@staticmethod
	def _purge_strays():
		"""Clear rows left by an earlier generation of this module.

		Historic runs used fixed JIDs and a fixed line name, so a site that ever
		ran the old version still carries them. Per-run identifiers stop new
		strays; this clears the old ones so the module is not permanently red on
		a database that predates the change.
		"""
		for jid_like in ("%unreadtest@%", "%windowtest@%"):
			for name in frappe.get_all("WA Message", filters={"jid": ["like", jid_like]}, pluck="name"):
				frappe.delete_doc(
					"WA Message", name, force=True, ignore_permissions=True, delete_permanently=True
				)
			frappe.db.delete("WA Conversation Read State", {"jid": ["like", jid_like]})
		for line in ("wa-unread-test-line", "wa-window-test-line"):
			if frappe.db.exists("WA Line", line):
				frappe.delete_doc("WA Line", line, force=True, ignore_permissions=True)
		frappe.db.commit()

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

		jid = f"111unreadtest-{self.suffix}@s.whatsapp.net"
		self._make_message(jid, "first")
		self._make_message(jid, "second")
		self.addCleanup(frappe.db.delete, "WA Conversation Read State", {"jid": jid})

		frappe.set_user(self.agent_a)
		mark_wa_messages_read(jid=jid)

		def unread_count_for(jid):
			convs = get_wa_conversations(line="")["conversations"]
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

		line_name = f"wa-unread-test-line-{self.suffix}"
		frappe.get_doc({"doctype": "WA Line", "instance_name": line_name}).insert(ignore_permissions=True)
		# Registered unconditionally. The old form only registered cleanup when
		# it had just created the line, so a leaked line was never cleaned again.
		self.addCleanup(frappe.delete_doc, "WA Line", line_name, ignore_permissions=True, force=True)

		jid = f"222unreadtest-{self.suffix}@s.whatsapp.net"
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

		jid = f"444unreadtest-{self.suffix}@race.test"
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

		message_log_len = len(frappe.message_log)
		frappe.db.get_value = fake_get_value
		try:
			# Must not raise.
			_mark_conversation_read_for_user(jid, user=user, upto=frappe.utils.now_datetime())
		finally:
			frappe.db.get_value = original_get_value

		rows = frappe.get_all("WA Conversation Read State", filters={"user": user, "jid": jid})
		self.assertEqual(len(rows), 1)
		# The losing insert queues a "must be unique" msgprint before raising;
		# the fallback must scrub it so the agent never sees a spurious toast
		# for a race that was otherwise handled transparently.
		self.assertEqual(len(frappe.message_log), message_log_len)

	def _set_unread_window(self, days):
		frappe.db.set_single_value("WhatsApp Helpdesk Settings", "unread_window_days", days)
		# _shared_settings() reads through get_cached_doc.
		frappe.clear_document_cache("WhatsApp Helpdesk Settings", "WhatsApp Helpdesk Settings")

	def test_unread_window_floors_old_messages_as_read(self):
		"""Incoming messages older than the unread window never count as unread.

		Without a floor an agent with no read-state row for a JID has
		`last_read IS NULL`, so the whole message history counts as unread —
		which is both a nonsense badge and why the unread queries had to scan
		every row ever received. Setting the window to 0 must restore that old
		unbounded behaviour, so both directions are asserted here.
		"""
		from helpdesk.integrations.wa import get_wa_conversations, get_wa_lines

		line_name = f"wa-window-test-line-{self.suffix}"
		frappe.get_doc({"doctype": "WA Line", "instance_name": line_name}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "WA Line", line_name, ignore_permissions=True, force=True)

		original = frappe.db.get_single_value("WhatsApp Helpdesk Settings", "unread_window_days")
		self.addCleanup(self._set_unread_window, original)

		jid = f"555windowtest-{self.suffix}@s.whatsapp.net"
		self.addCleanup(frappe.db.delete, "WA Conversation Read State", {"jid": jid})
		recent = self._make_message(jid, "recent", line=line_name)
		old = self._make_message(jid, "ancient", line=line_name)
		# Backdate past any plausible window. `creation` is set on insert, so it
		# has to be rewritten directly rather than passed in.
		frappe.db.set_value(
			"WA Message",
			old.name,
			"creation",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), days=-60),
			update_modified=False,
		)

		def counts():
			convs = get_wa_conversations(line=line_name)["conversations"]
			match = [c for c in convs if c["jid"] == jid]
			lines = [l for l in get_wa_lines() if l["name"] == line_name]
			return (
				match[0]["unread_count"] if match else 0,
				lines[0]["unread"] if lines else 0,
			)

		frappe.set_user(self.agent_a)

		self._set_unread_window(30)
		self.assertEqual(counts(), (1, 1), "only the recent message is unread inside a 30-day window")

		self._set_unread_window(0)
		self.assertEqual(counts(), (2, 2), "a window of 0 disables the floor entirely")

		# The floor must not resurrect messages an agent has explicitly read.
		self._set_unread_window(30)
		from helpdesk.integrations.wa import mark_wa_messages_read

		mark_wa_messages_read(jid=jid)
		self.assertEqual(counts(), (0, 0))
		self.assertTrue(recent.name)

	def test_historical_sync_does_not_create_unread_for_anyone(self):
		from helpdesk.integrations.wa import _mark_conversation_read_for_all_agents, get_wa_conversations

		jid = f"333unreadtest-{self.suffix}@g.us"
		self._make_message(jid, "imported old message")
		self.addCleanup(frappe.db.delete, "WA Conversation Read State", {"jid": jid})
		_mark_conversation_read_for_all_agents(jid, upto=frappe.utils.now_datetime())

		for user in (self.agent_a, self.agent_b):
			frappe.set_user(user)
			convs = get_wa_conversations(line="")["conversations"]
			match = [c for c in convs if c["jid"] == jid]
			self.assertEqual(match[0]["unread_count"] if match else 0, 0)
