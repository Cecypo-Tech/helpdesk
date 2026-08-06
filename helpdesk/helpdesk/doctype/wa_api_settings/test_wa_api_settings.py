import frappe
import unittest


class TestWAAPISettings(unittest.TestCase):
	def test_singleton_exists_after_migrate(self):
		self.assertTrue(frappe.db.exists("DocType", "WA API Settings"))
		doc = frappe.get_single("WA API Settings")
		self.assertIsNotNone(doc)
		self.assertEqual(doc.name, "WA API Settings")

	def test_required_fields_present(self):
		meta = frappe.get_meta("WA API Settings")
		field_names = [f.fieldname for f in meta.fields]
		# Connection settings only. default_team, unknown_contact_action and
		# notification_quiet_minutes were asserted here when they were global;
		# ticket defaults are now per-instance on WA Line, and the notification
		# window lives on WhatsApp Helpdesk Settings.
		for required in [
			"enabled",
			"server_url",
			"global_api_key",
			"placeholder_email_domain",
		]:
			self.assertIn(required, field_names, f"Missing field: {required}")

	def test_ticket_defaults_live_on_the_line(self):
		# Guards the split: if these ever migrate back to the global singleton,
		# this is where it should be noticed.
		line_fields = [f.fieldname for f in frappe.get_meta("WA Line").fields]
		for expected in ("default_team", "default_ticket_type"):
			self.assertIn(expected, line_fields)
