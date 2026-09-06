"""Work the Evolution webhook hands to jobs, and which queue it uses.

The webhook is what Evolution blocks on, and on Frappe Cloud the `short`
workers are the ones inbound WhatsApp Business ingestion depends on. Anything
that calls out to Evolution or rewrites rows in bulk belongs elsewhere.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from helpdesk.integrations import wa

JID = "999linejobs@s.whatsapp.net"
LINE = "wa-line-jobs-test-line"
SETTINGS = "WA API Settings"


class TestWALineJobs(FrappeTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("WA Line", LINE):
			frappe.get_doc({"doctype": "WA Line", "instance_name": LINE}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "WA Line", LINE, ignore_permissions=True, force=True)
		self.line = frappe.get_doc("WA Line", LINE)
		before = frappe.db.get_single_value(SETTINGS, "enabled")
		self.addCleanup(self._set_enabled, before)
		self._set_enabled(1)
		self.addCleanup(frappe.db.delete, "WA Message", {"jid": JID})
		self.addCleanup(frappe.db.delete, "WA Contact", {"jid": JID})
		self.addCleanup(frappe.db.delete, "HD Notification", {"reference_wa_jid": JID})

	@staticmethod
	def _set_enabled(value):
		frappe.db.set_single_value(SETTINGS, "enabled", value)
		frappe.clear_cache(doctype=SETTINGS)

	def test_media_retry_runs_off_the_short_queue(self):
		data = {
			"key": {"remoteJid": JID, "fromMe": False, "id": f"wamid.media.{frappe.generate_hash(length=8)}"},
			"pushName": "Line Jobs",
			"message": {
				"imageMessage": {
					"mimetype": "image/jpeg",
					"url": "https://mmg.whatsapp.net/x.enc",
					"mediaKey": "k",
				}
			},
		}
		with patch("frappe.enqueue") as enqueue, patch.object(wa, "_notify_agents"), patch.object(
			wa, "_publish_wa_event"
		):
			result = wa._handle_upsert(data, self.line, wa._settings())

		self.assertEqual(result.get("status"), "ok")
		retries = [
			c for c in enqueue.call_args_list
			if c.args and c.args[0] == "helpdesk.integrations.wa._retry_media_download"
		]
		self.assertEqual(len(retries), 1)
		self.assertEqual(retries[0].kwargs.get("queue"), "default")

	def _post(self, payload: dict):
		key = wa._settings().global_api_key or ""
		env = EnvironBuilder(
			method="POST", data=json.dumps(payload), content_type="application/json",
			headers={"apikey": key},
		).get_environ()
		frappe.local.request = Request(env)
		try:
			return wa.webhook()
		finally:
			frappe.local.request = None

	def test_contacts_upsert_is_queued_not_processed_inline(self):
		contacts = [{"id": JID, "notify": "Line Jobs"}]
		with patch("frappe.enqueue") as enqueue, patch.object(wa, "_handle_contacts_upsert") as inline:
			result = self._post({"event": "contacts.upsert", "instance": LINE, "data": contacts})

		inline.assert_not_called()
		self.assertEqual(result.get("status"), "queued")
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.args[0], "helpdesk.integrations.wa._handle_contacts_upsert")
		self.assertEqual(enqueue.call_args.kwargs.get("queue"), "long")
		self.assertEqual(enqueue.call_args.kwargs.get("contacts"), contacts)
