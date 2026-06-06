# Manager Task Digest & Team Health Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give helpdesk managers a daily digest (WhatsApp + in-app) and an on-demand "Team Health" dashboard surfacing tasks that are overdue or due soon, so work stops slipping through the cracks.

**Architecture:** A new `HD Task Settings` singleton (with a recipients child table) configures the feature. A daily scheduler function in `hd_task.py` queries overdue/due-soon tasks via a shared helper and pushes a digest over WhatsApp (reusing the existing `WhatsApp Message` outbound mechanism) and as in-app `HD Notification`s. The same shared query backs a whitelisted `get_team_task_health()` API that powers a manager-only "Team Health" view added to the existing Tasks page.

**Tech Stack:** Frappe (Python DocTypes, scheduler, whitelisted APIs, FrappeTestCase), Vue 3 Composition API + frappe-ui (createResource), the helpdesk WhatsApp + notification infrastructure.

---

## Spec

Design: `docs/superpowers/specs/2026-06-06-manager-task-digest-design.md`

## Conventions for this codebase

- **Bench root:** `/home/kushal/frappe-bench`. **Site:** `dev.localhost`.
- **Run a single test module:**
  `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module "helpdesk.helpdesk.doctype.hd_task.test_hd_task"`
- **Apply new/changed DocTypes:**
  `cd /home/kushal/frappe-bench && bench --site dev.localhost migrate`
- **Build frontend:** `cd /home/kushal/frappe-bench/apps/helpdesk && bench build --app helpdesk` then hard-refresh.
- Python files in this app use **tabs** for indentation (see `hd_task.py`). Match that.
- DocType JSON lives at `helpdesk/helpdesk/doctype/<snake_name>/<snake_name>.json` with a sibling `.py` controller and `__init__.py`.

## File Structure

**Create:**
- `helpdesk/helpdesk/doctype/hd_task_digest_recipient/` — child table DocType (JSON, `__init__.py`, controller)
- `helpdesk/helpdesk/doctype/hd_task_settings/` — singleton DocType (JSON, `__init__.py`, controller)
- `desk/src/pages/tasks/TeamHealthView.vue` — manager dashboard component

**Modify:**
- `helpdesk/helpdesk/doctype/hd_task/hd_task.py` — shared query helper, `_send_wa_text` + `_format_due_label` helpers, `send_manager_task_digest`, `get_team_task_health`
- `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py` — new tests
- `helpdesk/hooks.py` — register digest in `scheduler_events["daily"]`
- `helpdesk/helpdesk/doctype/hd_notification/hd_notification.json` — add `Task` to `notification_type` options
- `helpdesk/helpdesk/doctype/hd_notification/utils.py` — let `clear` accept a single notification `name`
- `desk/src/stores/notification.ts` — `readByName(name)`
- `desk/src/components/notifications/Notifications.vue` — render + route `Task` notifications
- `desk/src/pages/tasks/Tasks.vue` — manager-only Team Health tab + view switch

---

## Task 1: `HD Task Digest Recipient` child DocType

**Files:**
- Create: `helpdesk/helpdesk/doctype/hd_task_digest_recipient/hd_task_digest_recipient.json`
- Create: `helpdesk/helpdesk/doctype/hd_task_digest_recipient/__init__.py`
- Create: `helpdesk/helpdesk/doctype/hd_task_digest_recipient/hd_task_digest_recipient.py`

- [ ] **Step 1: Create the child table JSON**

Create `helpdesk/helpdesk/doctype/hd_task_digest_recipient/hd_task_digest_recipient.json`:

```json
{
 "actions": [],
 "autoname": "autoincrement",
 "creation": "2026-06-06 00:00:00",
 "doctype": "DocType",
 "editable_grid": 1,
 "engine": "InnoDB",
 "field_order": [
  "agent",
  "phone"
 ],
 "fields": [
  {
   "fieldname": "agent",
   "fieldtype": "Link",
   "in_list_view": 1,
   "label": "Agent",
   "options": "HD Agent"
  },
  {
   "fieldname": "phone",
   "fieldtype": "Data",
   "in_list_view": 1,
   "label": "Phone (override)",
   "description": "Optional WhatsApp number. Used when no agent is set, or to override the agent's number."
  }
 ],
 "index_web_pages_for_search": 1,
 "istable": 1,
 "links": [],
 "modified": "2026-06-06 00:00:00",
 "modified_by": "Administrator",
 "module": "Helpdesk",
 "name": "HD Task Digest Recipient",
 "naming_rule": "Autoincrement",
 "owner": "Administrator",
 "permissions": [],
 "row_format": "Dynamic",
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

- [ ] **Step 2: Create the package init**

Create `helpdesk/helpdesk/doctype/hd_task_digest_recipient/__init__.py` as an empty file:

```python
```

- [ ] **Step 3: Create the controller with row validation**

Create `helpdesk/helpdesk/doctype/hd_task_digest_recipient/hd_task_digest_recipient.py`:

```python
import frappe
from frappe.model.document import Document


class HDTaskDigestRecipient(Document):
	def validate(self):
		if not self.agent and not self.phone:
			frappe.throw(frappe._("Each digest recipient must have an Agent or a Phone number."))
```

- [ ] **Step 4: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add helpdesk/helpdesk/doctype/hd_task_digest_recipient
git commit -m "feat(tasks): add HD Task Digest Recipient child doctype"
```

---

## Task 2: `HD Task Settings` singleton DocType

**Files:**
- Create: `helpdesk/helpdesk/doctype/hd_task_settings/hd_task_settings.json`
- Create: `helpdesk/helpdesk/doctype/hd_task_settings/__init__.py`
- Create: `helpdesk/helpdesk/doctype/hd_task_settings/hd_task_settings.py`
- Modify: apply via `bench migrate`

- [ ] **Step 1: Create the singleton JSON**

Create `helpdesk/helpdesk/doctype/hd_task_settings/hd_task_settings.json`:

```json
{
 "actions": [],
 "creation": "2026-06-06 00:00:00",
 "doctype": "DocType",
 "document_type": "Document",
 "editable_grid": 1,
 "engine": "InnoDB",
 "field_order": [
  "enable_manager_digest",
  "due_soon_window_hours",
  "section_recipients",
  "digest_recipients"
 ],
 "fields": [
  {
   "default": "0",
   "fieldname": "enable_manager_digest",
   "fieldtype": "Check",
   "label": "Enable Manager Task Digest"
  },
  {
   "default": "48",
   "description": "How far ahead (in hours) a task counts as \"due soon\".",
   "fieldname": "due_soon_window_hours",
   "fieldtype": "Int",
   "label": "Due Soon Window (Hours)"
  },
  {
   "fieldname": "section_recipients",
   "fieldtype": "Section Break",
   "label": "Digest Recipients"
  },
  {
   "fieldname": "digest_recipients",
   "fieldtype": "Table",
   "label": "Digest Recipients",
   "options": "HD Task Digest Recipient"
  }
 ],
 "index_web_pages_for_search": 1,
 "issingle": 1,
 "links": [],
 "modified": "2026-06-06 00:00:00",
 "modified_by": "Administrator",
 "module": "Helpdesk",
 "name": "HD Task Settings",
 "owner": "Administrator",
 "permissions": [
  {
   "create": 1,
   "delete": 1,
   "email": 1,
   "export": 1,
   "print": 1,
   "read": 1,
   "role": "System Manager",
   "share": 1,
   "write": 1
  },
  {
   "create": 1,
   "delete": 1,
   "email": 1,
   "export": 1,
   "print": 1,
   "read": 1,
   "role": "Agent Manager",
   "share": 1,
   "write": 1
  }
 ],
 "sort_field": "creation",
 "sort_order": "DESC",
 "states": [],
 "track_changes": 0
}
```

- [ ] **Step 2: Create the package init**

Create `helpdesk/helpdesk/doctype/hd_task_settings/__init__.py` as an empty file:

```python
```

- [ ] **Step 3: Create the controller**

Create `helpdesk/helpdesk/doctype/hd_task_settings/hd_task_settings.py`:

```python
import frappe
from frappe.model.document import Document


class HDTaskSettings(Document):
	pass
```

- [ ] **Step 4: Apply the DocTypes to the site**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost migrate`
Expected: completes without error; `HD Task Settings` and `HD Task Digest Recipient` are created.

- [ ] **Step 5: Verify the singleton loads**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost execute frappe.client.get_value --kwargs "{'doctype':'HD Task Settings','fieldname':'enable_manager_digest'}"`
Expected: returns a dict with `enable_manager_digest` (0).

- [ ] **Step 6: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add helpdesk/helpdesk/doctype/hd_task_settings
git commit -m "feat(tasks): add HD Task Settings singleton"
```

---

## Task 3: Shared query helper for overdue / due-soon tasks

**Files:**
- Modify: `helpdesk/helpdesk/doctype/hd_task/hd_task.py`
- Test: `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py`

This helper is the single source of truth for "what's slipping", used by both the digest and the dashboard.

- [ ] **Step 1: Write the failing tests**

Add to `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py` inside `class TestHDTask`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module "helpdesk.helpdesk.doctype.hd_task.test_hd_task"`
Expected: FAIL with `ImportError`/`cannot import name '_get_due_and_overdue_tasks'`.

- [ ] **Step 3: Implement the helper**

Add to `helpdesk/helpdesk/doctype/hd_task/hd_task.py` (after `get_my_due_tasks`):

```python
def _get_due_and_overdue_tasks(window_hours: int = 48) -> dict:
	"""Return tasks that are overdue or due soon, for the manager digest and dashboard.

	overdue   = due_date < today and status != Done
	due_soon  = today <= due_date <= today + ceil(window_hours/24) days, status != Done
	unassigned = subset of (overdue + due_soon) with no assigned_to

	All lists are sorted by due_date ascending.
	"""
	import math

	window_days = max(1, math.ceil((window_hours or 0) / 24))
	today = frappe.utils.today()
	window_end = frappe.utils.add_days(today, window_days)
	fields = ["name", "title", "assigned_to", "due_date", "due_time", "priority", "ticket"]

	overdue = frappe.get_all(
		"HD Task",
		filters=[["due_date", "<", today], ["status", "!=", "Done"]],
		fields=fields,
		order_by="due_date asc",
	)
	due_soon = frappe.get_all(
		"HD Task",
		filters=[
			["due_date", ">=", today],
			["due_date", "<=", window_end],
			["status", "!=", "Done"],
		],
		fields=fields,
		order_by="due_date asc",
	)
	unassigned = [t for t in (overdue + due_soon) if not t.get("assigned_to")]
	return {"overdue": overdue, "due_soon": due_soon, "unassigned": unassigned}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module "helpdesk.helpdesk.doctype.hd_task.test_hd_task"`
Expected: PASS (all tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add helpdesk/helpdesk/doctype/hd_task/hd_task.py helpdesk/helpdesk/doctype/hd_task/test_hd_task.py
git commit -m "feat(tasks): add shared overdue/due-soon query helper"
```

---

## Task 4: Extract `_format_due_label` and `_send_wa_text` helpers (DRY refactor)

**Files:**
- Modify: `helpdesk/helpdesk/doctype/hd_task/hd_task.py`

The existing `send_due_task_wpa_notifications` inlines due-date formatting and WhatsApp-message creation. Extract both so the digest reuses identical behavior.

- [ ] **Step 1: Add the two helpers**

Add to `helpdesk/helpdesk/doctype/hd_task/hd_task.py` (just above `send_due_task_wpa_notifications`):

```python
def _format_due_label(due_date, due_time) -> str:
	"""Render a task due date (optionally with HH:MM time) for messages."""
	label = str(due_date)
	if due_time:
		# due_time is a timedelta from MySQL — format as HH:MM
		total_seconds = int(due_time.total_seconds())
		h, m = divmod(total_seconds // 60, 60)
		label += f" at {h:02d}:{m:02d}"
	return label


def _send_wa_text(phone: str, message: str) -> None:
	"""Send a plain-text WhatsApp message to a phone number via frappe_whatsapp.

	Mirrors the outbound mechanism used for per-task reminders. Caller is
	responsible for ensuring the WhatsApp Message doctype exists.
	"""
	frappe.get_doc({
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": phone,
		"message": message,
		"content_type": "text",
	}).insert(ignore_permissions=True)
```

- [ ] **Step 2: Rewrite the loop body in `send_due_task_wpa_notifications` to use them**

In `helpdesk/helpdesk/doctype/hd_task/hd_task.py`, replace the body of the `for task in due_tasks:` loop (the lines from `due_label = str(task.due_date)` through the `frappe.get_doc({...}).insert(...)` call) with:

```python
		try:
			phone = _get_agent_phone(task.assigned_to)
			if not phone:
				continue

			due_label = _format_due_label(task.due_date, task.due_time)
			lines = [f"⏰ Task due: {task.title}", f"Due: {due_label}", f"Status: {task.status}"]
			if task.ticket:
				lines.append(f"Ticket: {task.ticket}")

			_send_wa_text(phone, "\n".join(lines))

			frappe.db.set_value("HD Task", task.name, "wpa_notified", 1, update_modified=False)
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"WPA task notification failed: {task.name}")
```

- [ ] **Step 3: Run the existing tests to verify no regression**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module "helpdesk.helpdesk.doctype.hd_task.test_hd_task"`
Expected: PASS (refactor changes no behavior).

- [ ] **Step 4: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add helpdesk/helpdesk/doctype/hd_task/hd_task.py
git commit -m "refactor(tasks): extract due-label and WhatsApp-text send helpers"
```

---

## Task 5: `send_manager_task_digest` daily scheduler function

**Files:**
- Modify: `helpdesk/helpdesk/doctype/hd_task/hd_task.py`
- Modify: `helpdesk/hooks.py`
- Test: `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py`

- [ ] **Step 1: Write the failing tests**

Add to `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py` inside `class TestHDTask`:

```python
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
		with patch("helpdesk.helpdesk.doctype.hd_task.hd_task._send_wa_text") as wa, \
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module "helpdesk.helpdesk.doctype.hd_task.test_hd_task"`
Expected: FAIL with `cannot import name 'send_manager_task_digest'`.

- [ ] **Step 3: Implement the digest function and in-app helper**

Add to `helpdesk/helpdesk/doctype/hd_task/hd_task.py` (after `_send_wa_text`):

```python
def _build_digest_message(overdue: list, due_soon: list, max_lines: int = 5) -> str:
	"""Build the plain-text WhatsApp digest body."""
	lines = [f"📋 Task digest: {len(overdue)} overdue, {len(due_soon)} due soon"]

	def fmt(task):
		who = task.get("assigned_to") or "Unassigned"
		return f"• {task['title']} — {who} — {_format_due_label(task.get('due_date'), task.get('due_time'))}"

	preview = (overdue + due_soon)[:max_lines]
	lines.extend(fmt(t) for t in preview)
	remaining = (len(overdue) + len(due_soon)) - len(preview)
	if remaining > 0:
		lines.append(f"+{remaining} more")
	return "\n".join(lines)


def _notify_digest_recipient(agent: str, overdue_count: int, due_soon_count: int) -> None:
	"""Create an in-app HD Notification for a digest recipient agent."""
	user_to = frappe.db.get_value("HD Agent", agent, "user") or agent
	frappe.get_doc({
		"doctype": "HD Notification",
		"user_from": "Administrator",
		"user_to": user_to,
		"notification_type": "Task",
		"message": f"{overdue_count} overdue, {due_soon_count} due soon — review Team Health.",
	}).insert(ignore_permissions=True)


def send_manager_task_digest() -> None:
	"""Daily scheduler: send a digest of overdue/due-soon tasks to configured managers.

	Delivered over WhatsApp (per recipient phone) and as an in-app notification
	(per recipient agent). No-op when disabled, no recipients, or nothing is due.
	"""
	try:
		settings = frappe.get_cached_doc("HD Task Settings")
	except Exception:
		return
	if not settings.enable_manager_digest or not settings.digest_recipients:
		return

	data = _get_due_and_overdue_tasks(settings.due_soon_window_hours or 48)
	overdue, due_soon = data["overdue"], data["due_soon"]
	if not overdue and not due_soon:
		return

	message = _build_digest_message(overdue, due_soon)
	wa_available = frappe.db.exists("DocType", "WhatsApp Message")

	for recipient in settings.digest_recipients:
		try:
			phone = recipient.phone
			if not phone and recipient.agent:
				phone = _get_agent_phone(recipient.agent)
			if phone and wa_available:
				_send_wa_text(phone, message)
			if recipient.agent:
				_notify_digest_recipient(recipient.agent, len(overdue), len(due_soon))
			frappe.db.commit()
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Manager task digest failed for recipient: {recipient.agent or recipient.phone}",
			)
```

- [ ] **Step 4: Register the daily scheduler hook**

In `helpdesk/hooks.py`, change the `"daily"` list inside `scheduler_events` to add the digest entry. Replace:

```python
    "daily": [
        "helpdesk.helpdesk.doctype.hd_ticket.hd_ticket.close_tickets_after_n_days",
        "helpdesk.integrations.wa.enqueue_wa_sync",
        "helpdesk.integrations.embeddings.embed_resolved_tickets",
        "helpdesk.integrations.kb_autofill.promote_gaps_to_draft_articles",
    ],
```

with:

```python
    "daily": [
        "helpdesk.helpdesk.doctype.hd_ticket.hd_ticket.close_tickets_after_n_days",
        "helpdesk.integrations.wa.enqueue_wa_sync",
        "helpdesk.integrations.embeddings.embed_resolved_tickets",
        "helpdesk.integrations.kb_autofill.promote_gaps_to_draft_articles",
        "helpdesk.helpdesk.doctype.hd_task.hd_task.send_manager_task_digest",
    ],
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module "helpdesk.helpdesk.doctype.hd_task.test_hd_task"`
Expected: PASS (all tests). Note: the `test_digest_*` tests depend on the `Task` notification type being a valid option — Task 7 adds it. Until Task 7 + migrate, `_notify_digest_recipient` is patched out in these tests, so they pass regardless.

- [ ] **Step 6: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add helpdesk/helpdesk/doctype/hd_task/hd_task.py helpdesk/helpdesk/doctype/hd_task/test_hd_task.py helpdesk/hooks.py
git commit -m "feat(tasks): add daily manager task digest"
```

---

## Task 6: `get_team_task_health` dashboard API

**Files:**
- Modify: `helpdesk/helpdesk/doctype/hd_task/hd_task.py`
- Test: `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py`

- [ ] **Step 1: Write the failing tests**

Add to `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py` inside `class TestHDTask`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module "helpdesk.helpdesk.doctype.hd_task.test_hd_task"`
Expected: FAIL with `cannot import name 'get_team_task_health'`.

- [ ] **Step 3: Implement the API**

Add to `helpdesk/helpdesk/doctype/hd_task/hd_task.py` (after `send_manager_task_digest`):

```python
@frappe.whitelist()
def get_team_task_health() -> dict:
	"""Manager dashboard data: overdue + due-soon tasks, team-wide, with counts.

	Restricted to Agent Manager / System Manager.
	"""
	roles = set(frappe.get_roles(frappe.session.user))
	if not ({"Agent Manager", "System Manager"} & roles):
		frappe.throw(frappe._("Not permitted"), frappe.PermissionError)

	try:
		window_hours = frappe.db.get_single_value("HD Task Settings", "due_soon_window_hours") or 48
	except Exception:
		window_hours = 48

	data = _get_due_and_overdue_tasks(window_hours)
	return {
		"overdue": data["overdue"],
		"due_soon": data["due_soon"],
		"counts": {
			"overdue": len(data["overdue"]),
			"due_soon": len(data["due_soon"]),
			"unassigned": len(data["unassigned"]),
		},
	}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module "helpdesk.helpdesk.doctype.hd_task.test_hd_task"`
Expected: PASS. (Test runs as Administrator, who has System Manager.)

- [ ] **Step 5: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add helpdesk/helpdesk/doctype/hd_task/hd_task.py helpdesk/helpdesk/doctype/hd_task/test_hd_task.py
git commit -m "feat(tasks): add get_team_task_health dashboard API"
```

---

## Task 7: Add `Task` notification type + single-notification clear

**Files:**
- Modify: `helpdesk/helpdesk/doctype/hd_notification/hd_notification.json`
- Modify: `helpdesk/helpdesk/doctype/hd_notification/utils.py`

- [ ] **Step 1: Add `Task` to the notification_type options**

In `helpdesk/helpdesk/doctype/hd_notification/hd_notification.json`, find the `notification_type` field. Its `options` currently is:

```
"options": "Assignment\nMention\nReaction\nWhatsApp"
```

Change it to:

```
"options": "Assignment\nMention\nReaction\nWhatsApp\nTask"
```

- [ ] **Step 2: Extend `clear` to accept a single notification name**

In `helpdesk/helpdesk/doctype/hd_notification/utils.py`, replace the whole `clear` function with:

```python
@frappe.whitelist()
def clear(ticket: str | int | None = None, comment: str | None = None, notification: str | None = None):
    """
    Mark notifications as read. No arguments will clear all notifications for `user`.

    :param ticket: Ticket to clear notifications for
    :param comment: Comment to clear notifications for
    :param notification: A single HD Notification name to clear (used for non-ticket notifications)
    """
    filters = {"user_to": frappe.session.user, "read": False}
    if ticket:
        filters["reference_ticket"] = ticket
    if comment:
        filters["reference_comment"] = comment
    if notification:
        filters["name"] = notification
    for notification_name in frappe.get_all(
        "HD Notification", filters=filters, pluck="name"
    ):
        frappe.db.set_value(
            "HD Notification", notification_name, "read", 1, update_modified=False
        )
```

- [ ] **Step 3: Apply the doctype change**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost migrate`
Expected: completes without error.

- [ ] **Step 4: Verify a Task notification can be created**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost execute frappe.client.insert --kwargs "{'doc':{'doctype':'HD Notification','user_from':'Administrator','user_to':'Administrator','notification_type':'Task','message':'plan smoke test'}}"`
Expected: returns the created doc dict (no validation error on `notification_type`). Then clean it up:
Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost execute frappe.db.delete --kwargs "{'doctype':'HD Notification','filters':{'message':'plan smoke test'}}"` and commit the DB with `bench --site dev.localhost execute frappe.db.commit`.

- [ ] **Step 5: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add helpdesk/helpdesk/doctype/hd_notification/hd_notification.json helpdesk/helpdesk/doctype/hd_notification/utils.py
git commit -m "feat(notifications): add Task type and single-notification clear"
```

---

## Task 8: `TeamHealthView.vue` dashboard component

**Files:**
- Create: `desk/src/pages/tasks/TeamHealthView.vue`

No JS unit-test harness exists in this app, so frontend tasks verify via build + manual browser check.

- [ ] **Step 1: Create the component**

Create `desk/src/pages/tasks/TeamHealthView.vue`:

```vue
<template>
  <div class="flex flex-col h-full overflow-hidden">
    <!-- Counts strip -->
    <div class="flex items-center gap-2 px-4 py-2 border-b border-outline-gray-1 bg-surface-white">
      <button
        v-for="f in filters"
        :key="f.key"
        class="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded border transition-colors"
        :class="activeFilter === f.key
          ? f.activeClass
          : 'bg-surface-white border-outline-gray-2 text-ink-gray-6 hover:border-outline-gray-4'"
        @click="activeFilter = activeFilter === f.key ? null : f.key"
      >
        {{ f.label }}
        <span class="font-semibold">{{ f.count }}</span>
      </button>
    </div>

    <!-- Grouped task list -->
    <div class="flex-1 overflow-auto p-4 space-y-5">
      <div v-if="resource.loading" class="text-sm text-ink-gray-5">{{ __('Loading…') }}</div>
      <div
        v-else-if="visibleGroups.length === 0"
        class="text-sm text-ink-gray-5 flex flex-col items-center gap-2 mt-20"
      >
        <LucideCheckCircle2 class="size-6 text-green-500" />
        {{ __('Nothing overdue or due soon. Nice.') }}
      </div>
      <div v-for="group in visibleGroups" :key="group.assignee" class="space-y-1.5">
        <div class="text-sm font-medium text-ink-gray-8">
          {{ group.assignee || __('Unassigned') }}
          <span class="text-ink-gray-5">({{ group.tasks.length }})</span>
        </div>
        <RouterLink
          v-for="task in group.tasks"
          :key="task.name"
          :to="{ name: 'TaskAgent', params: { taskId: task.name } }"
          class="flex items-center justify-between gap-3 rounded border border-outline-gray-1 bg-surface-white px-3 py-2 hover:bg-surface-gray-2"
        >
          <span class="truncate text-base text-ink-gray-8">{{ task.title }}</span>
          <span
            class="shrink-0 text-xs"
            :class="task._overdue ? 'text-red-600 font-medium' : 'text-amber-600'"
          >
            {{ task.due_date }}
          </span>
        </RouterLink>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { createResource } from "frappe-ui";
import { computed, ref } from "vue";
import LucideCheckCircle2 from "~icons/lucide/check-circle-2";

const activeFilter = ref<"overdue" | "due_soon" | null>(null);

const resource = createResource({
  url: "helpdesk.helpdesk.doctype.hd_task.hd_task.get_team_task_health",
  auto: true,
});

const counts = computed(() => resource.data?.counts ?? { overdue: 0, due_soon: 0, unassigned: 0 });

const filters = computed(() => [
  {
    key: "overdue",
    label: __("Overdue"),
    count: counts.value.overdue,
    activeClass: "bg-red-50 border-red-300 text-red-600 font-semibold",
  },
  {
    key: "due_soon",
    label: __("Due Soon"),
    count: counts.value.due_soon,
    activeClass: "bg-amber-50 border-amber-300 text-amber-600 font-semibold",
  },
]);

const allTasks = computed(() => {
  const overdue = (resource.data?.overdue ?? []).map((t: any) => ({ ...t, _overdue: true }));
  const dueSoon = (resource.data?.due_soon ?? []).map((t: any) => ({ ...t, _overdue: false }));
  if (activeFilter.value === "overdue") return overdue;
  if (activeFilter.value === "due_soon") return dueSoon;
  return [...overdue, ...dueSoon];
});

const visibleGroups = computed(() => {
  const byAssignee = new Map<string, any[]>();
  for (const task of allTasks.value) {
    const key = task.assigned_to || "";
    if (!byAssignee.has(key)) byAssignee.set(key, []);
    byAssignee.get(key)!.push(task);
  }
  // Named assignees first (alphabetical), unassigned ("") bucket last.
  return [...byAssignee.entries()]
    .sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b)))
    .map(([assignee, tasks]) => ({ assignee, tasks }));
});
</script>
```

- [ ] **Step 2: Build to verify it compiles**

Run: `cd /home/kushal/frappe-bench/apps/helpdesk && bench build --app helpdesk`
Expected: build succeeds with no errors referencing `TeamHealthView.vue`.

- [ ] **Step 3: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add desk/src/pages/tasks/TeamHealthView.vue
git commit -m "feat(tasks): add Team Health manager dashboard component"
```

---

## Task 9: Wire Team Health into the Tasks page (manager-only)

**Files:**
- Modify: `desk/src/pages/tasks/Tasks.vue`

- [ ] **Step 1: Render the view when `?view=team-health`**

In `desk/src/pages/tasks/Tasks.vue`, find the template block:

```vue
    <CalendarView
      v-if="isCalendarView"
    />
    <KanbanView
      v-else-if="isKanbanView"
    />
    <template v-else>
```

Replace it with:

```vue
    <TeamHealthView
      v-if="isTeamHealthView"
    />
    <CalendarView
      v-else-if="isCalendarView"
    />
    <KanbanView
      v-else-if="isKanbanView"
    />
    <template v-else>
```

- [ ] **Step 2: Add the manager-only toggle button**

In the same file, find the quick-filter bar's right-side button group (the `<div class="ml-auto flex items-center gap-1.5">` containing the Calendar and Kanban buttons). Immediately after the opening `<div class="ml-auto flex items-center gap-1.5">` tag, insert:

```vue
          <button
            v-if="isManager"
            class="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded border transition-colors bg-surface-white border-outline-gray-2 text-ink-gray-6 hover:border-outline-gray-4"
            @click="() => { currentView = { label: __('Team Health'), icon: LucideHeartPulse }; router.push({ name: 'TasksAgent', query: { view: 'team-health' } }); }"
          >
            <LucideHeartPulse class="h-3 w-3" />
            {{ __('Team Health') }}
          </button>
```

- [ ] **Step 3: Import the component, icon, and add the computed**

In the `<script setup>` of `Tasks.vue`, add the component import next to the existing view imports (after the `import KanbanView ...` line):

```javascript
import TeamHealthView from "@/pages/tasks/TeamHealthView.vue";
```

Add the icon import next to the other `~icons/lucide/*` imports:

```javascript
import LucideHeartPulse from "~icons/lucide/heart-pulse";
```

Then add the computed next to `isCalendarView` (the file already destructures `isManager` from `useAuthStore()` and has `route`):

```javascript
const isTeamHealthView = computed(() => route.query.view === "team-health");
```

- [ ] **Step 4: Build and verify**

Run: `cd /home/kushal/frappe-bench/apps/helpdesk && bench build --app helpdesk`
Expected: build succeeds.

- [ ] **Step 5: Manual check**

Start the site if needed (`cd /home/kushal/frappe-bench && bench serve --port 8002`), log in as a manager, open `http://dev.localhost:8002/helpdesk/tasks`, click **Team Health**. Expected: URL becomes `?view=team-health`, counts strip renders, tasks grouped by assignee. Log in as a non-manager: the **Team Health** button is absent.

- [ ] **Step 6: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add desk/src/pages/tasks/Tasks.vue
git commit -m "feat(tasks): add manager-only Team Health tab to Tasks page"
```

---

## Task 10: Render & route `Task` notifications in the bell

**Files:**
- Modify: `desk/src/stores/notification.ts`
- Modify: `desk/src/components/notifications/Notifications.vue`

- [ ] **Step 1: Add `readByName` to the store**

In `desk/src/stores/notification.ts`, just after the existing `read` arrow function (the one that takes `ticket`), add:

```javascript
  const readByName = (name: string) => {
    createResource({
      url: "helpdesk.helpdesk.doctype.hd_notification.utils.clear",
      auto: true,
      params: {
        notification: name,
      },
      onSuccess: () => resource.reload(),
    });
  };
```

Then add `readByName` to the object returned by the store. Find the `return { ... }` near the bottom that includes `read,` and add `readByName,` next to it:

```javascript
    read,
    readByName,
```

- [ ] **Step 2: Handle the `Task` type in `Notifications.vue` rendering**

In `desk/src/components/notifications/Notifications.vue`, inside the `<span class="space-x-1 text-ink-gray-7">` block, after the existing `WhatsApp` line:

```vue
              <span v-if="n.notification_type === 'WhatsApp'" class="text-sm text-ink-gray-6">
                {{ n.message }}
              </span>
```

add:

```vue
              <span v-if="n.notification_type === 'Task'" class="text-sm text-ink-gray-6">
                {{ n.message }}
              </span>
```

Also adjust the author-name guard so Task notifications don't print a stray author. Replace:

```vue
              <span
                class="font-medium text-ink-gray-9"
                v-if="n.notification_type !== 'Reaction' || !n.message"
              >
                {{ n.notification_type === 'WhatsApp' ? '' : n.user_from }}
              </span>
```

with:

```vue
              <span
                class="font-medium text-ink-gray-9"
                v-if="(n.notification_type !== 'Reaction' || !n.message) && n.notification_type !== 'Task'"
              >
                {{ n.notification_type === 'WhatsApp' ? '' : n.user_from }}
              </span>
```

- [ ] **Step 3: Route `Task` notifications to Team Health**

In the `getRoute` function's `switch`, after the `case "WhatsApp":` block, add:

```javascript
    case "Task":
      return {
        name: "TasksAgent",
        query: { view: "team-health" },
      };
```

- [ ] **Step 4: Mark `Task` notifications read by name on click**

Replace the `handleNotificationClick` function:

```javascript
function handleNotificationClick(n: Notification) {
  notificationStore.toggle();
  if (n.read) return;
  notificationStore.read(n.reference_ticket);
}
```

with:

```javascript
function handleNotificationClick(n: Notification) {
  notificationStore.toggle();
  if (n.read) return;
  if (n.notification_type === "Task") {
    notificationStore.readByName(n.name);
  } else {
    notificationStore.read(n.reference_ticket);
  }
}
```

- [ ] **Step 5: Build and verify**

Run: `cd /home/kushal/frappe-bench/apps/helpdesk && bench build --app helpdesk`
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add desk/src/stores/notification.ts desk/src/components/notifications/Notifications.vue
git commit -m "feat(notifications): render and route Task digest notifications"
```

---

## Task 11: End-to-end manual smoke & final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full task test module**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module "helpdesk.helpdesk.doctype.hd_task.test_hd_task"`
Expected: all tests PASS.

- [ ] **Step 2: Configure the digest**

In the site, open **HD Task Settings** (Frappe desk: `/app/hd-task-settings`), tick **Enable Manager Task Digest**, add a recipient row with your own agent and/or a test phone number, save.

- [ ] **Step 3: Seed a slipping task**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost execute frappe.client.insert --kwargs "{'doc':{'doctype':'HD Task','title':'Digest smoke task','status':'Todo','due_date':'2026-06-01'}}"`
Expected: returns the created task (due in the past → overdue).

- [ ] **Step 4: Run the digest manually**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost execute helpdesk.helpdesk.doctype.hd_task.hd_task.send_manager_task_digest`
Expected: no error. If a recipient agent was set, a `Task` HD Notification exists for that user; if a valid WhatsApp number was set and `WhatsApp Message` is installed, an outgoing message was queued.

- [ ] **Step 5: Verify the dashboard + bell**

Open `http://dev.localhost:8002/helpdesk/tasks` as a manager → **Team Health** shows the seeded overdue task. Open the notification bell → the digest notification appears and clicking it navigates to `?view=team-health` and marks itself read.

- [ ] **Step 6: Clean up the smoke task**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost execute frappe.db.delete --kwargs "{'doctype':'HD Task','filters':{'title':'Digest smoke task'}}"` then `cd /home/kushal/frappe-bench && bench --site dev.localhost execute frappe.db.commit`

- [ ] **Step 7: Final commit (if any verification fixups were needed)**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add -A
git commit -m "chore(tasks): manager digest verification fixups" || echo "nothing to commit"
```

---

## Follow-ups (out of scope — see spec Future Work)

- Playwright e2e asserting the Team Health tab lists overdue tasks (infra now installed under `desk/e2e/`).
- `HD Task Digest Log` doctype for send auditing/retry.
- Real-time per-task alerts; per-team-lead scoping; "stalled"/"unassigned" digest triggers.
