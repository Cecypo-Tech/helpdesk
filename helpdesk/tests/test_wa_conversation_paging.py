import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.integrations.wa import get_wa_conversations

LINE = "wa-paging-test-line"


class TestWAConversationPaging(FrappeTestCase):
	"""get_wa_conversations returns one page at a time.

	It used to return every conversation on a line with no LIMIT, and the
	browser did the searching and filtering. Everything that narrows the list
	therefore has to happen in SQL now: a filter applied after the page is cut
	produces short and inconsistently sized pages, which is the failure these
	tests are really guarding against.
	"""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("WA Line", LINE):
			frappe.get_doc({"doctype": "WA Line", "instance_name": LINE}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "WA Line", LINE, ignore_permissions=True, force=True)
		self.addCleanup(frappe.db.delete, "WA Message", {"line": LINE})
		self.addCleanup(frappe.db.delete, "WA Conversation Read State", {"jid": ("like", "%pagingtest%")})

	def _msg(self, jid, message="hello", direction="Incoming", minutes_ago=0):
		doc = frappe.get_doc({
			"doctype": "WA Message",
			"jid": jid,
			"direction": direction,
			"message": message,
			"content_type": "text",
			"message_id": frappe.generate_hash(length=10),
			"sender_name": "Paging Sender",
			"sender_jid": jid,
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

	def _jids(self, **kwargs):
		result = get_wa_conversations(line=LINE, **kwargs)
		return [c["jid"] for c in result["conversations"]], result["has_more"]

	def test_pages_are_full_sized_and_ordered_by_recency(self):
		# Oldest first so that jid 0 is the least recent conversation.
		for i in range(5):
			self._msg(f"{i}pagingtest@s.whatsapp.net", minutes_ago=(5 - i) * 10)

		page1, has_more = self._jids(limit=2, offset=0)
		self.assertEqual(len(page1), 2)
		self.assertTrue(has_more)
		self.assertEqual(page1, ["4pagingtest@s.whatsapp.net", "3pagingtest@s.whatsapp.net"])

		page2, has_more = self._jids(limit=2, offset=2)
		self.assertEqual(page2, ["2pagingtest@s.whatsapp.net", "1pagingtest@s.whatsapp.net"])
		self.assertTrue(has_more)

		page3, has_more = self._jids(limit=2, offset=4)
		self.assertEqual(page3, ["0pagingtest@s.whatsapp.net"])
		self.assertFalse(has_more, "the last partial page must not claim more follow")

		# No overlap or gaps across pages.
		self.assertEqual(len(set(page1 + page2 + page3)), 5)

	def test_groups_filter_applies_before_the_page_is_cut(self):
		for i in range(3):
			self._msg(f"{i}pagingtest@s.whatsapp.net")
		for i in range(3):
			self._msg(f"{i}pagingtestgrp@g.us")

		jids, _ = self._jids(conv_filter="groups", limit=2)
		self.assertEqual(len(jids), 2, "a filtered page must still be full")
		self.assertTrue(all(j.endswith("@g.us") for j in jids))

	def test_unread_filter_is_per_agent_and_server_side(self):
		read_jid = "readpagingtest@s.whatsapp.net"
		unread_jid = "unreadpagingtest@s.whatsapp.net"
		self._msg(read_jid)
		self._msg(unread_jid)

		from helpdesk.integrations.wa import mark_wa_messages_read

		mark_wa_messages_read(jid=read_jid)

		jids, _ = self._jids(conv_filter="unread")
		self.assertIn(unread_jid, jids)
		self.assertNotIn(read_jid, jids)

	def test_favourites_filter_uses_the_jids_the_client_sends(self):
		# Favourites live in the agent's localStorage; the server has no record
		# of them, so an empty list can only mean "no favourites".
		for i in range(3):
			self._msg(f"{i}pagingtest@s.whatsapp.net")

		favourite = "1pagingtest@s.whatsapp.net"
		jids, _ = self._jids(conv_filter="favourites", favourite_jids=[favourite])
		self.assertEqual(jids, [favourite])

		# The frontend sends this as a JSON string over HTTP.
		jids, _ = self._jids(conv_filter="favourites", favourite_jids=f'["{favourite}"]')
		self.assertEqual(jids, [favourite])

		jids, has_more = self._jids(conv_filter="favourites", favourite_jids=[])
		self.assertEqual(jids, [])
		self.assertFalse(has_more)

	def test_search_matches_message_text_and_jid(self):
		self._msg("aaapagingtest@s.whatsapp.net", message="please send the invoice")
		self._msg("bbbpagingtest@s.whatsapp.net", message="unrelated chatter")

		jids, _ = self._jids(search="invoice")
		self.assertEqual(jids, ["aaapagingtest@s.whatsapp.net"])

		# Searching reaches back through history, not just the latest message —
		# the client-side filter this replaced could only see the preview.
		self._msg("aaapagingtest@s.whatsapp.net", message="thanks, got it")
		jids, _ = self._jids(search="invoice")
		self.assertEqual(jids, ["aaapagingtest@s.whatsapp.net"])

		jids, _ = self._jids(search="bbbpagingtest")
		self.assertEqual(jids, ["bbbpagingtest@s.whatsapp.net"])

		jids, has_more = self._jids(search="nothingmatchesthis")
		self.assertEqual(jids, [])
		self.assertFalse(has_more)

	def test_search_matches_contact_name(self):
		jid = "cccpagingtest@s.whatsapp.net"
		self._msg(jid, message="no keyword in here")
		frappe.get_doc({
			"doctype": "WA Contact",
			"jid": jid,
			"custom_name": "Zenobia Paging",
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.db.delete, "WA Contact", {"jid": jid})

		jids, _ = self._jids(search="Zenobia")
		self.assertEqual(jids, [jid])

	def test_limit_is_clamped(self):
		self._msg("0pagingtest@s.whatsapp.net")
		# A caller asking for everything must not be able to reinstate the
		# unbounded query this endpoint used to run.
		result = get_wa_conversations(line=LINE, limit=100000)
		self.assertLessEqual(len(result["conversations"]), 200)
		result = get_wa_conversations(line=LINE, limit=0)
		self.assertGreaterEqual(len(result["conversations"]), 1)
