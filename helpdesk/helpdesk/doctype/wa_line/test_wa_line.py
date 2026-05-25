import frappe
import unittest

class TestWALine(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		if frappe.db.exists("WA Line", {"instance_name": "_test-line"}):
			frappe.delete_doc("WA Line", frappe.db.get_value(
				"WA Line", {"instance_name": "_test-line"}, "name"), force=True)

	def tearDown(self):
		frappe.db.rollback()

	def test_create_line(self):
		doc = frappe.get_doc({
			"doctype": "WA Line",
			"label": "Test Sales",
			"instance_name": "_test-line",
		}).insert(ignore_permissions=True)
		self.assertEqual(doc.instance_name, "_test-line")
		self.assertEqual(doc.label, "Test Sales")

	def test_instance_name_unique(self):
		frappe.get_doc({"doctype": "WA Line", "label": "A",
						"instance_name": "_test-line"}).insert(ignore_permissions=True)
		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc({"doctype": "WA Line", "label": "B",
							"instance_name": "_test-line"}).insert(ignore_permissions=True)

	def test_required_fields(self):
		meta = frappe.get_meta("WA Line")
		field_names = [f.fieldname for f in meta.fields]
		for required in ["label", "instance_name", "connected_user", "group_jids", "blocked_jids"]:
			self.assertIn(required, field_names, f"Missing field: {required}")
