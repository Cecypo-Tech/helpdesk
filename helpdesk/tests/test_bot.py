import unittest

import frappe


class TestWordFilter(unittest.TestCase):
	def test_empty_string_is_short(self):
		from helpdesk.integrations.bot import _is_short_message

		self.assertTrue(_is_short_message("", 3))

	def test_none_is_short(self):
		from helpdesk.integrations.bot import _is_short_message

		self.assertTrue(_is_short_message(None, 3))

	def test_greeting_is_short(self):
		from helpdesk.integrations.bot import _is_short_message

		self.assertTrue(_is_short_message("hi", 3))
		self.assertTrue(_is_short_message("hello there", 3))

	def test_real_query_is_not_short(self):
		from helpdesk.integrations.bot import _is_short_message

		self.assertFalse(_is_short_message("my application is crashing on login", 3))

	def test_exactly_min_words_is_not_short(self):
		from helpdesk.integrations.bot import _is_short_message

		self.assertFalse(_is_short_message("one two three", 3))


class TestKBSearch(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		if not frappe.db.table_exists("HD Article"):
			return
		if not frappe.db.exists("HD Article", {"title": "Bot Test Reset Password"}):
			frappe.get_doc(
				{
					"doctype": "HD Article",
					"title": "Bot Test Reset Password",
					"content": "To reset your password, click Forgot Password on the login page.",
					"status": "Published",
				}
			).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		if not frappe.db.table_exists("HD Article"):
			return
		frappe.db.delete("HD Article", {"title": "Bot Test Reset Password"})

	def test_search_returns_matching_article(self):
		if not frappe.db.table_exists("HD Article"):
			self.skipTest("HD Article table not present")
		from helpdesk.integrations.bot import _search_kb

		results = _search_kb("reset password", limit=3)
		self.assertIsInstance(results, list)
		titles = [r["title"] for r in results]
		self.assertIn("Bot Test Reset Password", titles)

	def test_search_returns_empty_on_no_match(self):
		if not frappe.db.table_exists("HD Article"):
			self.skipTest("HD Article table not present")
		from helpdesk.integrations.bot import _search_kb

		results = _search_kb("xyzzy_nonexistent_query_12345", limit=3)
		self.assertEqual(results, [])


class TestGapTracking(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "Bot gap test ticket",
				"raised_by": "Administrator",
			}
		).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.db.delete("HD Bot Missing KB Query", {"ticket": cls.ticket.name})
		frappe.delete_doc("HD Ticket", cls.ticket.name, ignore_permissions=True, force=True)

	def test_gap_record_inserted(self):
		from helpdesk.integrations.bot import _record_gap

		_record_gap(
			ticket_name=self.ticket.name,
			channel="WA Line",
			query_text="How do I export my data?",
			suggested_title="Data Export Guide",
			suggested_category="Account Management",
		)
		exists = frappe.db.exists(
			"HD Bot Missing KB Query",
			{"ticket": self.ticket.name, "query_text": "How do I export my data?"},
		)
		self.assertTrue(exists)

	def test_gap_record_default_status_is_pending(self):
		from helpdesk.integrations.bot import _record_gap

		_record_gap(
			ticket_name=self.ticket.name,
			channel="WABA",
			query_text="How do I change my billing plan?",
			suggested_title="Billing Plan Guide",
			suggested_category="Billing",
		)
		status = frappe.db.get_value(
			"HD Bot Missing KB Query",
			{"ticket": self.ticket.name, "query_text": "How do I change my billing plan?"},
			"status",
		)
		self.assertEqual(status, "Pending")


class TestEscalation(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "Bot escalation test",
				"raised_by": "Administrator",
			}
		).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.delete_doc("HD Ticket", cls.ticket.name, ignore_permissions=True, force=True)

	def test_escalate_sets_bot_escalated(self):
		from unittest.mock import patch

		settings = frappe.get_single("Helpdesk Bot Settings")
		settings.escalation_message_enabled = 0
		settings.save(ignore_permissions=True)

		from helpdesk.integrations.bot import _escalate

		with patch("helpdesk.integrations.bot.send_wa_reply"):
			_escalate(self.ticket.name)

		val = frappe.db.get_value("HD Ticket", self.ticket.name, "bot_escalated")
		self.assertEqual(val, 1)

	def test_escalate_sends_message_when_enabled(self):
		from unittest.mock import patch

		settings = frappe.get_single("Helpdesk Bot Settings")
		settings.escalation_message_enabled = 1
		settings.escalation_message = "Test escalation message"
		settings.save(ignore_permissions=True)

		# Reset bot_escalated
		frappe.db.set_value("HD Ticket", self.ticket.name, "bot_escalated", 0)

		from helpdesk.integrations.bot import _escalate

		with patch("helpdesk.integrations.bot.send_wa_reply") as mock_send:
			_escalate(self.ticket.name)
			mock_send.assert_called_once_with(
				ticket=self.ticket.name, message="Test escalation message"
			)


class TestDocEventHandlers(unittest.TestCase):
	def test_handle_wa_message_skips_outgoing(self):
		from unittest.mock import patch

		from helpdesk.integrations.bot import handle_wa_message

		doc = frappe.new_doc("WA Message")
		doc.direction = "Outgoing"
		doc.reference_doctype = "HD Ticket"
		doc.reference_name = "TEST-001"

		with patch("frappe.enqueue") as mock_enqueue:
			handle_wa_message(doc)
			mock_enqueue.assert_not_called()

	def test_handle_wa_message_skips_when_bot_disabled(self):
		from unittest.mock import patch

		frappe.db.set_single_value("Helpdesk Bot Settings", "is_enabled", 0)
		frappe.clear_cache()

		from helpdesk.integrations.bot import handle_wa_message

		doc = frappe.new_doc("WA Message")
		doc.direction = "Incoming"
		doc.reference_doctype = "HD Ticket"
		doc.reference_name = "TEST-001"
		doc.line = None

		with patch("frappe.enqueue") as mock_enqueue:
			handle_wa_message(doc)
			mock_enqueue.assert_not_called()

		frappe.db.set_single_value("Helpdesk Bot Settings", "is_enabled", 1)
		frappe.clear_cache()

	def test_handle_whatsapp_message_skips_outgoing(self):
		from unittest.mock import patch

		from helpdesk.integrations.bot import handle_whatsapp_message

		doc = frappe.new_doc("WhatsApp Message")
		doc.type = "Outgoing"
		doc.reference_doctype = "HD Ticket"
		doc.reference_name = "TEST-001"

		with patch("frappe.enqueue") as mock_enqueue:
			handle_whatsapp_message(doc)
			mock_enqueue.assert_not_called()
