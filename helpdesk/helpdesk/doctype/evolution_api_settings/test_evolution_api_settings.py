import frappe
import unittest


class TestEvolutionApiSettings(unittest.TestCase):
	def test_singleton_exists_after_migrate(self):
		self.assertTrue(frappe.db.exists("DocType", "Evolution API Settings"))
		doc = frappe.get_single("Evolution API Settings")
		self.assertIsNotNone(doc)
		# The singleton exists, even if server_url is not yet set
		self.assertEqual(doc.name, "Evolution API Settings")

	def test_required_fields_present(self):
		meta = frappe.get_meta("Evolution API Settings")
		field_names = [f.fieldname for f in meta.fields]
		for required in [
			"enabled",
			"server_url",
			"global_api_key",
			"default_team",
			"unknown_contact_action",
			"notification_quiet_minutes",
		]:
			self.assertIn(required, field_names, f"Missing field: {required}")
