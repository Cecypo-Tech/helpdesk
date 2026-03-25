import frappe
from frappe.translate import get_all_translations


@frappe.whitelist()
def get_my_open_counts():
	"""Return open ticket and task counts assigned to the current user."""
	user = frappe.session.user

	tickets = frappe.db.count(
		"HD Ticket",
		filters={
			"status": ["not in", ["Resolved", "Closed"]],
			"_assign": ["like", f"%{user}%"],
		},
	)

	tasks = frappe.db.count(
		"HD Task",
		filters={
			"status": ["!=", "Done"],
			"assigned_to": user,
		},
	)

	return {"tickets": tickets, "tasks": tasks}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_translations():
    language = None
    if frappe.session.user != "Guest":
        language = frappe.db.get_value("User", frappe.session.user, "language")
    if not language:
        language = frappe.db.get_single_value("System Settings", "language")
    return get_all_translations(language)
