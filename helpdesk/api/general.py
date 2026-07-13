import frappe
from frappe.translate import get_all_translations


@frappe.whitelist()
def get_my_open_counts():
	"""Return open ticket and task counts assigned to the current user,
	plus the total open ticket count for the WhatsApp Business channel
	(unlike tickets/tasks, that page is a shared inbox, not per-agent)."""
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

	whatsapp = 0
	if frappe.db.exists("DocType", "WhatsApp Message"):
		whatsapp = frappe.db.sql(
			"""
			SELECT COUNT(DISTINCT t.name)
			FROM `tabHD Ticket` t
			INNER JOIN `tabWhatsApp Message` wm
				ON wm.reference_doctype = 'HD Ticket' AND wm.reference_name = t.name
			WHERE t.status NOT IN ('Resolved', 'Closed')
			"""
		)[0][0]

	return {"tickets": tickets, "tasks": tasks, "whatsapp": whatsapp}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_translations():
    language = None
    if frappe.session.user != "Guest":
        language = frappe.db.get_value("User", frappe.session.user, "language")
    if not language:
        language = frappe.db.get_single_value("System Settings", "language")
    return get_all_translations(language)
