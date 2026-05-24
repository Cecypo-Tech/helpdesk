import frappe
import unittest

class TestEvolutionLine(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		if frappe.db.exists("Evolution Line", {"instance_name": "_test-line"}):
			frappe.delete_doc("Evolution Line", frappe.db.get_value(
				"Evolution Line", {"instance_name": "_test-line"}, "name"), force=True)

	def tearDown(self):
		frappe.db.rollback()

	def test_create_line(self):
		doc = frappe.get_doc({
			"doctype": "Evolution Line",
			"label": "Test Sales",
			"instance_name": "_test-line",
		}).insert(ignore_permissions=True)
		self.assertEqual(doc.instance_name, "_test-line")
		self.assertEqual(doc.label, "Test Sales")

	def test_instance_name_unique(self):
		frappe.get_doc({"doctype": "Evolution Line", "label": "A",
						"instance_name": "_test-line"}).insert(ignore_permissions=True)
		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc({"doctype": "Evolution Line", "label": "B",
							"instance_name": "_test-line"}).insert(ignore_permissions=True)

	def test_required_fields(self):
		meta = frappe.get_meta("Evolution Line")
		field_names = [f.fieldname for f in meta.fields]
		for required in ["label", "instance_name", "connected_user", "group_jids", "blocked_jids"]:
			self.assertIn(required, field_names, f"Missing field: {required}")
