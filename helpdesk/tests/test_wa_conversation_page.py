import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.integrations import wa

EXPECTED_KEYS = {
	"phone",
	"display_name",
	"last_message",
	"last_message_time",
	"last_direction",
	"ticket_status",
	"ticket_priority",
	"company",
	"assigned_to",
	"open_task_count",
}


class TestWAConversationPage(FrappeTestCase):
	"""Cover paging and filtering of the WhatsApp Business conversation list.

	The endpoint used to select every WhatsApp Message with no LIMIT and let the
	browser filter the result, so the page loaded 200+ conversations at once and
	a filter could only narrow what had already been fetched.
	"""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("DocType", "WhatsApp Message"):
			self.skipTest("frappe_whatsapp is not installed")
		self.tag = frappe.generate_hash(length=6)
		self.phones = [f"25470{i:07d}" for i in range(1, 6)]

	def _message(self, phone, direction="Incoming", message="hi", ticket=None):
		data = {
			"doctype": "WhatsApp Message",
			"type": direction,
			"message": message,
			"content_type": "text",
			"message_id": f"wamid.page.{frappe.generate_hash(length=10)}",
			"status": "received" if direction == "Incoming" else "sent",
			"profile_name": f"P{self.tag}",
		}
		if direction == "Incoming":
			data["from"] = phone
			data["to"] = "254700000001"
		else:
			data["from"] = "254700000001"
			data["to"] = phone
		if ticket:
			data["reference_doctype"] = "HD Ticket"
			data["reference_name"] = ticket
		doc = frappe.get_doc(data).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "WhatsApp Message", doc.name, ignore_permissions=True, force=True
		)
		return doc

	def _ticket(self, status=None):
		doc = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": f"Conv page {self.tag}",
			"raised_by": f"conv.{self.tag}@example.com",
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", doc.name, ignore_permissions=True, force=True
		)
		if status:
			# Re-fetch: HD Ticket.key is set once in before_insert, and saving
			# the same in-memory doc again trips CannotChangeConstantError.
			fresh = frappe.get_doc("HD Ticket", doc.name)
			fresh.status = status
			fresh.save(ignore_permissions=True)
		return doc

	def _seed(self):
		"""One conversation per phone, newest first in list order."""
		for phone in self.phones:
			self._message(phone, message=f"msg {self.tag}")

	def _phones_in(self, result):
		return [c["phone"] for c in result["conversations"]]

	# ── shape ─────────────────────────────────────────────────────────────

	def test_returns_conversations_and_has_more(self):
		self._seed()
		result = wa.get_whatsapp_conversations(limit=2)
		self.assertEqual(set(result.keys()), {"conversations", "has_more"})
		self.assertIsInstance(result["conversations"], list)
		self.assertIsInstance(result["has_more"], bool)

	def test_conversation_shape_is_unchanged(self):
		self._seed()
		conv = wa.get_whatsapp_conversations(limit=1)["conversations"][0]
		self.assertEqual(set(conv.keys()), EXPECTED_KEYS)

	# ── paging ────────────────────────────────────────────────────────────

	def test_page_is_capped_at_limit_and_reports_more(self):
		self._seed()
		result = wa.get_whatsapp_conversations(limit=2)
		self.assertEqual(len(result["conversations"]), 2)
		self.assertTrue(result["has_more"])

	def test_offset_returns_the_next_page_without_overlap(self):
		self._seed()
		first = self._phones_in(wa.get_whatsapp_conversations(limit=2, offset=0))
		second = self._phones_in(wa.get_whatsapp_conversations(limit=2, offset=2))
		self.assertEqual(len(first), 2)
		self.assertEqual(len(second), 2)
		self.assertFalse(set(first) & set(second), "pages overlap")

	def test_paging_covers_every_conversation(self):
		self._seed()
		everything = self._phones_in(wa.get_whatsapp_conversations(limit=500))
		paged = []
		offset = 0
		while True:
			page = wa.get_whatsapp_conversations(limit=2, offset=offset)
			paged.extend(self._phones_in(page))
			if not page["has_more"]:
				break
			offset += 2
		self.assertEqual(paged, everything, "paging lost or reordered conversations")

	def test_one_row_per_phone_however_many_messages(self):
		phone = self.phones[0]
		for i in range(4):
			self._message(phone, message=f"m{i} {self.tag}")
		listed = self._phones_in(wa.get_whatsapp_conversations(limit=200))
		self.assertEqual(listed.count(phone), 1)

	# ── filters ───────────────────────────────────────────────────────────

	def test_awaiting_filter_returns_only_incoming_last_messages(self):
		incoming_phone, outgoing_phone = self.phones[0], self.phones[1]
		self._message(incoming_phone, direction="Incoming", message=f"in {self.tag}")
		self._message(outgoing_phone, direction="Incoming", message=f"in {self.tag}")
		self._message(outgoing_phone, direction="Outgoing", message=f"out {self.tag}")

		awaiting = self._phones_in(wa.get_whatsapp_conversations(conv_filter="awaiting", limit=200))
		self.assertIn(incoming_phone, awaiting)
		self.assertNotIn(outgoing_phone, awaiting)

	def test_open_filter_excludes_resolved_tickets(self):
		open_phone, closed_phone = self.phones[0], self.phones[1]
		open_ticket = self._ticket()
		closed_ticket = self._ticket(status="Closed")
		self._message(open_phone, ticket=open_ticket.name, message=f"open {self.tag}")
		self._message(closed_phone, ticket=closed_ticket.name, message=f"closed {self.tag}")

		listed = self._phones_in(wa.get_whatsapp_conversations(conv_filter="open", limit=200))
		self.assertIn(open_phone, listed)
		self.assertNotIn(closed_phone, listed)

	def test_filters_apply_before_the_page_cut(self):
		# The whole point of server-side filtering: a filtered page must be full,
		# not a full page that has been thinned out afterwards.
		open_ticket = self._ticket()
		for phone in self.phones[:3]:
			self._message(phone, ticket=open_ticket.name, message=f"open {self.tag}")
		for phone in self.phones[3:]:
			self._message(phone, message=f"loose {self.tag}")

		page = wa.get_whatsapp_conversations(conv_filter="open", limit=2)
		self.assertEqual(len(page["conversations"]), 2)

	# ── search ────────────────────────────────────────────────────────────

	def test_search_matches_message_text(self):
		needle = f"pineapple{self.tag}"
		self._message(self.phones[0], message=needle)
		self._message(self.phones[1], message=f"unrelated {self.tag}")

		listed = self._phones_in(wa.get_whatsapp_conversations(search=needle, limit=200))
		self.assertEqual(listed, [self.phones[0]])

	def test_search_matches_phone_number(self):
		self._seed()
		listed = self._phones_in(
			wa.get_whatsapp_conversations(search=self.phones[2], limit=200)
		)
		self.assertIn(self.phones[2], listed)

	def test_search_with_no_match_returns_nothing(self):
		self._seed()
		result = wa.get_whatsapp_conversations(search=f"zzz-no-such-{self.tag}", limit=200)
		self.assertEqual(result["conversations"], [])
		self.assertFalse(result["has_more"])

	# ── guards ────────────────────────────────────────────────────────────

	def test_limit_is_clamped(self):
		self._seed()
		# A caller asking for everything must not be able to turn the endpoint
		# back into the unbounded query this replaced.
		result = wa.get_whatsapp_conversations(limit=100000)
		self.assertLessEqual(len(result["conversations"]), 200)
