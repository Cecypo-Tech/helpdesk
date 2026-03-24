import frappe
from frappe.model.document import Document

ALLOWED_FIELDS = {
	"title", "status", "priority", "assigned_to",
	"due_date", "ticket", "team", "description",
}


class HDTask(Document):
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
	"""Set a single field on HD Task using a direct DB write to avoid timestamp conflicts.

	Using frappe.db.set_value skips check_if_latest entirely, which is safe for
	simple field updates where the last-write-wins semantic is acceptable.
	"""
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
	"""Save all fields and subtasks for an HD Task in one call.

	Loads fresh from DB so the client never needs to track modified.
	Retries up to 3x on TimestampMismatchError to handle concurrent saves.
	"""
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
	"""Save the subtasks child table for an HD Task.

	Always loads fresh from DB to get the current modified timestamp, and
	retries up to 3 times on TimestampMismatchError (race condition between
	concurrent saves resolves within one retry).
	"""
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
			# Brief yield then retry with a fresh get_doc
			frappe.db.rollback()
