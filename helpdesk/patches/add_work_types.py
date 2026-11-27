"""Ticket types become work types: Software (covered), Network, Printer, Template change, Other.

Incident, Bug and Question are disabled (not deleted, so old tickets keep their value).
Unspecified stays the default for new tickets and means "not picked yet".
"""

import frappe

from helpdesk.consts import WORK_TYPES
RETIRED = ("Incident", "Bug", "Question")


def execute():
	for name in WORK_TYPES:
		if not frappe.db.exists("HD Ticket Type", name):
			frappe.get_doc({"doctype": "HD Ticket Type", "name": name}).insert(ignore_permissions=True)
		else:
			frappe.db.set_value("HD Ticket Type", name, "disabled", 0)
	for name in RETIRED:
		if frappe.db.exists("HD Ticket Type", name) and not frappe.db.get_value("HD Ticket Type", name, "is_system"):
			frappe.db.set_value("HD Ticket Type", name, "disabled", 1)
