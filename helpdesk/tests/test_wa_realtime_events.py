"""The realtime event a stored WhatsApp Business message emits.

The conversation list keys on phone number, not ticket, yet the event that
refreshed it used to fire only for ticket-linked messages. A message from an
unknown number under "Skip Ticket Creation", any message while the integration
was disabled, or an outgoing row without a ticket all landed in the table and
never told anyone — the list showed the previous message until something
unrelated refreshed it.

Every stored row is now announced with enough of itself for the list to update
in place and the thread to append the bubble, so clients no longer refetch the
whole conversation per message.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.integrations import wa

MESSAGE_POST = (
	"frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message"
	".whatsapp_message.make_post_request"
)
EVENT = "helpdesk:whatsapp-message"
SETTINGS = "WhatsApp Helpdesk Settings"


class TestWARealtimeEvents(FrappeTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("DocType", "WhatsApp Message"):
			self.skipTest("frappe_whatsapp is not installed")
		self.tag = frappe.generate_hash(length=6)
		self.phone = "254700" + self.tag[:6].encode().hex()[:6]
		for field in ("enabled", "unknown_contact_action"):
			before = frappe.db.get_single_value(SETTINGS, field)
			self.addCleanup(self._restore, field, before)

	@staticmethod
	def _restore(field, value):
		frappe.db.set_single_value(SETTINGS, field, value)
		frappe.clear_cache(doctype=SETTINGS)

	def _set(self, **values):
		for field, value in values.items():
			frappe.db.set_single_value(SETTINGS, field, value)
		frappe.clear_cache(doctype=SETTINGS)

	def _insert(self, direction, **overrides):
		data = {
			"doctype": "WhatsApp Message",
			"type": direction,
			"message": f"hello {self.tag}",
			"content_type": "text",
			"message_id": f"wamid.rt.{frappe.generate_hash(length=10)}",
			"profile_name": f"P{self.tag}",
		}
		if direction == "Incoming":
			data["from"] = self.phone
		else:
			data["to"] = self.phone
		data.update(overrides)
		with patch("frappe.enqueue"), patch(
			MESSAGE_POST, return_value={"messages": [{"id": data["message_id"]}]}
		), patch("helpdesk.integrations.wa.frappe.publish_realtime") as publish:
			doc = frappe.get_doc(data).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "WhatsApp Message", doc.name, ignore_permissions=True, force=True
		)
		return doc, publish

	@staticmethod
	def _events(publish):
		return [c for c in publish.call_args_list if c.args and c.args[0] == EVENT]

	def test_an_unlinked_incoming_message_is_announced(self):
		self._set(enabled=1, unknown_contact_action="Skip Ticket Creation")
		doc, publish = self._insert("Incoming")

		events = self._events(publish)
		self.assertEqual(len(events), 1)
		payload = events[0].kwargs["message"]
		self.assertEqual(payload["name"], doc.name)
		self.assertEqual(payload["phone"], self.phone)
		self.assertEqual(payload["ticket"], "")
		self.assertTrue(payload["is_incoming"])
		self.assertEqual(payload["origin"], "insert")
		# Must not race the row: the client applies it and reconciles later.
		self.assertTrue(events[0].kwargs.get("after_commit"))

	def test_outgoing_is_announced_even_with_the_integration_disabled(self):
		self._set(enabled=0)
		doc, publish = self._insert("Outgoing")

		events = self._events(publish)
		self.assertEqual(len(events), 1)
		payload = events[0].kwargs["message"]
		self.assertEqual(payload["name"], doc.name)
		self.assertEqual(payload["phone"], self.phone)
		self.assertFalse(payload["is_incoming"])

	def test_payload_carries_what_the_thread_renders(self):
		self._set(enabled=1)
		doc, publish = self._insert("Incoming", message=f"full text {self.tag}")
		payload = self._events(publish)[0].kwargs["message"]
		for key in (
			"name", "phone", "ticket", "is_incoming", "origin", "type", "content_type",
			"message", "attach", "status", "creation", "profile_name", "message_id",
			"reply_to_message_id", "is_reply", "sender_full_name",
		):
			self.assertIn(key, payload, key)
		# The thread renders this as the bubble, so it is the whole text.
		self.assertEqual(payload["message"], f"full text {self.tag}")
		self.assertEqual(payload["type"], "Incoming")
		self.assertIsInstance(payload["creation"], str)

	def test_ingest_announces_the_ticket_link(self):
		"""The job's event is what the ticket thread keys on."""
		self._set(enabled=1)
		doc, _ = self._insert("Incoming")
		ticket = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": f"rt {self.tag}",
			"raised_by": f"rt.{self.tag}@example.com",
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", ticket.name, ignore_permissions=True, force=True
		)
		doc.db_set("reference_doctype", "HD Ticket", update_modified=False)
		doc.db_set("reference_name", ticket.name, update_modified=False)

		with patch("helpdesk.integrations.wa.frappe.publish_realtime") as publish, patch(
			"helpdesk.integrations.wa.frappe.db.commit"
		):
			wa._announce_incoming(doc, ticket.name, "P", wa._fw_settings())

		payload = self._events(publish)[0].kwargs["message"]
		self.assertEqual(payload["ticket"], str(ticket.name))
		self.assertEqual(payload["origin"], "ingest")
		self.assertEqual(payload["name"], doc.name)
		self.assertEqual(payload["phone"], self.phone)

	def test_a_reaction_never_opens_a_ticket(self):
		"""A thumbs-up on a resolved conversation is not a new request."""
		self._set(enabled=1, unknown_contact_action="Create Contact and Ticket")
		doc, _ = self._insert(
			"Incoming", content_type="reaction", message="👍",
			reply_to_message_id="wamid.none",
		)
		before = frappe.db.count("HD Ticket")
		wa.link_incoming_message(doc)
		self.assertEqual(frappe.db.count("HD Ticket"), before)
		doc.reload()
		self.assertFalse(doc.reference_name)
