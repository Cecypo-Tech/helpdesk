from unittest.mock import patch
from urllib.parse import quote

import frappe
from frappe.tests.utils import FrappeTestCase

JID = "999notiftest@s.whatsapp.net"
LINE = "wa-notif-test-line"


class TestWANotifications(FrappeTestCase):
	"""Incoming WA Line messages must reach the bell and the phone.

	They never did: the realtime event was published under a name nothing
	listened for, and no HD Notification was created at all — which is what
	sends the web push. Both failed silently.
	"""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("WA Line", LINE):
			frappe.get_doc({"doctype": "WA Line", "instance_name": LINE}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "WA Line", LINE, ignore_permissions=True, force=True)
		self.line = frappe.get_doc("WA Line", LINE)
		self.agents = [
			self._make_agent("wa-notif-a@example.com"),
			self._make_agent("wa-notif-b@example.com"),
		]
		self.addCleanup(frappe.db.delete, "HD Notification", {"reference_wa_jid": JID})

	def _make_agent(self, email):
		if not frappe.db.exists("User", email):
			frappe.get_doc({
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
			}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "User", email, ignore_permissions=True, force=True)
		if not frappe.db.exists("HD Agent", {"user": email}):
			agent = frappe.get_doc({
				"doctype": "HD Agent", "user": email, "agent_name": email, "is_active": 1
			}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "HD Agent", agent.name, ignore_permissions=True, force=True)
		return email

	def _notifications_for(self, user, read=None):
		filters = {"reference_wa_jid": JID, "user_to": user, "notification_type": "WhatsApp"}
		if read is not None:
			filters["read"] = read
		return frappe.get_all("HD Notification", filters=filters, pluck="name")

	def _notify(self):
		from helpdesk.integrations.wa import _notify_agents

		# The quiet-period guard is a separate concern; pin it off so these
		# tests exercise the notification path itself.
		settings = frappe._dict(notification_quiet_minutes=0)
		with patch("helpdesk.integrations.wa._shared_settings", return_value=settings):
			_notify_agents(JID, "hello there", "Test Sender", self.line, settings)

	def test_every_active_agent_is_notified(self):
		self._notify()
		for agent in self.agents:
			self.assertEqual(
				len(self._notifications_for(agent, read=0)), 1,
				f"{agent} should have exactly one unread notification",
			)

	def test_further_messages_do_not_pile_up(self):
		"""One notification per conversation, not per message."""
		for _ in range(5):
			self._notify()
		for agent in self.agents:
			self.assertEqual(len(self._notifications_for(agent)), 1)

	def test_opening_the_chat_releases_the_dedupe(self):
		"""Without this an agent is notified once about a chat and never again."""
		from helpdesk.integrations.wa import _clear_wa_notifications

		self._notify()
		reader, other = self.agents
		_clear_wa_notifications(JID, reader)

		self.assertEqual(len(self._notifications_for(reader, read=0)), 0)
		# Reading is per-agent; the other agent's notification is untouched.
		self.assertEqual(len(self._notifications_for(other, read=0)), 1)

		self._notify()
		self.assertEqual(
			len(self._notifications_for(reader, read=0)), 1,
			"a new message after reading must notify again",
		)
		self.assertEqual(
			len(self._notifications_for(other, read=0)), 1,
			"the agent who never read must not be notified twice",
		)

	def test_publishes_the_event_the_frontend_listens_for(self):
		"""The name is the bug: nothing listened for helpdesk:new-baileys-message."""
		published = []
		with patch(
			"frappe.publish_realtime",
			side_effect=lambda event, *a, **kw: published.append(event),
		):
			self._notify()
		self.assertIn("helpdesk:baileys-notification", published)
		self.assertNotIn("helpdesk:new-baileys-message", published)

	def test_push_deep_links_to_the_conversation(self):
		"""A ticketless chat would otherwise push the agent to the dashboard."""
		self._notify()
		doc = frappe.get_doc("HD Notification", self._notifications_for(self.agents[0])[0])
		self.assertFalse(doc.reference_ticket)
		self.assertEqual(doc.reference_wa_line, LINE)

		sent = {}
		with patch(
			"helpdesk.helpdesk.api.push_notifications.send_push_to_user",
			side_effect=lambda **kw: sent.update(kw),
		):
			doc._send_push_notification()

		self.assertIn(f"/helpdesk/whatsapp/{LINE}", sent.get("url", ""))
		# The JID is percent-encoded — "@" is not safe in a query value.
		self.assertIn(quote(JID), sent.get("url", ""))
		self.assertEqual(sent.get("title"), "New WhatsApp message")

	def test_quiet_period_suppresses_the_push_not_just_the_bell(self):
		"""An attended conversation must not buzz phones on every customer reply.

		The guard has to sit ahead of the HD Notification insert, because that
		insert is what sends the web push. Behind it, the quiet period would
		only ever have silenced the in-app bell.
		"""
		from helpdesk.integrations.wa import _notify_agents

		frappe.get_doc({
			"doctype": "WA Message",
			"jid": JID,
			"direction": "Outgoing",
			"message": "an agent is on it",
			"content_type": "text",
			"message_id": frappe.generate_hash(length=10),
			"status": "Sent",
			"line": LINE,
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.db.delete, "WA Message", {"jid": JID})

		settings = frappe._dict(notification_quiet_minutes=10)
		with patch("helpdesk.integrations.wa._shared_settings", return_value=settings):
			_notify_agents(JID, "customer follow-up", "Test Sender", self.line, settings)

		for agent in self.agents:
			self.assertEqual(
				len(self._notifications_for(agent)), 0,
				"no notification may be created while the chat is being attended",
			)

	def test_unset_quiet_period_falls_back_to_the_default(self):
		"""Unset must not read as 0, which would mean notify on every message."""
		from helpdesk.integrations import wa

		settings = frappe._dict()  # a Single predating the field
		self.assertIsNone(settings.get("notification_quiet_minutes"))
		with patch.object(wa, "_shared_settings", return_value=settings):
			with patch.object(wa.frappe.db, "count", return_value=1) as counted:
				wa._notify_agents(JID, "hi", "Sender", self.line, settings)

		counted.assert_called_once()
		self.assertEqual(
			len(self._notifications_for(self.agents[0])), 0,
			"the default quiet period should have suppressed this",
		)

	def test_group_chats_notify_like_direct_chats(self):
		from helpdesk.integrations.wa import _notify_agents

		group_jid = "999notiftestgroup@g.us"
		self.addCleanup(frappe.db.delete, "HD Notification", {"reference_wa_jid": group_jid})
		settings = frappe._dict(notification_quiet_minutes=0)
		with patch("helpdesk.integrations.wa._shared_settings", return_value=settings):
			_notify_agents(group_jid, "group msg", "Someone", self.line, settings)

		for agent in self.agents:
			self.assertEqual(
				len(frappe.get_all("HD Notification", filters={
					"reference_wa_jid": group_jid, "user_to": agent, "read": 0
				})), 1,
			)
