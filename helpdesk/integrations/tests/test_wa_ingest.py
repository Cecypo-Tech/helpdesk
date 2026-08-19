"""Inbound ingestion runs in a job, not in Meta's webhook request."""

import unittest
from unittest.mock import patch

import frappe

from helpdesk.integrations import wa_ingest

PREFIX = "_test-ingest-"
NUMBER = "254700222333"


def _settings(**overrides):
	"""Stand-in for WhatsApp Helpdesk Settings so tests don't mutate the singleton."""
	base = frappe._dict(
		enabled=1,
		unknown_contact_action="Create Contact and Ticket",
		new_conversation_timeout_hours=24,
		placeholder_email_domain="whatsapp.placeholder.local",
		customer_reply_status=None,
		agent_reply_status=None,
		default_ticket_type=None,
		default_team=None,
	)
	base.update(overrides)
	return base


class _SettingsMixin:
	def setUp(self):
		frappe.set_user("Administrator")
		patcher = patch(
			"helpdesk.integrations.wa._fw_settings", return_value=_settings()
		)
		patcher.start()
		self.addCleanup(patcher.stop)


def _cleanup():
	for ticket in frappe.get_all(
		"HD Ticket", filters={"subject": ["like", PREFIX + "%"]}, pluck="name"
	):
		frappe.delete_doc(
			"HD Ticket", ticket, force=True, ignore_permissions=True, delete_permanently=True
		)
	frappe.db.sql(
		"DELETE FROM `tabWhatsApp Message` WHERE message_id LIKE %s", (PREFIX + "%",)
	)
	for contact in frappe.get_all(
		"Contact", filters={"first_name": ["like", PREFIX + "%"]}, pluck="name"
	):
		frappe.delete_doc(
			"Contact", contact, force=True, ignore_permissions=True, delete_permanently=True
		)
	frappe.db.commit()


def _insert(message_id, message=None):
	doc = frappe.get_doc(
		{
			"doctype": "WhatsApp Message",
			"type": "Incoming",
			"from": NUMBER,
			"message": message or (PREFIX + "hello"),
			"message_id": message_id,
			"content_type": "text",
			"profile_name": PREFIX + "Sender",
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc


class TestWaIngestEnqueue(_SettingsMixin, unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		_cleanup()

	@classmethod
	def tearDownClass(cls):
		_cleanup()

	def tearDown(self):
		_cleanup()

	def test_insert_enqueues_instead_of_linking(self):
		# The whole point: the webhook request stores the row and returns. If
		# linking happened inline again, this would come back with a ticket.
		with patch("frappe.enqueue") as enqueued:
			doc = _insert(PREFIX + "enqueue")

		self.assertTrue(enqueued.called)
		kwargs = enqueued.call_args.kwargs
		self.assertEqual(kwargs.get("message_name"), doc.name)
		self.assertTrue(kwargs.get("enqueue_after_commit"))
		self.assertIsNone(
			frappe.db.get_value("WhatsApp Message", doc.name, "reference_name")
			or None
		)

	def test_job_links_a_ticket(self):
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "link")

		wa_ingest.process_incoming_message(doc.name)

		ticket = frappe.db.get_value("WhatsApp Message", doc.name, "reference_name")
		self.assertTrue(ticket)
		self.assertEqual(
			frappe.db.get_value("WhatsApp Message", doc.name, "reference_doctype"),
			"HD Ticket",
		)

	def test_job_is_idempotent(self):
		# RQ retries, and the sweeper can enqueue a message whose first job is
		# merely slow. Neither may raise a second ticket for one message.
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "twice")

		wa_ingest.process_incoming_message(doc.name)
		first = frappe.db.get_value("WhatsApp Message", doc.name, "reference_name")
		wa_ingest.process_incoming_message(doc.name)
		second = frappe.db.get_value("WhatsApp Message", doc.name, "reference_name")

		self.assertEqual(first, second)

	def test_job_on_a_deleted_message_is_a_no_op(self):
		# The duplicate guard drops rows after the enqueue has been scheduled.
		wa_ingest.process_incoming_message("does-not-exist")

	def test_bot_runs_only_after_the_ticket_link_exists(self):
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "bot")

		with patch("helpdesk.integrations.bot.handle_whatsapp_message") as bot_hook:
			wa_ingest.process_incoming_message(doc.name)

		self.assertTrue(bot_hook.called)
		passed = bot_hook.call_args.args[0]
		self.assertEqual(passed.reference_doctype, "HD Ticket")
		self.assertTrue(passed.reference_name)

	def test_verification_runs_on_the_linked_message(self):
		# The wiring, asserted without any WABA transport mock: spying on
		# handle_incoming proves the call exists, that it is reached after the
		# bot block rather than being unreachable behind it, and that it gets a
		# doc that already carries its ticket. Everything the verification state
		# machine does past that point is covered in test_contact_verification.
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "verify")

		with patch(
			"helpdesk.integrations.wa_verification.handle_incoming"
		) as verification_hook:
			wa_ingest.process_incoming_message(doc.name)

		self.assertTrue(verification_hook.called)
		passed = verification_hook.call_args.args[0]
		self.assertEqual(passed.reference_doctype, "HD Ticket")
		self.assertTrue(passed.reference_name)


class TestWaIngestSweeper(_SettingsMixin, unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		_cleanup()

	@classmethod
	def tearDownClass(cls):
		_cleanup()

	def tearDown(self):
		_cleanup()

	def _age(self, name, expr):
		frappe.db.sql(
			f"UPDATE `tabWhatsApp Message` SET creation = {expr} WHERE name = %s", (name,)
		)
		frappe.db.commit()

	def test_unlinked_message_is_re_enqueued(self):
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "orphan")
		self._age(doc.name, "NOW() - INTERVAL 30 MINUTE")

		with patch("frappe.enqueue") as enqueued:
			result = wa_ingest.sweep_unlinked_messages()

		self.assertGreaterEqual(result["swept"], 1)
		self.assertIn(
			doc.name, [c.kwargs.get("message_name") for c in enqueued.call_args_list]
		)

	def test_recent_message_is_left_for_its_own_job(self):
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "recent")

		with patch("frappe.enqueue") as enqueued:
			wa_ingest.sweep_unlinked_messages()

		self.assertNotIn(
			doc.name, [c.kwargs.get("message_name") for c in enqueued.call_args_list]
		)

	def test_linked_message_is_not_swept(self):
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "linked")
		wa_ingest.process_incoming_message(doc.name)
		self._age(doc.name, "NOW() - INTERVAL 30 MINUTE")

		with patch("frappe.enqueue") as enqueued:
			wa_ingest.sweep_unlinked_messages()

		self.assertNotIn(
			doc.name, [c.kwargs.get("message_name") for c in enqueued.call_args_list]
		)

	def test_ancient_message_is_left_alone(self):
		# Re-running ingestion over months-old history would raise tickets for
		# conversations that were handled long ago.
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "ancient")
		self._age(doc.name, "NOW() - INTERVAL 30 DAY")

		with patch("frappe.enqueue") as enqueued:
			wa_ingest.sweep_unlinked_messages()

		self.assertNotIn(
			doc.name, [c.kwargs.get("message_name") for c in enqueued.call_args_list]
		)


class TestWaIngestHardening(_SettingsMixin, unittest.TestCase):
	"""A message must keep its ticket even when the trimmings fail.

	On 2026-08-13 one unguarded notification threw from HD Ticket.after_insert,
	took the insert with it, and stopped new WhatsApp conversations becoming
	tickets — invisibly, because inside the webhook request the rollback erased
	the message and its Notification Log row too.
	"""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		_cleanup()

	@classmethod
	def tearDownClass(cls):
		_cleanup()

	def tearDown(self):
		_cleanup()

	def _linked(self, name):
		return frappe.db.get_value("WhatsApp Message", name, "reference_name")

	def test_ticket_survives_a_failing_agent_notification(self):
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "notify-boom")

		with patch(
			"helpdesk.integrations.wa._notify_fw_agents",
			side_effect=Exception("smtp is down"),
		):
			wa_ingest.process_incoming_message(doc.name)

		self.assertTrue(self._linked(doc.name))

	def test_ticket_survives_a_failing_realtime_publish(self):
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "realtime-boom")

		# Scoped to our publisher: patching frappe.publish_realtime itself breaks
		# every unrelated doc write in the same call, Contact creation included.
		with patch(
			"helpdesk.integrations.wa._publish_fw_message",
			side_effect=Exception("redis is down"),
		):
			wa_ingest.process_incoming_message(doc.name)

		self.assertTrue(self._linked(doc.name))

	def test_ticket_survives_a_failing_bot(self):
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "bot-boom")

		with patch(
			"helpdesk.integrations.bot.handle_whatsapp_message",
			side_effect=Exception("bot exploded"),
		):
			wa_ingest.process_incoming_message(doc.name)

		self.assertTrue(self._linked(doc.name))

	def test_incoming_realtime_is_emitted_immediately(self):
		# after_commit in a job fires only when the worker finishes, which left
		# the agent's thread stale until they clicked away and back.
		with patch("frappe.enqueue"):
			doc = _insert(PREFIX + "realtime-now")

		with patch("helpdesk.integrations.wa.frappe.publish_realtime") as pub:
			wa_ingest.process_incoming_message(doc.name)

		events = [c for c in pub.call_args_list if c.args and c.args[0] == "helpdesk:whatsapp-message"]
		self.assertTrue(events)
		self.assertFalse(events[-1].kwargs.get("after_commit"))
