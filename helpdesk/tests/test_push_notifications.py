"""Web push leaves the request that created the notification.

HD Notification.after_insert used to call the push service inline: one HTTPS
request per subscription of the notified agent, with no timeout, inside
whatever request inserted the row. For WA Line that request is the Evolution
webhook, which Evolution blocks on, and one unattended incoming message
creates a notification per active agent.
"""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

PUSH = "helpdesk.helpdesk.api.push_notifications.send_push_to_user"


class TestPushNotifications(FrappeTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		self.addCleanup(
			frappe.db.delete, "HD Notification", {"reference_wa_jid": "999pushtest@s.whatsapp.net"}
		)

	def test_after_insert_hands_push_to_a_job(self):
		with patch("frappe.enqueue") as enqueue, patch(PUSH) as push:
			frappe.get_doc({
				"doctype": "HD Notification",
				"user_from": "Administrator",
				"user_to": "Administrator",
				"notification_type": "WhatsApp",
				"reference_wa_jid": "999pushtest@s.whatsapp.net",
				"message": "hello",
			}).insert(ignore_permissions=True)

		push.assert_not_called()
		calls = [c for c in enqueue.call_args_list if c.args and c.args[0] == PUSH]
		self.assertEqual(len(calls), 1)
		kwargs = calls[0].kwargs
		self.assertEqual(kwargs.get("queue"), "default")
		self.assertTrue(kwargs.get("enqueue_after_commit"))
		self.assertEqual(kwargs.get("user"), "Administrator")
		self.assertEqual(kwargs.get("title"), "New WhatsApp message")
		self.assertEqual(kwargs.get("body"), "hello")

	def test_webpush_call_is_bounded(self):
		from helpdesk.helpdesk.api import push_notifications as pn

		settings = SimpleNamespace(
			vapid_public_key="pub", vapid_private_key="priv", vapid_email="ops@example.com",
			get_password=lambda _f: "priv",
		)
		sub = SimpleNamespace(name="s1", endpoint="https://push.example/x", keys_p256dh="p", keys_auth="a")
		with patch.object(pn, "_get_settings", return_value=settings), patch.object(
			pn.frappe, "get_all", return_value=[sub]
		), patch("pywebpush.webpush") as webpush:
			pn.send_push_to_user("Administrator", "t", "b", url="/helpdesk", tag="x")

		webpush.assert_called_once()
		self.assertEqual(webpush.call_args.kwargs.get("timeout"), 10)
