import unittest

import frappe

from helpdesk.tests.settings_guard import restore_bot_settings, snapshot_bot_settings

_settings_snapshot = None


def setUpModule():
	global _settings_snapshot
	_settings_snapshot = snapshot_bot_settings()


def tearDownModule():
	restore_bot_settings(_settings_snapshot)


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

	def test_search_falls_back_to_like_when_no_embeddings(self):
		# Semantic path returns nothing (e.g. index not built) → whole-query LIKE fallback.
		if not frappe.db.table_exists("HD Article"):
			self.skipTest("HD Article table not present")
		from unittest.mock import patch

		from helpdesk.integrations.bot import _search_kb

		with patch("helpdesk.integrations.embeddings.search_articles", return_value=[]):
			results = _search_kb("Reset Password", limit=3)
		self.assertIsInstance(results, list)
		titles = [r["title"] for r in results]
		self.assertIn("Bot Test Reset Password", titles)

	def test_search_returns_empty_on_no_match(self):
		if not frappe.db.table_exists("HD Article"):
			self.skipTest("HD Article table not present")
		from unittest.mock import patch

		from helpdesk.integrations.bot import _search_kb

		with patch("helpdesk.integrations.embeddings.search_articles", return_value=[]):
			results = _search_kb("xyzzy_nonexistent_query_12345", limit=3)
		self.assertEqual(results, [])

	def test_search_prefers_semantic_results(self):
		from unittest.mock import patch

		from helpdesk.integrations.bot import _search_kb

		semantic = [{"name": "a1", "title": "Semantic Hit", "content": "x", "outline_doc_id": None}]
		with patch("helpdesk.integrations.embeddings.search_articles", return_value=semantic):
			results = _search_kb("anything at all", limit=3)
		self.assertEqual(results, semantic)

	def test_fallback_respects_category_allowlist(self):
		if not frappe.db.table_exists("HD Article"):
			self.skipTest("HD Article table not present")
		from unittest.mock import patch

		from helpdesk.integrations.bot import _search_kb

		category = frappe.db.get_value("HD Article", {"title": "Bot Test Reset Password"}, "category")
		other = frappe.get_doc(
			{"doctype": "HD Article Category", "category_name": "Bot Test Other Category"}
		).insert(ignore_permissions=True)
		try:
			with patch("helpdesk.integrations.embeddings.search_articles", return_value=[]):
				allowed = _search_kb("Reset Password", limit=3, allowed_categories=[category] if category else None)
				blocked = _search_kb("Reset Password", limit=3, allowed_categories=[other.name])
			if category:
				self.assertIn("Bot Test Reset Password", [r["title"] for r in allowed])
			self.assertNotIn("Bot Test Reset Password", [r["title"] for r in blocked])
		finally:
			frappe.delete_doc("HD Article Category", other.name, ignore_permissions=True, force=True)


class TestPriorCustomerContext(unittest.TestCase):
	def test_returns_empty_for_ticket_without_history(self):
		from helpdesk.integrations.bot import _get_prior_customer_context

		ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "Prior context test ticket",
				"raised_by": "prior-context-test@example.com",
			}
		).insert(ignore_permissions=True)
		try:
			self.assertEqual(_get_prior_customer_context("waba", None, None, ticket.name), "")
		finally:
			frappe.delete_doc("HD Ticket", ticket.name, ignore_permissions=True, force=True)

	def test_returns_empty_on_bad_args(self):
		from helpdesk.integrations.bot import _get_prior_customer_context

		self.assertEqual(_get_prior_customer_context("wa_line", None, None, None), "")
		self.assertEqual(_get_prior_customer_context("waba", None, None, None), "")


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

		from helpdesk.integrations.bot import _BotState, _escalate

		with patch("helpdesk.integrations.bot.send_wa_reply"):
			_escalate(_BotState(self.ticket.name, None, None))

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

		from helpdesk.integrations.bot import _BotState, _escalate

		with patch("helpdesk.integrations.bot.send_wa_reply") as mock_send:
			_escalate(_BotState(self.ticket.name, None, None))
			# system=True: an escalation message is automated, so it must not
			# assign the ticket or move it out of the agents' queue.
			mock_send.assert_called_once_with(
				ticket=self.ticket.name, message="Test escalation message", system=True
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


class TestBotDoesNotHandleIdentity(unittest.TestCase):
	"""The bot must never decide who a customer is.

	It used to ask for a company name, run the answer through an LLM extractor,
	and then either link the ticket to an HD Customer whose name matched or
	create one and link that — with no human in the loop. That let anyone attach
	themselves to a real customer's record by typing its name, and filled the
	customer list with extracted strings on the miss path. Identity now belongs
	to helpdesk.integrations.wa_verification, which records a claim and leaves
	the decision to an agent.
	"""

	PREFIX = "_test-botident-"

	def setUp(self):
		frappe.set_user("Administrator")
		self.cleanup()
		# A raised_by of "Administrator" resolves to a real contact, and
		# HD Ticket.set_customer then derives a customer from it — which would
		# have meant this fixture never exercised the no-customer path at all.
		# The assertion below is what caught that.
		self.ticket = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": self.PREFIX + "no customer",
			"raised_by": self.PREFIX + frappe.generate_hash(length=6) + "@example.invalid",
		}).insert(ignore_permissions=True)
		self.assertFalse(
			frappe.db.get_value("HD Ticket", self.ticket.name, "customer"),
			"fixture must start with no customer, or the test proves nothing",
		)
		frappe.db.commit()
		self.addCleanup(self.cleanup)

	def cleanup(self):
		for t in frappe.get_all(
			"HD Ticket", filters={"subject": ["like", self.PREFIX + "%"]}, pluck="name"
		):
			frappe.delete_doc("HD Ticket", t, force=True, ignore_permissions=True,
			                  delete_permanently=True)
		frappe.db.sql(
			"DELETE FROM `tabWhatsApp Message` WHERE message_id LIKE %s", (self.PREFIX + "%",)
		)
		for c in frappe.get_all(
			"HD Customer", filters={"customer_name": ["like", "%" + self.PREFIX + "%"]},
			pluck="name",
		):
			frappe.delete_doc("HD Customer", c, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _bot_settings(self):
		return frappe._dict(
			is_enabled=1,
			min_message_words=1,
			conversation_mode="Multi-turn",
			max_bot_replies=3,
			clarification_message_enabled=0,
			clarification_message="",
			kb_search_limit=3,
			enable_gap_tracking=0,
			auto_escalate_on_no_kb=0,
			system_prompt="You are a helpful support assistant.",
			escalation_message_enabled=0,
			escalation_message="",
		)

	def _run(self, text):
		"""Drive one inbound message through the bot and return the send mock."""
		from unittest.mock import patch

		with patch("frappe.enqueue"):
			msg = frappe.get_doc({
				"doctype": "WhatsApp Message",
				"type": "Incoming",
				"from": "254700999888",
				"message": text,
				"content_type": "text",
				"message_id": self.PREFIX + frappe.generate_hash(length=8),
				"reference_doctype": "HD Ticket",
				"reference_name": self.ticket.name,
			}).insert(ignore_permissions=True)
		frappe.db.commit()

		from helpdesk.integrations import bot

		with (
			patch.object(bot, "_bot_settings", return_value=self._bot_settings()),
			patch.object(bot, "_combined_kb_search", return_value=[]),
			patch.object(bot, "send_wa_reply") as mock_send,
			patch("helpdesk.integrations.llm.chat", return_value="Here is an answer."),
			patch(
				"helpdesk.integrations.embeddings.search_resolved_tickets", return_value=[]
			),
		):
			bot.process_message(msg.name, "waba")
		return mock_send

	def test_a_company_name_does_not_create_a_customer(self):
		before = frappe.db.count("HD Customer")
		self._run("We are " + self.PREFIX + "Acme Corporation")
		self.assertEqual(
			frappe.db.count("HD Customer"), before,
			"the bot invented an HD Customer from what the customer typed",
		)

	def test_a_company_name_does_not_link_the_ticket(self):
		"""The dangerous half: naming a real customer must not attach you to it."""
		existing = frappe.get_doc({
			"doctype": "HD Customer", "customer_name": self.PREFIX + "Real Customer Ltd",
		}).insert(ignore_permissions=True).name

		self._run("We are " + self.PREFIX + "Real Customer Ltd")

		self.assertFalse(
			frappe.db.get_value("HD Ticket", self.ticket.name, "customer"),
			f"the bot linked the ticket to {existing} on a typed name alone",
		)

	def test_a_ticket_with_no_customer_still_gets_answered(self):
		"""The removed branch used to short-circuit here and ask for a company
		name instead of helping. An unidentified customer still gets support."""
		mock_send = self._run("my printer will not connect to the network at all")
		self.assertTrue(mock_send.called, "the bot did not reply at all")
		sent = mock_send.call_args.kwargs.get("message", "")
		self.assertNotIn("company name", sent.lower())


class TestBotRepliesAreSystemSends(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# _BotState reads the ticket on construction, so this needs to be real.
		self.ticket = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": "_test-botsys- reply shape",
			"raised_by": "_test-botsys-" + frappe.generate_hash(length=6) + "@example.invalid",
		}).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(frappe.db.commit)
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", self.ticket.name,
			force=True, ignore_permissions=True,
		)

	def test_send_reply_does_not_claim_or_move_the_ticket(self):
		"""A bot reply is not an agent reply: it must not assign the ticket to
		whatever user the job runs as, nor move it out of the agents' queue."""
		from unittest.mock import patch

		from helpdesk.integrations.bot import _BotState

		with patch("helpdesk.integrations.bot.send_wa_reply") as mock_send:
			_BotState(self.ticket.name, None, None).send_reply("hello")

		self.assertTrue(mock_send.call_args.kwargs.get("system"))

	def test_wa_line_replies_are_system_sends_too(self):
		from unittest.mock import patch

		from helpdesk.integrations.bot import _BotState

		with patch("helpdesk.integrations.bot.send_wa_reply") as mock_send:
			_BotState(None, "2547000@s.whatsapp.net", "some-line").send_reply("hello")

		self.assertTrue(mock_send.call_args.kwargs.get("system"))


class TestSuggestAgentReplyIsGuarded(unittest.TestCase):
	def test_a_non_agent_is_refused(self):
		"""@frappe.whitelist() alone bypasses permissions. Without the guard any
		authenticated user — portal Customers included — could read a draft built
		from someone else's WhatsApp thread, and spend a billable LLM call doing it."""
		from helpdesk.integrations.bot import suggest_agent_reply

		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			suggest_agent_reply("any-ticket")
