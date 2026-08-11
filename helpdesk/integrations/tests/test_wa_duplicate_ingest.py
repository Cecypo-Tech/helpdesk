"""Meta delivers the same inbound message more than once.

Two mechanisms, both outside our control: an event is fanned out to every app
subscribed to the WABA, and any delivery that does not return 200 promptly is
retried. `frappe_whatsapp` inserts unconditionally — `message_id` carries no
unique constraint — so each delivery stored its own row and the conversation
showed the customer's message twice.
"""

import unittest

import frappe

PREFIX = "_test-dup-"


def _cleanup():
	frappe.db.sql(
		"DELETE FROM `tabWhatsApp Message` WHERE message_id LIKE %s", (PREFIX + "%",)
	)
	frappe.db.commit()


def _insert(message_id, msg_type="Incoming", **kwargs):
	doc = frappe.get_doc(
		{
			"doctype": "WhatsApp Message",
			"type": msg_type,
			"from": kwargs.pop("from_", "254700111222"),
			"message": kwargs.pop("message", "hello"),
			"message_id": message_id,
			"content_type": "text",
			**kwargs,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _rows(message_id):
	return frappe.get_all(
		"WhatsApp Message", filters={"message_id": message_id}, pluck="name"
	)


class TestWaDuplicateIngest(unittest.TestCase):
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

	def test_same_message_id_is_stored_once(self):
		wamid = PREFIX + "same"
		_insert(wamid)
		frappe.db.commit()
		_insert(wamid)
		frappe.db.commit()

		self.assertEqual(len(_rows(wamid)), 1)

	def test_first_row_is_the_survivor(self):
		wamid = PREFIX + "first"
		first = _insert(wamid, message="original")
		frappe.db.commit()
		_insert(wamid, message="redelivered")
		frappe.db.commit()

		self.assertEqual(_rows(wamid), [first.name])
		self.assertEqual(
			frappe.db.get_value("WhatsApp Message", first.name, "message"), "original"
		)

	def test_distinct_message_ids_both_survive(self):
		a, b = PREFIX + "a", PREFIX + "b"
		_insert(a)
		_insert(b)
		frappe.db.commit()

		self.assertEqual(len(_rows(a)), 1)
		self.assertEqual(len(_rows(b)), 1)

	def test_blank_message_id_never_dedupes(self):
		# Rows can reach the table before Meta has issued an id. Treating "" as a
		# value would collapse every one of them into a single row.
		for _ in range(3):
			_insert("", message="no id yet")
		frappe.db.commit()

		names = frappe.get_all(
			"WhatsApp Message",
			filters={"message_id": "", "message": "no id yet"},
			pluck="name",
		)
		self.assertEqual(len(names), 3)
		frappe.db.sql("DELETE FROM `tabWhatsApp Message` WHERE message = 'no id yet'")
		frappe.db.commit()

	def test_outgoing_is_not_flagged_against_incoming(self):
		# The guard is scoped to inbound. Outgoing rows take their id from Meta's
		# send response, and collapsing one into an inbound row would erase a
		# reply an agent actually sent.
		#
		# Asserted against the hook rather than a second insert: inserting an
		# Outgoing row runs frappe_whatsapp's send path, which calls Meta and
		# rewrites message_id from the response.
		from helpdesk.integrations.wa import (
			_WA_DUPLICATE_FLAG,
			flag_duplicate_whatsapp_message,
		)

		wamid = PREFIX + "out"
		_insert(wamid, msg_type="Incoming")
		frappe.db.commit()

		outgoing = frappe.get_doc(
			{
				"doctype": "WhatsApp Message",
				"type": "Outgoing",
				"to": "254700111222",
				"message": "reply",
				"message_id": wamid,
				"content_type": "text",
			}
		)
		flag_duplicate_whatsapp_message(outgoing)

		self.assertFalse(outgoing.flags.get(_WA_DUPLICATE_FLAG))

	def test_duplicate_does_not_link_a_second_ticket(self):
		wamid = PREFIX + "ticket"
		_insert(wamid)
		frappe.db.commit()
		before = frappe.db.count("HD Ticket")

		_insert(wamid)
		frappe.db.commit()

		self.assertEqual(frappe.db.count("HD Ticket"), before)


class TestWaMessageIdSeen(unittest.TestCase):
	@classmethod
	def tearDownClass(cls):
		_cleanup()

	def setUp(self):
		frappe.set_user("Administrator")

	def test_unknown_id_is_not_seen(self):
		from helpdesk.integrations.wa import _wa_message_id_seen

		self.assertFalse(_wa_message_id_seen(PREFIX + "never-stored"))

	def test_stored_id_is_seen(self):
		from helpdesk.integrations.wa import _wa_message_id_seen

		wamid = PREFIX + "seen"
		_insert(wamid)
		frappe.db.commit()

		self.assertTrue(_wa_message_id_seen(wamid))
