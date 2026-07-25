from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase


class TestWAWebhookPerf(FrappeTestCase):
	"""Covers the webhook changes made to cut per-request cost.

	Both behaviours here are easy to regress into their slow forms without any
	visible symptom — a per-item query loop and a blocking media download both
	still produce correct data, just slowly — so they are pinned by assertions
	on the query/call counts rather than only on the resulting rows.
	"""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		self.line_name = "wa-webhook-perf-line"
		if not frappe.db.exists("WA Line", self.line_name):
			frappe.get_doc(
				{"doctype": "WA Line", "instance_name": self.line_name}
			).insert(ignore_permissions=True)
			self.addCleanup(
				frappe.delete_doc, "WA Line", self.line_name, ignore_permissions=True, force=True
			)
		self.line = frappe.get_doc("WA Line", self.line_name)

	def _make_outgoing(self, message_id):
		doc = frappe.get_doc({
			"doctype": "WA Message",
			"jid": "777webhookperf@s.whatsapp.net",
			"direction": "Outgoing",
			"message": "sent",
			"content_type": "text",
			"message_id": message_id,
			"status": "Sent",
			"line": self.line_name,
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "WA Message", doc.name, ignore_permissions=True, force=True)
		return doc

	def test_handle_update_resolves_a_batch_in_one_query(self):
		from helpdesk.integrations.wa import _handle_update

		ids = [f"wa-perf-msg-{i}" for i in range(3)]
		docs = [self._make_outgoing(mid) for mid in ids]

		updates = [
			{"key": {"id": ids[0], "remoteJid": "x@s.whatsapp.net"}, "update": {"status": 3}},
			{"key": {"id": ids[1], "remoteJid": "x@s.whatsapp.net"}, "update": {"status": 4}},
			{"key": {"id": ids[2], "remoteJid": "x@s.whatsapp.net"}, "update": {"status": 0}},
			# Unknown id and unmappable status must be skipped, not queried for.
			{"key": {"id": "wa-perf-msg-missing"}, "update": {"status": 3}},
			{"key": {"id": ids[0]}, "update": {}},
		]

		selects = []
		real_sql = frappe.db.sql

		def counting_sql(query, *args, **kwargs):
			if "tabWA Message" in str(query) and str(query).strip().upper().startswith("SELECT"):
				selects.append(str(query))
			return real_sql(query, *args, **kwargs)

		with patch.object(frappe.db, "sql", side_effect=counting_sql):
			_handle_update(updates, self.line)

		self.assertEqual(
			len(selects), 1,
			f"batch must be resolved by a single lookup, saw {len(selects)}:\n{selects}",
		)

		for doc, expected in zip(docs, ["Delivered", "Read", "Failed"]):
			self.assertEqual(
				frappe.db.get_value("WA Message", doc.name, "status"), expected
			)

	def test_webhook_media_extraction_does_not_call_evolution(self):
		"""The decrypt round-trip belongs to _retry_media_download, not the request.

		Evolution blocks on our webhook response, so the download was the slowest
		thing in it. Leaving media_url empty is what enqueues the background job,
		which means the CDN-URL fallback must also stay out of the way — writing
		it would make the message look downloaded and the job would never run.
		"""
		from helpdesk.integrations import wa

		raw_msg = {
			"imageMessage": {
				"mimetype": "image/jpeg",
				"url": "https://mmg.whatsapp.net/encrypted.enc",
			}
		}

		with patch.object(wa, "_download_media_via_wa") as download:
			url = wa._extract_media_url(
				raw_msg,
				line=self.line,
				full_webhook_data={"key": {"id": "x"}, "message": raw_msg},
				allow_remote_fetch=False,
			)

		download.assert_not_called()
		self.assertEqual(url, "", "an empty media_url is what triggers the retry job")

		# The same call with fetching allowed still reaches Evolution.
		with patch.object(wa, "_download_media_via_wa", return_value="/files/x.jpg") as download:
			url = wa._extract_media_url(
				raw_msg,
				line=self.line,
				full_webhook_data={"key": {"id": "x"}, "message": raw_msg},
			)
		download.assert_called_once()
		self.assertEqual(url, "/files/x.jpg")

	def test_inline_base64_media_is_still_saved_in_the_webhook(self):
		"""Deferring the download must not defer the path that costs nothing."""
		from helpdesk.integrations import wa

		raw_msg = {"imageMessage": {"mimetype": "image/jpeg", "base64": "Zm9v"}}

		with patch.object(wa, "_save_base64_media", return_value="/files/inline.jpg") as save:
			url = wa._extract_media_url(
				raw_msg,
				line=self.line,
				full_webhook_data={"key": {"id": "x"}, "message": raw_msg},
				allow_remote_fetch=False,
			)

		save.assert_called_once()
		self.assertEqual(url, "/files/inline.jpg")
