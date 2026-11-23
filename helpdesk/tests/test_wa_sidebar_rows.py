from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.integrations import wa

LINE = "wa-sidebar-rows-test-line"
PHONE = "254799000111"
JID = f"{PHONE}@s.whatsapp.net"


class TestWASidebarRows(FrappeTestCase):
	"""What a sidebar row needs: who sent the last message, the raw text (the
	client labels media), and the contact's company even when the WA Contact
	has none of its own."""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("WA Line", LINE):
			frappe.get_doc({"doctype": "WA Line", "instance_name": LINE}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "WA Line", LINE, ignore_permissions=True, force=True)
		self.addCleanup(frappe.db.delete, "WA Message", {"line": LINE})
		self.addCleanup(frappe.db.delete, "WA Contact", {"jid": JID})

	def _msg(self, direction="Incoming", message="hi", content_type="text", sender_name="Customer", minutes_ago=0):
		doc = frappe.get_doc({
			"doctype": "WA Message",
			"jid": JID,
			"direction": direction,
			"message": message,
			"content_type": content_type,
			"message_id": frappe.generate_hash(length=10),
			"sender_name": sender_name,
			"sender_jid": JID if direction == "Incoming" else "",
			"status": "Delivered",
			"line": LINE,
		}).insert(ignore_permissions=True)
		if minutes_ago:
			frappe.db.set_value(
				"WA Message", doc.name, "creation",
				frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-minutes_ago),
				update_modified=False,
			)
		return doc

	def _row(self):
		rows = wa.get_wa_conversations(line=LINE)["conversations"]
		return next(r for r in rows if r["jid"] == JID)

	def _customer_contact(self):
		customer = f"Sidebar Rows Co {frappe.generate_hash(length=4)}"
		frappe.get_doc({"doctype": "HD Customer", "customer_name": customer}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "HD Customer", customer, ignore_permissions=True, force=True)
		contact = frappe.get_doc({
			"doctype": "Contact",
			"first_name": "Sidebar Rows",
			"phone_nos": [{"phone": f"+{PHONE}", "is_primary_mobile_no": 1}],
			"links": [{"link_doctype": "HD Customer", "link_name": customer}],
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Contact", contact.name, ignore_permissions=True, force=True)
		return customer

	# ── who sent it ───────────────────────────────────────────────────────

	def test_one_to_one_outgoing_carries_the_agents_name_and_user(self):
		self._msg("Outgoing", "on it", sender_name="Mary Agent")
		row = self._row()
		self.assertEqual(row["last_sender_name"], "Mary Agent")
		self.assertEqual(row["last_sender_user"], "Administrator")

	def test_one_to_one_incoming_carries_the_sender_name(self):
		self._msg("Incoming", "hello", sender_name="Malanga")
		self.assertEqual(self._row()["last_sender_name"], "Malanga")

	# ── raw text ──────────────────────────────────────────────────────────

	def test_media_without_caption_is_returned_empty_for_the_client_to_label(self):
		self._msg("Incoming", "", content_type="image")
		row = self._row()
		self.assertEqual(row["last_message"], "")
		self.assertEqual(row["content_type"], "image")

	# ── unread ────────────────────────────────────────────────────────────

	def test_reactions_do_not_count_as_unread(self):
		# A customer reacting to our reply is not a message waiting for an answer.
		self._msg("Incoming", "hello", minutes_ago=2)
		self._msg("Incoming", "👍", content_type="reaction")
		self.assertEqual(self._row()["unread_count"], 1)
		line = next(l for l in wa.get_wa_lines() if l["name"] == LINE)
		self.assertEqual(line["unread"], 1)

	def test_a_read_chat_with_a_new_reaction_stays_read(self):
		self._msg("Incoming", "hello", minutes_ago=2)
		wa._mark_conversation_read_for_user(JID, "Administrator")
		self.addCleanup(frappe.db.delete, "WA Conversation Read State", {"jid": JID})
		self._msg("Incoming", "👍", content_type="reaction")
		self.assertEqual(self._row()["unread_count"], 0)
		unread = wa.get_wa_conversations(line=LINE, conv_filter="unread")["conversations"]
		self.assertNotIn(JID, [r["jid"] for r in unread])

	# ── company ───────────────────────────────────────────────────────────

	def test_company_falls_back_to_the_contacts_hd_customer(self):
		customer = self._customer_contact()
		self._msg("Incoming", "hello")
		self.assertEqual(self._row()["company"], customer)

	def test_wa_contact_company_wins_over_the_fallback(self):
		self._customer_contact()
		own = f"Own Co {frappe.generate_hash(length=4)}"
		frappe.get_doc({"doctype": "HD Customer", "customer_name": own}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "HD Customer", own, ignore_permissions=True, force=True)
		frappe.get_doc({"doctype": "WA Contact", "jid": JID, "company": own}).insert(ignore_permissions=True)
		self._msg("Incoming", "hello")
		self.assertEqual(self._row()["company"], own)

	def test_lid_chat_with_a_known_phone_gets_the_fallback_company(self):
		# @lid chats hide the number in the JID; the WA Contact still knows it.
		customer = self._customer_contact()
		lid = "77001122334455@lid"
		self.addCleanup(frappe.db.delete, "WA Contact", {"jid": lid})
		frappe.get_doc({"doctype": "WA Contact", "jid": lid, "phone": PHONE}).insert(ignore_permissions=True)
		frappe.get_doc({
			"doctype": "WA Message", "jid": lid, "direction": "Incoming", "message": "hi",
			"content_type": "text", "message_id": frappe.generate_hash(length=10),
			"sender_name": "Lid Sender", "sender_jid": lid, "status": "Delivered", "line": LINE,
		}).insert(ignore_permissions=True)
		rows = wa.get_wa_conversations(line=LINE)["conversations"]
		self.assertEqual(next(r for r in rows if r["jid"] == lid)["company"], customer)

	def test_hd_customers_for_phones_maps_only_matched_phones(self):
		customer = self._customer_contact()
		self.assertEqual(
			wa._hd_customers_for_phones([PHONE, "254700000000"]),
			{PHONE: customer},
		)


class TestWABASidebarRows(FrappeTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("DocType", "WhatsApp Message"):
			self.skipTest("frappe_whatsapp is not installed")
		# Test sites have no WhatsApp Account; the account lookup isn't under test.
		p = patch(
			"frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message.WhatsAppMessage.set_whatsapp_account"
		)
		p.start()
		self.addCleanup(p.stop)
		self.phone = "2547990" + frappe.generate_hash(length=5).translate(str.maketrans("abcdef", "123456"))[:5]

	def test_outgoing_row_names_the_agent(self):
		# Inserting an Outgoing row sends it (needs a WhatsApp Account), so insert
		# it as Incoming and flip the type; normalized_phone is the customer either way.
		doc = frappe.get_doc({
			"doctype": "WhatsApp Message",
			"type": "Incoming",
			"from": self.phone,
			"to": "254700999888",
			"message": "on it",
			"content_type": "text",
			"message_id": f"wamid.rows.{frappe.generate_hash(length=10)}",
			"status": "received",
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "WhatsApp Message", doc.name, ignore_permissions=True, force=True)
		frappe.db.set_value("WhatsApp Message", doc.name, {"type": "Outgoing", "to": self.phone})
		rows = wa.get_whatsapp_conversations(search=self.phone, limit=200)["conversations"]
		row = next(r for r in rows if r["phone"] == self.phone)
		self.assertEqual(row["last_sender_user"], "Administrator")
		self.assertEqual(
			row["last_sender_name"],
			frappe.db.get_value("User", "Administrator", "full_name") or "Administrator",
		)
