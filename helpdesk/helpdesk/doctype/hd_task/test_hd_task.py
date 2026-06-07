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

	def test_get_my_due_tasks_returns_overdue(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import get_my_due_tasks
		task = frappe.get_doc({
			"doctype": "HD Task",
			"title": "Overdue sentinel",
			"status": "Todo",
		}).insert(ignore_permissions=True)
		frappe.db.set_value("HD Task", task.name, {
			"assigned_to": frappe.session.user,
			"due_date": frappe.utils.add_days(frappe.utils.today(), -1),
		})
		try:
			results = get_my_due_tasks()
			names = [r["name"] for r in results]
			self.assertIn(task.name, names)
		finally:
			frappe.delete_doc("HD Task", task.name, ignore_permissions=True, force=True)

	def test_get_my_due_tasks_includes_due_today(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import get_my_due_tasks
		task = frappe.get_doc({
			"doctype": "HD Task",
			"title": "Due today sentinel",
			"status": "Todo",
		}).insert(ignore_permissions=True)
		frappe.db.set_value("HD Task", task.name, {
			"assigned_to": frappe.session.user,
			"due_date": frappe.utils.today(),
		})
		try:
			results = get_my_due_tasks()
			names = [r["name"] for r in results]
			self.assertIn(task.name, names)
		finally:
			frappe.delete_doc("HD Task", task.name, ignore_permissions=True, force=True)

	def test_get_my_due_tasks_excludes_done(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import get_my_due_tasks
		task = frappe.get_doc({
			"doctype": "HD Task",
			"title": "Done sentinel",
			"status": "Done",
		}).insert(ignore_permissions=True)
		frappe.db.set_value("HD Task", task.name, {
			"assigned_to": frappe.session.user,
			"due_date": frappe.utils.add_days(frappe.utils.today(), -1),
		})
		try:
			results = get_my_due_tasks()
			names = [r["name"] for r in results]
			self.assertNotIn(task.name, names)
		finally:
			frappe.delete_doc("HD Task", task.name, ignore_permissions=True, force=True)

	def test_get_my_due_tasks_excludes_future(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import get_my_due_tasks
		task = frappe.get_doc({
			"doctype": "HD Task",
			"title": "Future sentinel",
			"status": "Todo",
		}).insert(ignore_permissions=True)
		frappe.db.set_value("HD Task", task.name, {
			"assigned_to": frappe.session.user,
			"due_date": frappe.utils.add_days(frappe.utils.today(), 2),
		})
		try:
			results = get_my_due_tasks()
			names = [r["name"] for r in results]
			self.assertNotIn(task.name, names)
		finally:
			frappe.delete_doc("HD Task", task.name, ignore_permissions=True, force=True)

	def test_get_my_due_tasks_excludes_other_user(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import get_my_due_tasks
		task = frappe.get_doc({
			"doctype": "HD Task",
			"title": "Other user sentinel",
			"status": "Todo",
		}).insert(ignore_permissions=True)
		frappe.db.set_value("HD Task", task.name, {
			"assigned_to": "someone.else@example.com",
			"due_date": frappe.utils.add_days(frappe.utils.today(), -1),
		})
		try:
			results = get_my_due_tasks()
			names = [r["name"] for r in results]
			self.assertNotIn(task.name, names)
		finally:
			frappe.delete_doc("HD Task", task.name, ignore_permissions=True, force=True)

	def _make_task(self, title, due_offset_days, status="Todo", assigned_to=None):
		task = frappe.get_doc({
			"doctype": "HD Task",
			"title": title,
			"status": status,
		}).insert(ignore_permissions=True)
		frappe.db.set_value("HD Task", task.name, {
			"assigned_to": assigned_to,
			"due_date": frappe.utils.add_days(frappe.utils.today(), due_offset_days),
		})
		self.addCleanup(
			lambda n=task.name: frappe.delete_doc("HD Task", n, ignore_permissions=True, force=True)
		)
		return task.name

	def test_due_helper_partitions_overdue_and_due_soon(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import _get_due_and_overdue_tasks
		overdue_name = self._make_task("Overdue one", -1)
		today_name = self._make_task("Due today", 0)
		soon_name = self._make_task("Due in 1 day", 1)
		far_name = self._make_task("Due in 5 days", 5)
		done_name = self._make_task("Done overdue", -1, status="Done")

		result = _get_due_and_overdue_tasks(window_hours=48)
		overdue_names = [t["name"] for t in result["overdue"]]
		due_soon_names = [t["name"] for t in result["due_soon"]]

		self.assertIn(overdue_name, overdue_names)
		self.assertIn(today_name, due_soon_names)
		self.assertIn(soon_name, due_soon_names)
		self.assertNotIn(far_name, overdue_names + due_soon_names)
		self.assertNotIn(done_name, overdue_names + due_soon_names)

	def test_due_helper_groups_unassigned(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import _get_due_and_overdue_tasks
		unassigned_name = self._make_task("Nobody owns me", -1, assigned_to=None)
		result = _get_due_and_overdue_tasks(window_hours=48)
		self.assertIn(unassigned_name, [t["name"] for t in result["overdue"]])
		self.assertIn(unassigned_name, [t["name"] for t in result["unassigned"]])

	def test_due_helper_window_upper_boundary(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import _get_due_and_overdue_tasks
		# window_hours=48 -> ceil(48/24)=2 days, so today+2 is the inclusive upper bound
		on_boundary = self._make_task("Due in exactly 2 days", 2)
		beyond = self._make_task("Due in 3 days", 3)
		result = _get_due_and_overdue_tasks(window_hours=48)
		due_soon_names = [t["name"] for t in result["due_soon"]]
		self.assertIn(on_boundary, due_soon_names)
		self.assertNotIn(beyond, due_soon_names)

	def _set_task_settings(self, enabled, recipients):
		settings = frappe.get_single("HD Task Settings")
		settings.enable_manager_digest = 1 if enabled else 0
		settings.due_soon_window_hours = 48
		settings.digest_recipients = []
		for r in recipients:
			settings.append("digest_recipients", r)
		settings.save(ignore_permissions=True)
		self.addCleanup(self._reset_task_settings)

	def _reset_task_settings(self):
		settings = frappe.get_single("HD Task Settings")
		settings.enable_manager_digest = 0
		settings.digest_recipients = []
		settings.save(ignore_permissions=True)

	def test_digest_noop_when_disabled(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import send_manager_task_digest
		from unittest.mock import patch
		self._make_task("Overdue for disabled test", -1)
		self._set_task_settings(enabled=False, recipients=[{"phone": "15550001111"}])
		with patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_wa_text") as wa, \
		     patch("helpdesk.helpdesk.doctype.hd_task.hd_task._notify_digest_recipient") as inapp:
			send_manager_task_digest()
		wa.assert_not_called()
		inapp.assert_not_called()

	def test_digest_noop_when_nothing_due(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import send_manager_task_digest
		from unittest.mock import patch
		self._make_task("Far future", 30)
		self._set_task_settings(enabled=True, recipients=[{"phone": "15550001111"}])
		empty = {"overdue": [], "due_soon": [], "unassigned": []}
		with patch("helpdesk.helpdesk.doctype.hd_task.hd_task._get_due_and_overdue_tasks", return_value=empty), \
		     patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_wa_text") as wa, \
		     patch("helpdesk.helpdesk.doctype.hd_task.hd_task._notify_digest_recipient") as inapp:
			send_manager_task_digest()
		wa.assert_not_called()
		inapp.assert_not_called()

	def test_digest_sends_to_each_recipient(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import send_manager_task_digest
		from unittest.mock import patch
		self._make_task("Overdue for send test", -1)
		self._set_task_settings(
			enabled=True,
			recipients=[{"phone": "15550001111"}, {"phone": "15550002222"}],
		)
		with patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_wa_text") as wa, \
		     patch("helpdesk.helpdesk.doctype.hd_task.hd_task._notify_digest_recipient"):
			send_manager_task_digest()
		self.assertEqual(wa.call_count, 2)

	def test_digest_one_bad_recipient_does_not_stop_others(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import send_manager_task_digest
		from unittest.mock import patch
		self._make_task("Overdue resilience test", -1)
		self._set_task_settings(
			enabled=True,
			recipients=[{"phone": "15550001111"}, {"phone": "15550002222"}],
		)
		with patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_wa_text",
		           side_effect=[Exception("boom"), None]) as wa, \
		     patch("helpdesk.helpdesk.doctype.hd_task.hd_task._notify_digest_recipient"):
			send_manager_task_digest()
		self.assertEqual(wa.call_count, 2)
