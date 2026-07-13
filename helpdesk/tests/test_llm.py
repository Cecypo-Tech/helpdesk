import importlib
import unittest
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
