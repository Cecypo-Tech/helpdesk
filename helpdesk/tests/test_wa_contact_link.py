import frappe
from frappe.tests.utils import FrappeTestCase
from unittest.mock import patch

from helpdesk.integrations import wa, wa_ingest

# Derived per run rather than fixed: a shared literal meant this module and
# test_contact_phone_suffix created contacts with the same trailing digits, so
# in a full-suite run every "unique match" assertion turned ambiguous.


def _settings(**overrides):
	"""Stand-in for WhatsApp Helpdesk Settings so tests don't mutate the singleton."""
	base = frappe._dict(
		enabled=1,
		unknown_contact_action="Create Contact and Ticket",
		new_conversation_timeout_hours=24,
		placeholder_email_domain="whatsapp.placeholder.local",
		customer_reply_status=None,
		agent_reply_status=None,
		default_ticket_type=None,
		default_team=None,
	)
	base.update(overrides)
	return base


class TestWAContactLink(FrappeTestCase):
	"""Cover contact/customer linkage when a customer replies after resolution.

	Regression cover for: the follow-up ticket re-derived the contact from the
	phone number alone, so a contact stored in national format (0799…) never
	matched WhatsApp's international format (254799…). The automation then
	invented a fresh Contact from the WhatsApp profile name, and the customer
	link vanished.
	"""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		# The code under test creates Contacts by itself — that is the bug. A
		# failing assertion would otherwise strand them in the real contact
		# list, so sweep by the test phone number rather than trusting each
		# test to know what got created.
		self.addCleanup(self._sweep_automation_contacts)
		frappe.set_user("Administrator")
		if not frappe.db.exists("DocType", "WhatsApp Message"):
			self.skipTest("frappe_whatsapp is not installed")
		self.suffix = frappe.generate_hash(length=6)
		# 9 random subscriber digits shared by both spellings, so intl and local
		# match each other and nothing else. hash() was too small a keyspace and
		# collided with the other WA test modules across a full-suite run.
		body = f"{int(frappe.generate_hash(length=12), 16) % 10**9:09d}"
		self.intl = "2547" + body
		self.local = "0" + body

	@staticmethod
	def _sweep_automation_contacts():
		for row in frappe.get_all(
			"Contact", filters={"first_name": "WA Profile Name"}, pluck="name"
		):
			try:
				frappe.delete_doc(
					"Contact", row, ignore_permissions=True, force=True,
					delete_permanently=True,
				)
			except Exception:
				pass

	def _make_customer(self):
		doc = frappe.get_doc({
			"doctype": "HD Customer", "customer_name": f"Cust {self.suffix}",
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "HD Customer", doc.name, ignore_permissions=True, force=True
		)
		return doc

	def _make_contact(self, phone, customer=None, first_name=None):
		data = {
			"doctype": "Contact",
			"first_name": first_name or f"Jane {self.suffix}",
			"email_ids": [{
				"doctype": "Contact Email",
				"email_id": f"jane.{self.suffix}@example.com",
				"is_primary": 1,
			}],
			"phone_nos": [{
				"doctype": "Contact Phone", "phone": phone, "is_primary_mobile_no": 1,
			}],
		}
		if customer:
			data["links"] = [{
				"doctype": "Dynamic Link",
				"link_doctype": "HD Customer",
				"link_name": customer,
			}]
		doc = frappe.get_doc(data).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "Contact", doc.name, ignore_permissions=True, force=True
		)
		return doc

	def _make_ticket(self, contact=None, subject=None):
		data = {
			"doctype": "HD Ticket",
			"subject": subject or f"Ticket {self.suffix}",
			"raised_by": f"jane.{self.suffix}@example.com",
		}
		if contact:
			data["contact"] = contact
		doc = frappe.get_doc(data).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", doc.name, ignore_permissions=True, force=True
		)
		return doc

	def _incoming(self, ticket=None, message="hello"):
		data = {
			"doctype": "WhatsApp Message",
			"type": "Incoming",
			"from": self.intl,
			"message": message,
			"content_type": "text",
			"message_id": f"wamid.link.{frappe.generate_hash(length=10)}",
			"status": "received",
			"profile_name": "WA Profile Name",
		}
		if ticket:
			data["reference_doctype"] = "HD Ticket"
			data["reference_name"] = ticket
		# frappe.enqueue is patched out for the insert itself. on_whatsapp_message_insert
		# now only ENQUEUES wa_ingest.process_incoming_message (enqueue_after_commit=True)
		# rather than linking inline, so leaving it live hands the work to a real RQ
		# worker: the assertions below race it, and anything it creates lands after
		# cleanup has run. That is what left stray Contacts on this site.
		with patch("frappe.enqueue"):
			doc = frappe.get_doc(data).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "WhatsApp Message", doc.name, ignore_permissions=True, force=True
		)
		return doc

	def _deliver(self, msg):
		"""Run the ingestion job for a message, the way a worker eventually would.

		Called explicitly so the linking these tests assert on has actually
		happened by the time they look. Must run inside whatever _fw_settings
		patch the test set up -- the job reads settings too. Same shape as
		helpdesk/integrations/tests/test_no_erpnext.py.
		"""
		wa_ingest.process_incoming_message(msg.name)
		return msg

	def _resolve(self, ticket):
		doc = frappe.get_doc("HD Ticket", ticket)
		doc.status = "Resolved"
		doc.save(ignore_permissions=True)

	# ── phone matching ────────────────────────────────────────────────────

	def test_national_format_matches_international(self):
		contact = self._make_contact(self.local)
		self.assertEqual(wa.match_phone_to_contact(self.intl), contact.name)

	def test_exact_match_still_works(self):
		contact = self._make_contact(self.intl)
		self.assertEqual(wa.match_phone_to_contact(self.intl), contact.name)

	def test_ambiguous_loose_match_returns_none_rather_than_guessing(self):
		# Two contacts whose numbers both end in the same subscriber digits.
		# Attaching the conversation to either would be a guess, and guessing
		# wrong shows one customer's messages under another's ticket.
		self._make_contact(self.local, first_name=f"A {self.suffix}")
		# Different country code, same subscriber digits.
		self._make_contact(f"+1 {self.intl[-9:]}", first_name=f"B {self.suffix}")
		self.assertIsNone(wa.match_phone_to_contact(self.intl))

	def test_unrelated_number_does_not_match(self):
		self._make_contact("07" + self.intl[-8:-1] + "9")
		self.assertIsNone(wa.match_phone_to_contact(self.intl))

	# ── linkage across the resolve boundary ───────────────────────────────

	def test_reply_after_resolution_keeps_contact_and_customer(self):
		customer = self._make_customer()
		contact = self._make_contact(self.local, customer=customer.name)
		t1 = self._make_ticket(contact=contact.name)
		self.assertEqual(frappe.db.get_value("HD Ticket", t1.name, "customer"), customer.name)
		self._incoming(ticket=t1.name)
		self._resolve(t1.name)

		with patch.object(wa, "_fw_settings", return_value=_settings()):
			reply = self._deliver(self._incoming(message="one more thing"))

		new_ticket = frappe.db.get_value("WhatsApp Message", reply.name, "reference_name")
		self.assertIsNotNone(new_ticket)
		self.assertNotEqual(str(new_ticket), str(t1.name))
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", new_ticket, ignore_permissions=True, force=True
		)

		row = frappe.db.get_value(
			"HD Ticket", new_ticket, ["contact", "customer"], as_dict=True
		)
		self.assertEqual(row.contact, contact.name)
		self.assertEqual(row.customer, customer.name)

	def test_reply_after_resolution_does_not_invent_a_contact(self):
		customer = self._make_customer()
		contact = self._make_contact(self.local, customer=customer.name)
		t1 = self._make_ticket(contact=contact.name)
		self._incoming(ticket=t1.name)
		self._resolve(t1.name)

		before = frappe.db.count("Contact")
		with patch.object(wa, "_fw_settings", return_value=_settings()):
			reply = self._deliver(self._incoming(message="one more thing"))
		after = frappe.db.count("Contact")

		new_ticket = frappe.db.get_value("WhatsApp Message", reply.name, "reference_name")
		if new_ticket:
			self.addCleanup(
				frappe.delete_doc, "HD Ticket", new_ticket, ignore_permissions=True, force=True
			)
		self.assertEqual(after, before, "a duplicate Contact was created")

	def test_reply_while_open_still_attaches_to_the_same_ticket(self):
		contact = self._make_contact(self.local)
		t1 = self._make_ticket(contact=contact.name)
		self._incoming(ticket=t1.name)

		with patch.object(wa, "_fw_settings", return_value=_settings()):
			reply = self._deliver(self._incoming(message="still talking"))

		self.assertEqual(
			str(frappe.db.get_value("WhatsApp Message", reply.name, "reference_name")),
			str(t1.name),
		)

	def test_first_ever_message_still_creates_a_contact(self):
		# No prior ticket to inherit from: the unknown-contact path must be
		# untouched by the inheritance change.
		before = frappe.db.count("Contact")
		with patch.object(wa, "_fw_settings", return_value=_settings()):
			msg = self._deliver(self._incoming(message="brand new conversation"))

		new_ticket = frappe.db.get_value("WhatsApp Message", msg.name, "reference_name")
		self.assertIsNotNone(new_ticket)
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", new_ticket, ignore_permissions=True, force=True
		)
		self.assertEqual(frappe.db.count("Contact"), before + 1)
		created = frappe.db.get_value("HD Ticket", new_ticket, "contact")
		self.assertTrue(created)
		self.addCleanup(
			frappe.delete_doc, "Contact", created, ignore_permissions=True, force=True
		)
