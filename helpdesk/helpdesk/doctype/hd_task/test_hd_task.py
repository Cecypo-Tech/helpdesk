import frappe
from frappe.tests.utils import FrappeTestCase


class TestHDTask(FrappeTestCase):
	def setUp(self):
		# Create test tasks
		self.task1 = frappe.get_doc({
			"doctype": "HD Task",
			"title": "Alpha task with special content",
			"status": "Backlog",
			"description": "This description mentions unicorn",
		}).insert(ignore_permissions=True)

		self.task2 = frappe.get_doc({
			"doctype": "HD Task",
			"title": "Beta task",
			"status": "Todo",
		}).insert(ignore_permissions=True)

		# Set tags directly via db
		frappe.db.set_value("HD Task", self.task1.name, "_user_tags", "frontend,bug")
		frappe.db.set_value("HD Task", self.task2.name, "_user_tags", "backend,bug")

	def tearDown(self):
		frappe.delete_doc("HD Task", self.task1.name, ignore_permissions=True, force=True)
		frappe.delete_doc("HD Task", self.task2.name, ignore_permissions=True, force=True)

	def test_create_task(self):
		task = frappe.get_doc({
			"doctype": "HD Task",
			"title": "Test Task",
			"status": "Backlog",
		})
		task.insert(ignore_permissions=True)
		self.assertEqual(task.status, "Backlog")
		task.delete(ignore_permissions=True)

	def test_get_all_task_tags(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import get_all_task_tags
		tags = get_all_task_tags()
		self.assertIn("frontend", tags)
		self.assertIn("bug", tags)
		self.assertIn("backend", tags)
		# Should be sorted and unique
		self.assertEqual(tags, sorted(set(tags)))

	def test_search_tasks_by_title(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import search_tasks
		results = search_tasks("Alpha task")
		self.assertIn(self.task1.name, results)
		self.assertNotIn(self.task2.name, results)

	def test_search_tasks_by_description(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import search_tasks
		results = search_tasks("unicorn")
		self.assertIn(self.task1.name, results)
		self.assertNotIn(self.task2.name, results)

	def test_search_tasks_by_subtask(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import search_tasks, save_task_subtasks
		# Add a subtask to task2 with unique text
		save_task_subtasks(
			self.task2.name,
			[{"title": "Deploy the phoenix service", "status": "Backlog", "due_date": None}]
		)
		results = search_tasks("phoenix")
		self.assertIn(self.task2.name, results)
		self.assertNotIn(self.task1.name, results)

	def test_set_task_field_user_tags(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import set_task_field
		set_task_field(self.task1.name, "_user_tags", "newtag,anothertag")
		saved = frappe.db.get_value("HD Task", self.task1.name, "_user_tags")
		self.assertEqual(saved, "newtag,anothertag")
