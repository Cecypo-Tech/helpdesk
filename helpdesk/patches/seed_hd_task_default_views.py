import frappe


def execute():
	"""Seed a public default List view and a public Kanban view for HD Task."""
	if not frappe.db.exists("HD View", {"dt": "HD Task", "label": "All Tasks", "public": 1}):
		frappe.get_doc(
			{
				"doctype": "HD View",
				"label": "All Tasks",
				"type": "list",
				"dt": "HD Task",
				"route_name": "TasksAgent",
				"public": 1,
				"is_default": 0,
				"filters": "{}",
				"order_by": "modified desc",
				"columns": "[]",
				"rows": "[]",
			}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("HD View", {"dt": "HD Task", "label": "Kanban", "public": 1}):
		frappe.get_doc(
			{
				"doctype": "HD View",
				"label": "Kanban",
				"type": "kanban",
				"dt": "HD Task",
				"route_name": "TasksAgent",
				"public": 1,
				"is_default": 0,
				"filters": "{}",
				"order_by": "modified desc",
				"columns": "[]",
				"rows": "[]",
				"group_by_field": "status",
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()
