import frappe
from frappe.tests.utils import FrappeTestCase


class TestHDTask(FrappeTestCase):
	def test_create_task(self):
		task = frappe.get_doc(
			{
				"doctype": "HD Task",
				"title": "Test Task",
				"status": "Backlog",
			}
		)
		task.insert(ignore_permissions=True)
		self.assertEqual(task.status, "Backlog")
		task.delete(ignore_permissions=True)
