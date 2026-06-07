import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

ALLOWED_FIELDS = {
	"title", "status", "priority", "assigned_to",
	"due_date", "due_time", "ticket", "team", "description", "_user_tags",
}


class HDTask(Document):
	def validate(self):
		if self.assigned_to == "@me":
			self.assigned_to = frappe.session.user

	@staticmethod
	def default_list_data():
		columns = [
			{"label": "Title", "type": "Data", "key": "title", "width": "20rem"},
			{"label": "Status", "type": "Select", "key": "status", "width": "9rem"},
			{"label": "Priority", "type": "Link", "key": "priority", "width": "8rem"},
			{"label": "Assigned To", "type": "Link", "key": "assigned_to", "width": "10rem"},
			{"label": "Due Date", "type": "Date", "key": "due_date", "width": "8rem"},
			{"label": "Ticket", "type": "Link", "key": "ticket", "width": "8rem"},
		]
		rows = ["title", "status", "priority", "assigned_to", "due_date", "ticket"]
		return {"columns": columns, "rows": rows}


@frappe.whitelist()
def set_task_field(task_name: str, fieldname: str, value=None):
	"""Set a single field on HD Task using a direct DB write to avoid timestamp conflicts."""
	if fieldname not in ALLOWED_FIELDS:
		frappe.throw(frappe._("Field {0} cannot be updated via this endpoint").format(fieldname))

	frappe.has_permission("HD Task", doc=task_name, ptype="write", throw=True)
	if value == "@me":
		value = frappe.session.user
	frappe.db.set_value("HD Task", task_name, fieldname, value or None)
	frappe.clear_document_cache("HD Task", task_name)
	modified = frappe.db.get_value("HD Task", task_name, "modified")
	return {"modified": str(modified)}


@frappe.whitelist()
def save_task(task_name: str, fields: dict | str, subtasks: list | str = "[]"):
	"""Save all fields and subtasks for an HD Task in one call."""
	frappe.has_permission("HD Task", doc=task_name, ptype="write", throw=True)
	fields = frappe.parse_json(fields)
	subtasks = frappe.parse_json(subtasks)

	allowed_scalar = ALLOWED_FIELDS
	for attempt in range(3):
		try:
			doc = frappe.get_doc("HD Task", task_name)
			for fname, fvalue in fields.items():
				if fname in allowed_scalar:
					if isinstance(fvalue, str) and fvalue == "@me":
						fvalue = frappe.session.user
					doc.set(fname, fvalue or None)
			doc.subtasks = []
			for sub in subtasks:
				doc.append("subtasks", {
					"doctype": "HD Task Subtask",
					"name": sub.get("name") or None,
					"title": sub.get("title", ""),
					"status": sub.get("status", "Backlog"),
					"due_date": sub.get("due_date") or None,
				})
			doc.save()
			return {"modified": str(doc.modified)}
		except frappe.TimestampMismatchError:
			if attempt == 2:
				raise
			frappe.db.rollback()


@frappe.whitelist()
def save_task_subtasks(task_name: str, subtasks: list | str):
	"""Save the subtasks child table for an HD Task."""
	frappe.has_permission("HD Task", doc=task_name, ptype="write", throw=True)
	subtasks = frappe.parse_json(subtasks)

	for attempt in range(3):
		try:
			doc = frappe.get_doc("HD Task", task_name)
			doc.subtasks = []
			for sub in subtasks:
				doc.append("subtasks", {
					"doctype": "HD Task Subtask",
					"name": sub.get("name") or None,
					"title": sub.get("title", ""),
					"status": sub.get("status", "Backlog"),
					"due_date": sub.get("due_date") or None,
				})
			doc.save()
			return {"modified": str(doc.modified)}
		except frappe.TimestampMismatchError:
			if attempt == 2:
				raise
			frappe.db.rollback()


@frappe.whitelist()
def get_all_task_tags() -> list[str]:
	"""Return all distinct tags used on HD Task documents, sorted alphabetically.

	Reads the _user_tags column (comma-separated) directly from tabHD Task
	to avoid relying on tabTag sync.
	"""
	rows = frappe.db.sql(
		"SELECT _user_tags FROM `tabHD Task` WHERE _user_tags IS NOT NULL AND _user_tags != ''",
		as_dict=False,
	)
	tags: set[str] = set()
	for (tag_str,) in rows:
		for tag in tag_str.split(","):
			tag = tag.strip()
			if tag:
				tags.add(tag)
	return sorted(tags)


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

	No-op when the WhatsApp Message DocType is not installed.
	"""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return
	frappe.get_doc({
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": phone,
		"message": message,
		"content_type": "text",
	}).insert(ignore_permissions=True)


def send_due_task_wpa_notifications() -> None:
	"""Hourly scheduler: send a WhatsApp reminder to the assigned agent when a task is due.

	Criteria for sending:
	- status != 'Done'
	- wpa_notified = 0  (haven't sent yet)
	- assigned_to is set
	- due_date is set and the due datetime has arrived:
	    - if due_time is set  → CONCAT(due_date, ' ', due_time) <= NOW()
	    - if due_time is null  → due_date <= CURDATE()

	After sending, wpa_notified is set to 1 to prevent re-sending.
	"""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return

	try:
		settings = frappe.get_cached_doc("WhatsApp Helpdesk Settings")
		if not settings.enabled:
			return
	except Exception:
		return

	due_tasks = frappe.db.sql(
		"""
		SELECT name, title, assigned_to, due_date, due_time, status, ticket
		FROM `tabHD Task`
		WHERE status != 'Done'
		  AND wpa_notified = 0
		  AND assigned_to IS NOT NULL AND assigned_to != ''
		  AND due_date IS NOT NULL
		  AND (
		      (due_time IS NULL AND due_date <= CURDATE())
		      OR
		      (due_time IS NOT NULL AND CONCAT(due_date, ' ', due_time) <= NOW())
		  )
		""",
		as_dict=True,
	)

	for task in due_tasks:
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


def _send_digest_wa(line: str, phone: str, message: str) -> None:
	"""Send the digest to a phone number via the WA Line (Evolution API) path.

	The WA Line path has no 24-hour template restriction, unlike WABA which
	cannot send proactive non-template messages. Caller passes the configured
	digest WA Line to send from.
	"""
	from helpdesk.integrations.wa import _normalize_phone, send_wa_reply

	jid = f"{_normalize_phone(phone)}@s.whatsapp.net"
	send_wa_reply(jid=jid, line=line, message=message)


def send_manager_task_digest() -> None:
	"""Daily scheduler: send a digest of overdue/due-soon tasks to configured managers.

	Delivered over WhatsApp via the configured WA Line (per recipient phone) and
	as an in-app notification (per recipient agent). The WhatsApp half is skipped
	when no digest WA Line is configured; in-app still works. No-op when disabled,
	no recipients, or nothing is due.
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
	wa_line = settings.digest_wa_line

	for recipient in settings.digest_recipients:
		try:
			phone = recipient.phone
			if not phone and recipient.agent:
				phone = _get_agent_phone(recipient.agent)
			if phone and wa_line:
				_send_digest_wa(wa_line, phone, message)
			if recipient.agent:
				_notify_digest_recipient(recipient.agent, len(overdue), len(due_soon))
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				frappe.get_traceback(),
				f"Manager task digest failed for recipient: {recipient.agent or recipient.phone}",
			)


def _get_agent_phone(assigned_to: str) -> str | None:
	"""Resolve a phone number for an HD Agent, trying User.mobile_no then their Contact."""
	user_email = frappe.db.get_value("HD Agent", assigned_to, "user")
	if not user_email:
		return None

	# Fastest path: mobile_no stored directly on the User record
	phone = frappe.db.get_value("User", user_email, "mobile_no")
	if phone:
		return phone

	# Fallback: Contact linked by email_id
	contact = frappe.db.get_value("Contact", {"email_id": user_email}, "name")
	if contact:
		phone = frappe.db.get_value("Contact", contact, "mobile_no") or \
		        frappe.db.get_value("Contact", contact, "phone")
	return phone or None


@frappe.whitelist()
def get_my_due_tasks() -> list[dict]:
	"""Return tasks assigned to the current user where due_date <= today and status != Done.

	Includes both overdue tasks and tasks due today. Does not filter by due_time.
	"""
	return frappe.get_list(
		"HD Task",
		filters=[
			["assigned_to", "=", frappe.session.user],
			["due_date", "<=", frappe.utils.today()],
			["status", "!=", "Done"],
		],
		fields=["name", "title", "due_date", "due_time", "status", "ticket"],
		order_by="due_date asc",
	)


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
		filters=[["due_date", "is", "set"], ["due_date", "<", today], ["status", "!=", "Done"]],
		fields=fields,
		order_by="due_date asc",
	)
	due_soon = frappe.get_all(
		"HD Task",
		filters=[
			["due_date", "is", "set"],
			["due_date", ">=", today],
			["due_date", "<=", window_end],
			["status", "!=", "Done"],
		],
		fields=fields,
		order_by="due_date asc",
	)
	unassigned = [t for t in (overdue + due_soon) if not t.get("assigned_to")]
	return {"overdue": overdue, "due_soon": due_soon, "unassigned": unassigned}


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


@frappe.whitelist()
def search_tasks(query: str) -> list[str]:
	"""Search task names, descriptions, and subtask titles via SQL LIKE.

	Returns a list of matching HD Task names.
	"""
	if not query or not query.strip():
		return []
	q = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
	like = f"%{q}%"
	main = frappe.db.sql(
		"""
		SELECT name FROM `tabHD Task`
		WHERE title LIKE %(like)s ESCAPE '\\\\' OR description LIKE %(like)s ESCAPE '\\\\'
		""",
		{"like": like},
		as_dict=False,
	)
	subs = frappe.db.sql(
		"""
		SELECT DISTINCT parent FROM `tabHD Task Subtask`
		WHERE title LIKE %(like)s ESCAPE '\\\\'
		""",
		{"like": like},
		as_dict=False,
	)
	names = {r[0] for r in main} | {r[0] for r in subs}
	return list(names)
