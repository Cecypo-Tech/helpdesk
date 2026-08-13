"""Meta's 24-hour reply window is per phone number, not per ticket.

Scoping it to one ticket refused an agent's reply whenever the customer's
latest message had opened a new ticket — the previous one having been resolved,
closed or aged past the conversation timeout. Meta would have accepted it.
"""

import unittest

import frappe

from helpdesk.integrations import wa

PREFIX = "_test-window-"
NUMBER = "254700444555"
OTHER = "254700444999"


def _cleanup():
	frappe.db.sql(
		"DELETE FROM `tabWhatsApp Message` WHERE message_id LIKE %s", (PREFIX + "%",)
	)
	for t in frappe.get_all(
		"HD Ticket", filters={"subject": ["like", PREFIX + "%"]}, pluck="name"
	):
		frappe.delete_doc(
			"HD Ticket", t, force=True, ignore_permissions=True, delete_permanently=True
		)
	frappe.db.commit()


def _ticket(suffix):
	doc = frappe.get_doc(
		{
			"doctype": "HD Ticket",
			"subject": PREFIX + suffix,
			"description": "probe",
			"raised_by": f"whatsapp+{NUMBER}@whatsapp.placeholder.local",
		}
	)
	doc.flags.skip_ack_email = True
	doc.insert(ignore_permissions=True)
	return doc.name


def _message(ticket, msg_type="Incoming", number=NUMBER, age_hours=0, suffix=""):
	doc = frappe.get_doc(
		{
			"doctype": "WhatsApp Message",
			"type": msg_type,
			"from": number if msg_type == "Incoming" else None,
			"to": None if msg_type == "Incoming" else number,
			"message": "hi",
			"message_id": PREFIX + (suffix or frappe.generate_hash(length=6)),
			"content_type": "text",
			"reference_doctype": "HD Ticket",
			"reference_name": ticket,
		}
	)
	doc.insert(ignore_permissions=True)
	if age_hours:
		frappe.db.sql(
			"UPDATE `tabWhatsApp Message` SET creation = NOW() - INTERVAL %s HOUR WHERE name = %s",
			(age_hours, doc.name),
		)
	frappe.db.commit()
	return doc.name


class TestFwReplyWindow(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		_cleanup()

	@classmethod
	def tearDownClass(cls):
		_cleanup()

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		_cleanup()

	def test_no_incoming_means_closed(self):
		t = _ticket("none")
		_message(t, msg_type="Outgoing")
		self.assertFalse(wa._fw_reply_window_open(t))

	def test_recent_incoming_on_this_ticket_is_open(self):
		t = _ticket("recent")
		_message(t)
		self.assertTrue(wa._fw_reply_window_open(t))

	def test_old_incoming_is_closed(self):
		t = _ticket("old")
		_message(t, age_hours=30)
		self.assertFalse(wa._fw_reply_window_open(t))

	def test_recent_incoming_on_another_ticket_keeps_it_open(self):
		# The regression. The customer's newest message opened a second ticket;
		# an agent replying on the first one was told the window had closed.
		old_ticket = _ticket("old-conv")
		_message(old_ticket, age_hours=40)
		new_ticket = _ticket("new-conv")
		_message(new_ticket)

		self.assertTrue(wa._fw_reply_window_open(old_ticket))
		self.assertTrue(wa._fw_reply_window_open(new_ticket))

	def test_another_persons_message_does_not_open_the_window(self):
		# Per phone number, not per site — someone else writing in must not
		# license a free-form send to this customer.
		mine = _ticket("mine")
		_message(mine, age_hours=40)
		theirs = _ticket("theirs")
		_message(theirs, number=OTHER)

		self.assertFalse(wa._fw_reply_window_open(mine))

	def test_outgoing_only_ticket_resolves_the_phone(self):
		# normalized_phone is the recipient on an outgoing row, so a ticket whose
		# newest message is our own reply still identifies the customer.
		t = _ticket("outgoing-newest")
		_message(t, age_hours=2)
		_message(t, msg_type="Outgoing", age_hours=1)

		self.assertEqual(wa._fw_conversation_phone(t), NUMBER)
		self.assertTrue(wa._fw_reply_window_open(t))
