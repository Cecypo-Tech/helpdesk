import importlib
import unittest
import unittest.mock
from unittest.mock import patch

import frappe

from helpdesk.tests.settings_guard import restore_bot_settings, snapshot_bot_settings

_settings_snapshot = None


def setUpModule():
	global _settings_snapshot
	_settings_snapshot = snapshot_bot_settings()


def tearDownModule():
	restore_bot_settings(_settings_snapshot)


class TestLLMRouting(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		settings = frappe.get_single("Helpdesk Bot Settings")
		settings.is_enabled = 1
		settings.gemini_api_key = "test-gemini-key"
		settings.anthropic_api_key = "test-anthropic-key"
		settings.system_prompt = "You are a test assistant."
		settings.save(ignore_permissions=True)

	def test_routes_to_gemini(self):
		frappe.db.set_single_value("Helpdesk Bot Settings", "llm_provider", "gemini-3.1-flash-lite")
		frappe.clear_cache()
		from helpdesk.integrations import llm
		importlib.reload(llm)
		with patch("helpdesk.integrations.llm._gemini") as mock_gemini:
			mock_gemini.return_value = "gemini reply"
			result = llm.chat([{"role": "user", "content": "hello"}])
			mock_gemini.assert_called_once()
			self.assertEqual(result, "gemini reply")

	def test_routes_to_haiku(self):
		frappe.db.set_single_value("Helpdesk Bot Settings", "llm_provider", "claude-haiku-4-5")
		frappe.clear_cache()
		from helpdesk.integrations import llm
		importlib.reload(llm)
		with patch("helpdesk.integrations.llm._haiku") as mock_haiku:
			mock_haiku.return_value = "haiku reply"
			result = llm.chat([{"role": "user", "content": "hello"}])
			mock_haiku.assert_called_once()
			self.assertEqual(result, "haiku reply")

	def test_images_passed_to_provider(self):
		frappe.db.set_single_value("Helpdesk Bot Settings", "llm_provider", "gemini-3.1-flash-lite")
		frappe.clear_cache()
		from helpdesk.integrations import llm
		importlib.reload(llm)
		with patch("helpdesk.integrations.llm._gemini") as mock_gemini:
			mock_gemini.return_value = "reply"
			fake_image = b"fake-image-bytes"
			llm.chat([{"role": "user", "content": "what is this?"}], images=[fake_image])
			args, kwargs = mock_gemini.call_args
			self.assertEqual(args[1], [fake_image])


class TestLLMTimeouts(unittest.TestCase):
	"""Every model call is bounded. The bot runs in a worker, and an unbounded
	call (the Anthropic SDK defaults to ten minutes) parks that worker for as
	long as the provider feels like taking."""

	@classmethod
	def setUpClass(cls):
		settings = frappe.get_single("Helpdesk Bot Settings")
		settings.gemini_api_key = "test-gemini-key"
		settings.anthropic_api_key = "test-anthropic-key"
		settings.save(ignore_permissions=True)

	def _reload(self):
		frappe.clear_cache()
		from helpdesk.integrations import llm
		importlib.reload(llm)
		return llm

	def test_timeout_defaults_to_thirty_seconds(self):
		frappe.db.set_single_value("Helpdesk Bot Settings", "llm_timeout_seconds", 0)
		llm = self._reload()
		self.assertEqual(llm.request_timeout(), 30)

	def test_timeout_is_read_from_settings(self):
		frappe.db.set_single_value("Helpdesk Bot Settings", "llm_timeout_seconds", 12)
		llm = self._reload()
		self.assertEqual(llm.request_timeout(), 12)

	def test_anthropic_client_is_bounded_and_uses_the_current_model_id(self):
		frappe.db.set_single_value("Helpdesk Bot Settings", "llm_timeout_seconds", 12)
		llm = self._reload()
		import anthropic

		fake_client = unittest.mock.MagicMock()
		fake_client.messages.create.return_value.content = [unittest.mock.MagicMock(text=" hi ")]
		with patch.object(anthropic, "Anthropic", return_value=fake_client) as ctor:
			reply = llm._haiku([{"role": "user", "content": "hello"}], None, llm._settings())
		self.assertEqual(reply, "hi")
		self.assertEqual(ctor.call_args.kwargs.get("timeout"), 12)
		self.assertEqual(ctor.call_args.kwargs.get("max_retries"), 2)
		self.assertEqual(fake_client.messages.create.call_args.kwargs["model"], "claude-haiku-4-5")

	def test_gemini_call_is_bounded(self):
		frappe.db.set_single_value("Helpdesk Bot Settings", "llm_timeout_seconds", 12)
		llm = self._reload()
		import google.generativeai as genai

		fake_model = unittest.mock.MagicMock()
		fake_model.generate_content.return_value.text = " hi "
		with patch.object(genai, "configure"), patch.object(
			genai, "GenerativeModel", return_value=fake_model
		):
			reply = llm._gemini([{"role": "user", "content": "hello"}], None, llm._settings())
		self.assertEqual(reply, "hi")
		opts = fake_model.generate_content.call_args.kwargs.get("request_options")
		self.assertEqual(opts, {"timeout": 12})
