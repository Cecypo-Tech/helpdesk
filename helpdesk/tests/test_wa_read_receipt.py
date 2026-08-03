from unittest.mock import patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from helpdesk.integrations import wa


class TestWAReadReceipt(FrappeTestCase):
	"""Cover the WABA read-receipt path in mark_wa_messages_read().

	Regression cover for the error-log storm: a receipt Meta refuses used to be
	retried forever, re-logging an empty "None\\n{}" error on every ticket open
	and leaving the message stuck in the tab's unread badge.

	No test here touches the network — the site has live WABA credentials, and a
	read receipt against a real message_id would mark a real customer's message
	read. Meta's responses are simulated at the requests boundary instead.
	"""

	def setUp(self):
		# mark_wa_messages_read() commits mid-test. Registering this first
		# (LIFO) makes it run last, after the addCleanup deletes below, so
		# those deletes are committed rather than rolled back at teardown.
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")

		if not frappe.db.exists("DocType", "WhatsApp Message"):
			self.skipTest("frappe_whatsapp is not installed")

		self.account = self._account()
		self.ticket = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": "WA read receipt test ticket",
			"raised_by": "wa-read-receipt-test@example.com",
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", self.ticket.name, ignore_permissions=True, force=True
		)

	def _account(self):
		name = frappe.db.get_value("WhatsApp Account", {"is_default_incoming": 1}, "name")
		if not name:
			self.skipTest("no default incoming WhatsApp Account configured")
		# get_cached_doc is what the code under test uses; clear it so per-test
		# mutations of allow_auto_read_receipt are picked up.
		frappe.clear_document_cache("WhatsApp Account", name)
		return frappe.get_doc("WhatsApp Account", name)

	def _make_message(self, age_hours=0):
		doc = frappe.get_doc({
			"doctype": "WhatsApp Message",
			"type": "Incoming",
			"from": "254700000000",
			"message": "hello",
			"content_type": "text",
			"message_id": f"wamid.test.{frappe.generate_hash(length=10)}",
			"status": "received",
			"reference_doctype": "HD Ticket",
			"reference_name": self.ticket.name,
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "WhatsApp Message", doc.name, ignore_permissions=True, force=True
		)
		if age_hours:
			frappe.db.set_value(
				"WhatsApp Message",
				doc.name,
				"creation",
				frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-age_hours),
				update_modified=False,
			)
		self.addCleanup(self._clear_cache_keys, doc.name)
		return doc

	def _clear_cache_keys(self, name):
		frappe.cache().delete_value(f"wa_read_receipt_retry:{name}")
		frappe.cache().delete_value(f"wa_read_receipt_attempts:{name}")

	def _clear_backoff(self, name):
		"""Skip past the 15-minute backoff so the next attempt is allowed."""
		frappe.cache().delete_value(f"wa_read_receipt_retry:{name}")

	def _status(self, name):
		return frappe.db.get_value("WhatsApp Message", name, "status")

	def _error_logs_for(self, name):
		return frappe.get_all(
			"Error Log", filters={"reference_doctype": "WhatsApp Message", "reference_name": name}
		)

	# ── the give-up path ──────────────────────────────────────────────────

	def test_persistent_failure_gives_up_after_max_attempts_and_logs_once(self):
		msg = self._make_message()

		with patch.object(wa, "_post_wa_read_receipt", return_value=(False, "HTTP 400: bad id")) as post:
			for _ in range(wa._WA_RECEIPT_MAX_ATTEMPTS + 2):
				wa.mark_wa_messages_read(ticket=self.ticket.name)
				self._clear_backoff(msg.name)

		# Stops calling Meta once it has given up, however often the tab reopens.
		self.assertEqual(post.call_count, wa._WA_RECEIPT_MAX_ATTEMPTS)
		# Exactly one Error Log, carrying the real response rather than "None\n{}".
		logs = self._error_logs_for(msg.name)
		self.assertEqual(len(logs), 1)
		self.assertIn("HTTP 400: bad id", frappe.db.get_value("Error Log", logs[0].name, "error"))
		# Settled locally, so it leaves the unread badge.
		self.assertEqual(self._status(msg.name), "marked as read")
		self.assertEqual(wa.get_ticket_wa_unread_count(str(self.ticket.name)), 0)

	def test_backoff_blocks_a_retry_inside_the_window(self):
		msg = self._make_message()

		with patch.object(wa, "_post_wa_read_receipt", return_value=(False, "HTTP 500: boom")) as post:
			wa.mark_wa_messages_read(ticket=self.ticket.name)
			wa.mark_wa_messages_read(ticket=self.ticket.name)  # no _clear_backoff

		self.assertEqual(post.call_count, 1)
		self.assertEqual(self._status(msg.name), "received")

	# ── the age cutoff ────────────────────────────────────────────────────

	def test_message_older_than_the_window_is_settled_without_an_api_call(self):
		msg = self._make_message(age_hours=wa._WA_RECEIPT_MAX_AGE_HOURS + 6)

		with patch.object(wa, "_post_wa_read_receipt") as post:
			marked = wa.mark_wa_messages_read(ticket=self.ticket.name)

		post.assert_not_called()
		self.assertEqual(marked, 1)
		self.assertEqual(self._status(msg.name), "marked as read")
		self.assertEqual(self._error_logs_for(msg.name), [])

	# ── the happy path ────────────────────────────────────────────────────

	def test_successful_receipt_marks_read_and_logs_nothing(self):
		msg = self._make_message()

		with patch.object(wa, "_post_wa_read_receipt", return_value=(True, "")) as post:
			marked = wa.mark_wa_messages_read(ticket=self.ticket.name)

		self.assertEqual(post.call_count, 1)
		self.assertEqual(marked, 1)
		self.assertEqual(self._status(msg.name), "marked as read")
		self.assertEqual(self._error_logs_for(msg.name), [])

	# ── the kill switch ───────────────────────────────────────────────────

	def test_account_kill_switch_suppresses_the_call(self):
		msg = self._make_message()
		original = self.account.allow_auto_read_receipt
		frappe.db.set_value("WhatsApp Account", self.account.name, "allow_auto_read_receipt", 0)
		frappe.clear_document_cache("WhatsApp Account", self.account.name)
		self.addCleanup(frappe.clear_document_cache, "WhatsApp Account", self.account.name)
		self.addCleanup(
			frappe.db.set_value,
			"WhatsApp Account",
			self.account.name,
			"allow_auto_read_receipt",
			original,
		)

		with patch.object(wa, "_post_wa_read_receipt") as post:
			wa.mark_wa_messages_read(ticket=self.ticket.name)

		post.assert_not_called()
		self.assertEqual(self._status(msg.name), "received")

	# ── error capture ─────────────────────────────────────────────────────

	def test_http_error_is_reported_with_status_and_body(self):
		response = requests.Response()
		response.status_code = 400
		response._content = b'{"error":{"message":"Invalid message id","code":100}}'
		error = requests.exceptions.HTTPError(response=response)

		with patch.object(wa._requests, "post", side_effect=error):
			ok, detail = wa._post_wa_read_receipt("wamid.bogus", self.account)

		self.assertFalse(ok)
		self.assertIn("HTTP 400", detail)
		self.assertIn("Invalid message id", detail)

	# ── account resolution ────────────────────────────────────────────────

	def test_unknown_account_link_resolves_to_none_instead_of_raising(self):
		# A message pointing at a since-deleted account must not 500 the whole
		# mark-as-read call for every other message on the ticket.
		self.assertIsNone(wa._wa_account_for_message("No Such WhatsApp Account 12345"))

	def test_empty_account_link_falls_back_to_the_default_incoming_account(self):
		self.assertEqual(wa._wa_account_for_message(None).name, self.account.name)

	def test_transport_error_is_reported_with_its_type(self):
		with patch.object(wa._requests, "post", side_effect=requests.exceptions.ConnectTimeout("slow")):
			ok, detail = wa._post_wa_read_receipt("wamid.bogus", self.account)

		self.assertFalse(ok)
		self.assertIn("ConnectTimeout", detail)
