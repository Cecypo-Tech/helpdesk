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

	def test_due_helper_excludes_null_due_date(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import _get_due_and_overdue_tasks
		task = frappe.get_doc({
			"doctype": "HD Task",
			"title": "No due date sentinel",
			"status": "Todo",
		}).insert(ignore_permissions=True)
		self.addCleanup(
			lambda n=task.name: frappe.delete_doc("HD Task", n, ignore_permissions=True, force=True)
		)
		result = _get_due_and_overdue_tasks(window_hours=48)
		all_names = [t["name"] for t in result["overdue"]] + [t["name"] for t in result["due_soon"]]
		self.assertNotIn(task.name, all_names)

	def _set_task_settings(self, enabled, recipients, wa_line=None):
		settings = frappe.get_single("HD Task Settings")
		# Snapshot the real singleton once so the test run restores it exactly
		# (send_manager_task_digest commits mid-run, escaping FrappeTestCase's
		# rollback, so without this a test run would clobber the live config).
		if not hasattr(self, "_orig_settings_snapshot"):
			self._orig_settings_snapshot = {
				"enable_manager_digest": settings.enable_manager_digest,
				"due_soon_window_hours": settings.due_soon_window_hours,
				"task_wa_line": settings.task_wa_line,
				"recipients": [{"agent": r.agent, "phone": r.phone} for r in settings.digest_recipients],
			}
			self.addCleanup(self._restore_task_settings)
		settings.enable_manager_digest = 1 if enabled else 0
		settings.due_soon_window_hours = 48
		settings.task_wa_line = None  # clear any stale link before save
		settings.digest_recipients = []
		for r in recipients:
			settings.append("digest_recipients", r)
		settings.save(ignore_permissions=True)
		# Set directly to bypass Link validation in tests (the WA send is mocked).
		frappe.db.set_single_value("HD Task Settings", "task_wa_line", wa_line)
		frappe.clear_document_cache("HD Task Settings")

	def _restore_task_settings(self):
		snap = getattr(self, "_orig_settings_snapshot", None)
		if snap is None:
			return
		settings = frappe.get_single("HD Task Settings")
		settings.enable_manager_digest = snap["enable_manager_digest"]
		settings.due_soon_window_hours = snap["due_soon_window_hours"]
		settings.task_wa_line = snap["task_wa_line"] or None
		settings.digest_recipients = []
		for r in snap["recipients"]:
			settings.append("digest_recipients", r)
		settings.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.clear_document_cache("HD Task Settings")

	def test_digest_noop_when_disabled(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import send_manager_task_digest
		from unittest.mock import patch
		self._make_task("Overdue for disabled test", -1)
		self._set_task_settings(enabled=False, recipients=[{"phone": "15550001111"}], wa_line="WA-TEST-LINE")
		with patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_task_wa") as wa, \
		     patch("helpdesk.helpdesk.doctype.hd_task.hd_task._notify_digest_recipient") as inapp:
			send_manager_task_digest()
		wa.assert_not_called()
		inapp.assert_not_called()

	def test_digest_noop_when_nothing_due(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import send_manager_task_digest
		from unittest.mock import patch
		self._make_task("Far future", 30)
		self._set_task_settings(enabled=True, recipients=[{"phone": "15550001111"}], wa_line="WA-TEST-LINE")
		empty = {"overdue": [], "due_soon": [], "unassigned": []}
		with patch("helpdesk.helpdesk.doctype.hd_task.hd_task._get_due_and_overdue_tasks", return_value=empty), \
		     patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_task_wa") as wa, \
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
			wa_line="WA-TEST-LINE",
		)
		with patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_task_wa") as wa, \
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
			wa_line="WA-TEST-LINE",
		)
		with patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_task_wa",
		           side_effect=[Exception("boom"), None]) as wa, \
		     patch("helpdesk.helpdesk.doctype.hd_task.hd_task._notify_digest_recipient"):
			send_manager_task_digest()
		self.assertEqual(wa.call_count, 2)

	def test_team_task_health_shape(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import get_team_task_health
		self._make_task("Overdue for health", -1)
		self._make_task("Due soon for health", 1)
		self._make_task("Unassigned for health", -1, assigned_to=None)
		result = get_team_task_health()
		self.assertIn("overdue", result)
		self.assertIn("due_soon", result)
		self.assertIn("counts", result)
		self.assertGreaterEqual(result["counts"]["overdue"], 1)
		self.assertGreaterEqual(result["counts"]["due_soon"], 1)
		self.assertGreaterEqual(result["counts"]["unassigned"], 1)

	def test_team_task_health_denies_non_manager(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import get_team_task_health
		# Guest has neither Agent Manager nor System Manager → must be rejected.
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				get_team_task_health()
		finally:
			frappe.set_user("Administrator")

	def test_digest_resolves_agent_phone_when_no_direct_phone(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import send_manager_task_digest
		from unittest import mock
		agent = frappe.db.get_value("HD Agent", {}, "name")
		if not agent:
			self.skipTest("No HD Agent available in this site")
		self._make_task("Overdue for agent-phone test", -1)
		self._set_task_settings(enabled=True, recipients=[{"agent": agent}], wa_line="WA-TEST-LINE")
		with mock.patch("helpdesk.helpdesk.doctype.hd_task.hd_task._get_agent_phone", return_value="15559998888") as gp, \
		     mock.patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_task_wa") as wa, \
		     mock.patch("helpdesk.helpdesk.doctype.hd_task.hd_task._notify_digest_recipient"):
			send_manager_task_digest()
		gp.assert_called_once_with(agent)
		wa.assert_called_once_with("WA-TEST-LINE", "15559998888", mock.ANY)

	def test_digest_skips_whatsapp_without_wa_line(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import send_manager_task_digest
		from unittest import mock
		agent = frappe.db.get_value("HD Agent", {}, "name")
		if not agent:
			self.skipTest("No HD Agent available in this site")
		self._make_task("Overdue no-line test", -1)
		# Enabled, recipient has a phone, but NO digest WA Line configured.
		self._set_task_settings(
			enabled=True,
			recipients=[{"phone": "15550001111", "agent": agent}],
			wa_line=None,
		)
		with mock.patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_task_wa") as wa, \
		     mock.patch("helpdesk.helpdesk.doctype.hd_task.hd_task._notify_digest_recipient") as inapp:
			send_manager_task_digest()
		wa.assert_not_called()
		inapp.assert_called_once()

	def test_due_reminder_skips_without_wa_line(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import send_due_task_wpa_notifications
		from unittest import mock
		self._set_task_settings(enabled=False, recipients=[], wa_line=None)
		with mock.patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_task_wa") as wa:
			send_due_task_wpa_notifications()
		wa.assert_not_called()

	def test_due_reminder_sends_via_wa_line(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import send_due_task_wpa_notifications
		from unittest import mock
		agent = frappe.db.get_value("HD Agent", {}, "name") or frappe.session.user
		self._set_task_settings(enabled=False, recipients=[], wa_line="WA-TEST-LINE")
		self._make_task("Reminder line test", -1, assigned_to=agent)
		# Mock the mutations/send so no real task is altered and nothing is sent.
		with mock.patch("helpdesk.helpdesk.doctype.hd_task.hd_task._get_agent_phone", return_value="15557776666"), \
		     mock.patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_task_wa") as wa, \
		     mock.patch("frappe.db.set_value"), \
		     mock.patch("frappe.db.commit"):
			send_due_task_wpa_notifications()
		self.assertTrue(wa.called)
		# Sent FROM the configured task WA Line.
		self.assertEqual(wa.call_args_list[0].args[0], "WA-TEST-LINE")

	def test_get_tasks_for_customer_returns_correct_tasks(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import get_tasks_for_customer

		# Create a customer
		cust = frappe.get_doc({
			"doctype": "HD Customer",
			"customer_name": "_Test Tasks Customer",
		}).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("HD Customer", cust.name, force=True))

		# Two tasks for this customer
		t1 = frappe.get_doc({"doctype": "HD Task", "title": "Alpha", "customer": cust.name, "status": "Todo"}).insert(ignore_permissions=True)
		t2 = frappe.get_doc({"doctype": "HD Task", "title": "Beta",  "customer": cust.name, "status": "Done"}).insert(ignore_permissions=True)
		# One task for a different customer — must NOT appear
		t3 = frappe.get_doc({"doctype": "HD Task", "title": "Gamma", "status": "Todo"}).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("HD Task", t1.name, force=True))
		self.addCleanup(lambda: frappe.delete_doc("HD Task", t2.name, force=True))
		self.addCleanup(lambda: frappe.delete_doc("HD Task", t3.name, force=True))

		result = get_tasks_for_customer(cust.name)
		names = [t["name"] for t in result]

		self.assertIn(t1.name, names)
		self.assertIn(t2.name, names)
		self.assertNotIn(t3.name, names)
		# Every row must carry the username enrichment key
		for t in result:
			self.assertIn("assigned_to_username", t)

	def test_get_tasks_for_customer_empty_string_returns_empty(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import get_tasks_for_customer
		self.assertEqual(get_tasks_for_customer(""), [])
