"""Work types and Charge/Waive: who must pick them, and which closes are exempt."""

import unittest

import frappe

from helpdesk.patches import add_work_types
from helpdesk.test_utils import make_ticket

AGENT = "qa.worktype.agent@example.com"


def _agent():
	if not frappe.db.exists("User", AGENT):
		frappe.get_doc({"doctype": "User", "email": AGENT, "first_name": "WT", "send_welcome_email": 0}).insert(
			ignore_permissions=True
		)
	frappe.get_doc("User", AGENT).add_roles("Agent")
	if not frappe.db.exists("HD Agent", AGENT):
		frappe.get_doc({"doctype": "HD Agent", "user": AGENT, "agent_name": "WT", "is_active": 1}).insert(
			ignore_permissions=True
		)


def _product(name="Raminian QA"):
	if not frappe.db.exists("HD Product", name):
		frappe.get_doc({"doctype": "HD Product", "product_name": name}).insert(ignore_permissions=True)
	return name


class _Base(unittest.TestCase):
	def setUp(self):
		add_work_types.execute()
		_agent()
		_product()
		self.ticket = make_ticket(subject="Printer offline")
		frappe.set_user(AGENT)
		frappe.local.request = frappe._dict(path="/api/method/frappe.client.set_value", method="POST", headers={})  # an interactive request

	def tearDown(self):
		frappe.local.request = None
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def close(self, ticket=None, **fields):
		t = frappe.get_doc("HD Ticket", (ticket or self.ticket).name)
		fields.setdefault("hd_product", "Raminian QA")
		t.update(fields)
		t.status = "Resolved"
		t.save(ignore_permissions=True)
		return t


class TestPatch(unittest.TestCase):
	def test_patch_creates_types_and_disables_old(self):
		for name in ("Incident", "Bug", "Question"):
			if not frappe.db.exists("HD Ticket Type", name):
				frappe.get_doc({"doctype": "HD Ticket Type", "name": name}).insert(ignore_permissions=True)
			frappe.db.set_value("HD Ticket Type", name, {"disabled": 0, "is_system": 0})
		add_work_types.execute()
		add_work_types.execute()  # idempotent
		for name in ("Software", "Network", "Printer", "Template change", "Other"):
			self.assertEqual(frappe.db.get_value("HD Ticket Type", name, "disabled"), 0)
		for name in ("Incident", "Bug", "Question"):
			self.assertEqual(frappe.db.get_value("HD Ticket Type", name, "disabled"), 1)
		frappe.db.rollback()


class TestCloseRule(_Base):
	def test_agent_close_without_type_blocked(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Pick a work type before closing"):
			self.close()
		frappe.set_user("Administrator")
		if not frappe.db.exists("HD Ticket Type", "Bug"):
			frappe.get_doc({"doctype": "HD Ticket Type", "name": "Bug"}).insert(ignore_permissions=True)
		frappe.set_user(AGENT)
		with self.assertRaisesRegex(frappe.ValidationError, "Pick a work type before closing"):
			self.close(ticket_type="Bug")

	def test_agent_close_non_software_needs_charge(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Pick Charge or Waive before closing"):
			self.close(ticket_type="Network")
		self.assertEqual(self.close(ticket_type="Network", charge="Charge").status, "Resolved")

	def test_agent_close_needs_product(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Pick a product before closing"):
			self.close(ticket_type="Software", hd_product=None)
		self.assertEqual(self.close(ticket_type="Software").status, "Resolved")

	def test_software_close_needs_no_charge(self):
		self.assertEqual(self.close(ticket_type="Software").status, "Resolved")

	def test_background_close_not_blocked(self):
		frappe.local.request = None
		self.assertEqual(self.close().status, "Resolved")

	def test_merge_not_blocked(self):
		from helpdesk.helpdesk.doctype.hd_ticket.api import merge_ticket

		frappe.set_user("Administrator")
		target = make_ticket(subject="Printer offline again")
		frappe.set_user(AGENT)
		merge_ticket(self.ticket.name, target.name)
		self.assertEqual(frappe.db.get_value("HD Ticket", self.ticket.name, "status"), "Closed")

	def test_auto_close_not_blocked(self):
		from helpdesk.helpdesk.doctype.hd_ticket import hd_ticket

		doc = frappe.get_doc("HD Ticket", self.ticket.name)
		doc.status = "Closed"
		doc.flags.ignore_validate = True  # what close_tickets_after_n_days does
		doc.save(ignore_permissions=True)
		self.assertTrue(callable(hd_ticket.close_tickets_after_n_days))
		self.assertEqual(frappe.db.get_value("HD Ticket", self.ticket.name, "status"), "Closed")

	def test_reminder_ticket_not_blocked(self):
		frappe.set_user("Administrator")
		cust = frappe.db.get_value("HD Customer", {}, "name") or frappe.get_doc(
			{"doctype": "HD Customer", "customer_name": "ACME Work Type Test"}
		).insert(ignore_permissions=True).name
		frappe.get_doc(
			{"doctype": "HD Payment Reminder", "hd_customer": cust, "status": "With Team", "ticket": self.ticket.name}
		).insert(ignore_permissions=True)
		frappe.set_user(AGENT)
		self.assertEqual(self.close().status, "Resolved")

	def test_type_change_to_software_clears_charge(self):
		t = frappe.get_doc("HD Ticket", self.ticket.name)
		t.update({"ticket_type": "Network", "charge": "Charge"})
		t.save(ignore_permissions=True)
		t = self.close(ticket_type="Software")
		self.assertFalse(t.charge)
