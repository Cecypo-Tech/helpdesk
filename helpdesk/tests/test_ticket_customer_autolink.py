"""An agent setting the customer on a ticket teaches helpdesk who the contact is."""

import unittest

import frappe

from helpdesk.utils import get_customer

PREFIX = "_AL "


class TestTicketCustomerAutolink(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.cleanup()
		self.addCleanup(self.cleanup)
		for name in ("A", "B"):
			frappe.get_doc({"doctype": "HD Customer", "customer_name": PREFIX + name}).insert(
				ignore_permissions=True
			)
		self.a, self.b = PREFIX + "A", PREFIX + "B"
		self.contact = frappe.get_doc({
			"doctype": "Contact",
			"first_name": PREFIX + "caller",
			"phone_nos": [{"phone": "254700999888", "is_primary_mobile_no": 1}],
		}).insert(ignore_permissions=True).name

	def cleanup(self):
		for t in frappe.get_all("HD Ticket", filters={"subject": ["like", PREFIX + "%"]}, pluck="name"):
			frappe.delete_doc("HD Ticket", t, force=True, ignore_permissions=True, delete_permanently=True)
		for c in frappe.get_all("Contact", filters={"first_name": ["like", PREFIX + "%"]}, pluck="name"):
			frappe.delete_doc("Contact", c, force=True, ignore_permissions=True, delete_permanently=True)
		for c in frappe.get_all("HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"):
			frappe.delete_doc("HD Customer", c, force=True, ignore_permissions=True)
		frappe.db.commit()

	def ticket(self, contact="__self__", customer=None):
		return frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": PREFIX + "ticket",
			"contact": self.contact if contact == "__self__" else contact,
			"customer": customer,
		}).insert(ignore_permissions=True)

	def set_customer(self, t, customer):
		# Re-read: the in-memory doc from insert() holds `key` as a UUID object,
		# which Frappe's set_only_once check sees as a change.
		doc = frappe.get_doc("HD Ticket", t.name)
		doc.customer = customer
		doc.save(ignore_permissions=True)

	def link(self, customer):
		c = frappe.get_doc("Contact", self.contact)
		c.append("links", {"link_doctype": "HD Customer", "link_name": customer})
		c.save(ignore_permissions=True)

	def test_setting_customer_links_contact(self):
		t = self.ticket()
		self.set_customer(t, self.a)
		self.assertEqual(get_customer(self.contact), [self.a])

	def test_next_ticket_fills_itself(self):
		t = self.ticket()
		self.set_customer(t, self.a)
		self.assertEqual(self.ticket().customer, self.a)

	def test_existing_link_is_left_alone(self):
		self.link(self.a)
		t = self.ticket()
		self.set_customer(t, self.b)
		self.assertEqual(get_customer(self.contact), [self.a])

	def test_clearing_customer_does_nothing(self):
		t = self.ticket()
		self.set_customer(t, self.a)
		self.set_customer(t, "")
		self.assertEqual(get_customer(self.contact), [self.a])

	def test_no_contact_is_a_noop(self):
		t = self.ticket(contact=None)
		self.set_customer(t, self.a)
		self.assertEqual(get_customer(self.contact), [])

	def test_insert_with_customer_links(self):
		self.ticket(customer=self.a)
		self.assertEqual(get_customer(self.contact), [self.a])
