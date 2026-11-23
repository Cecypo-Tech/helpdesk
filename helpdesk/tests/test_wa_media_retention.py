import json
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from helpdesk.integrations import wa, wa_media_retention

LINE = "wa-media-retention-test-line"
JID = "254799000222@s.whatsapp.net"


class TestWAMediaRetention(FrappeTestCase):
	"""Media older than the retention window is deleted from disk and the
	message remembers it had some; the base64 copy of the file never stays in
	the database."""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("WA Line", LINE):
			frappe.get_doc({"doctype": "WA Line", "instance_name": LINE}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "WA Line", LINE, ignore_permissions=True, force=True)
		self.addCleanup(frappe.db.delete, "WA Message", {"line": LINE})
		self._set_days(30)
		self.addCleanup(self._set_days, None)

	def _set_days(self, days):
		frappe.db.set_single_value("WhatsApp Helpdesk Settings", "media_retention_days", days)
		frappe.clear_document_cache("WhatsApp Helpdesk Settings", "WhatsApp Helpdesk Settings")

	def _file(self, attached_to=None):
		doc = frappe.get_doc({
			"doctype": "File",
			"file_name": f"wa_media_{frappe.generate_hash(length=8)}.txt",
			"content": frappe.generate_hash(length=40).encode(),
			"is_private": 0,
			**(attached_to or {}),
		}).insert(ignore_permissions=True)
		self.addCleanup(lambda n=doc.name: frappe.db.exists("File", n) and frappe.delete_doc("File", n, ignore_permissions=True, force=True))
		return doc

	def _msg(self, days_ago=0, media_url="", thumbnail_url="", raw=None, content_type="image"):
		doc = frappe.get_doc({
			"doctype": "WA Message", "jid": JID, "direction": "Incoming", "message": "",
			"content_type": content_type, "message_id": frappe.generate_hash(length=10),
			"sender_jid": JID, "status": "Delivered", "line": LINE,
			"media_url": media_url, "thumbnail_url": thumbnail_url,
			"raw_message": json.dumps(raw) if raw is not None else "",
		}).insert(ignore_permissions=True)
		if days_ago:
			frappe.db.set_value("WA Message", doc.name, "creation", add_to_date(now_datetime(), days=-days_ago), update_modified=False)
		return doc.name

	def _row(self, name):
		return frappe.db.get_value(
			"WA Message", name, ["media_url", "thumbnail_url", "raw_message", "media_purged"], as_dict=True
		)

	# ── ingest ────────────────────────────────────────────────────────────

	def test_stored_raw_message_drops_the_base64_copy_but_keeps_the_media_key(self):
		raw = {
			"base64": "QUFB" * 100,
			"imageMessage": {"mediaKey": "k", "url": "u", "base64": "QUFB", "jpegThumbnail": "x"},
			"messageContextInfo": {"a": 1},
		}
		stripped = wa._strip_media_thumbnails(raw)
		self.assertNotIn("base64", stripped)
		self.assertEqual(stripped["imageMessage"], {"mediaKey": "k", "url": "u"})
		self.assertEqual(stripped["messageContextInfo"], {"a": 1})

	# ── purge ─────────────────────────────────────────────────────────────

	def test_old_media_is_deleted_and_the_message_marked(self):
		media, thumb = self._file(), self._file()
		path = media.get_full_path()
		name = self._msg(days_ago=31, media_url=media.file_url, thumbnail_url=thumb.file_url, raw={"imageMessage": {"mediaKey": "k"}})
		wa_media_retention.purge_old_media()
		self.assertFalse(frappe.db.exists("File", media.name))
		self.assertFalse(frappe.db.exists("File", thumb.name))
		self.assertFalse(os.path.exists(path))
		self.assertEqual(dict(self._row(name)), {"media_url": "", "thumbnail_url": "", "raw_message": "", "media_purged": 1})

	def test_recent_media_is_kept(self):
		media = self._file()
		name = self._msg(days_ago=5, media_url=media.file_url)
		wa_media_retention.purge_old_media()
		self.assertTrue(frappe.db.exists("File", media.name))
		self.assertEqual(self._row(name).media_url, media.file_url)

	def test_a_file_a_recent_message_still_shows_is_kept(self):
		# Group messages are stored once per line, all pointing at one file.
		media = self._file()
		old = self._msg(days_ago=40, media_url=media.file_url)
		self._msg(days_ago=2, media_url=media.file_url)
		wa_media_retention.purge_old_media()
		self.assertTrue(frappe.db.exists("File", media.name))
		# Left unpurged, so the run after the recent one ages out deletes the file and marks it.
		self.assertEqual(self._row(old).media_url, media.file_url)
		self.assertEqual(self._row(old).media_purged, 0)

	def test_an_unrelated_upload_sharing_the_file_is_kept(self):
		# Frappe reuses one file for identical content, so a File Manager upload
		# can share the URL of a WhatsApp image.
		media = self._file()
		other = frappe.get_doc({
			"doctype": "File", "file_url": media.file_url, "file_name": "Screenshot (1).txt", "is_private": 0,
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.db.delete, "File", {"name": other.name})
		name = self._msg(days_ago=40, media_url=media.file_url)
		wa_media_retention.purge_old_media()
		self.assertTrue(frappe.db.exists("File", other.name))
		self.assertTrue(os.path.exists(media.get_full_path()))
		self.assertEqual(dict(self._row(name)), {"media_url": media.file_url, "thumbnail_url": "", "raw_message": "", "media_purged": 1})

	def test_a_file_that_fails_to_delete_does_not_stop_the_run(self):
		bad, good = self._file(), self._file()
		stuck = self._msg(days_ago=50, media_url=bad.file_url)
		done = self._msg(days_ago=45, media_url=good.file_url)
		real = wa_media_retention._delete_file
		frappe.db.commit()  # the failure path rolls back the open transaction, as it would mid-run
		self.addCleanup(frappe.db.delete, "Error Log", {"method": ["like", "%media retention%"]})

		def flaky(url, pending):
			ok = real(url, pending)
			if url == bad.file_url:
				raise frappe.QueryDeadlockError("deadlock")  # after its File row is gone
			return ok

		with patch.object(wa_media_retention, "_delete_file", side_effect=flaky):
			wa_media_retention.purge_old_media()
		self.assertEqual(self._row(stuck).media_purged, 0)
		# Undone for that message only: its File row is back and the disk file stays.
		self.assertTrue(frappe.db.exists("File", bad.name))
		self.assertTrue(os.path.exists(bad.get_full_path()))
		self.assertEqual(self._row(done).media_purged, 1)
		self.assertFalse(frappe.db.exists("File", good.name))
		self.assertFalse(os.path.exists(good.get_full_path()))

	def test_a_second_run_while_one_is_going_does_nothing(self):
		media = self._file()
		self._msg(days_ago=40, media_url=media.file_url)
		lock = frappe.cache.lock(frappe.cache.make_key(wa_media_retention.JOB_ID), timeout=60)
		self.assertTrue(lock.acquire(blocking=False))
		try:
			wa_media_retention.purge_old_media()
		finally:
			lock.release()
		self.assertTrue(frappe.db.exists("File", media.name))

	def test_a_file_also_attached_to_a_ticket_is_kept(self):
		media = self._file()
		self._file_for_ticket = frappe.get_doc({
			"doctype": "File", "file_url": media.file_url, "file_name": media.file_name,
			"attached_to_doctype": "HD Ticket", "attached_to_name": "1", "is_private": 0,
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.db.delete, "File", {"name": self._file_for_ticket.name})
		self._msg(days_ago=40, media_url=media.file_url)
		wa_media_retention.purge_old_media()
		self.assertTrue(frappe.db.exists("File", self._file_for_ticket.name))
		self.assertTrue(os.path.exists(media.get_full_path()))

	def test_zero_days_keeps_everything(self):
		self._set_days(0)
		media = self._file()
		self._msg(days_ago=400, media_url=media.file_url)
		wa_media_retention.purge_old_media()
		self.assertTrue(frappe.db.exists("File", media.name))

	def test_unset_means_thirty_days(self):
		self._set_days(None)
		self.assertEqual(wa_media_retention.retention_days(), 30)

	def test_recent_rows_lose_their_base64_copy(self):
		raw = {"base64": "QUFB" * 100, "imageMessage": {"mediaKey": "k", "base64": "QUFB"}}
		name = self._msg(days_ago=3, media_url="/files/x.jpg", raw=raw)
		wa_media_retention.purge_old_media()
		self.assertEqual(json.loads(self._row(name).raw_message), {"imageMessage": {"mediaKey": "k"}})


class TestWABAMediaRetention(FrappeTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("DocType", "WhatsApp Message"):
			self.skipTest("frappe_whatsapp is not installed")
		p = patch(
			"frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message.WhatsAppMessage.set_whatsapp_account"
		)
		p.start()
		self.addCleanup(p.stop)

	def test_old_waba_attachment_is_deleted(self):
		media = frappe.get_doc({
			"doctype": "File", "file_name": f"waba_{frappe.generate_hash(length=8)}.txt",
			"content": frappe.generate_hash(length=40).encode(), "is_private": 0,
		}).insert(ignore_permissions=True)
		doc = frappe.get_doc({
			"doctype": "WhatsApp Message", "type": "Incoming", "from": "254799000333", "to": "254700999888",
			"message": "", "content_type": "image", "attach": media.file_url,
			"message_id": f"wamid.ret.{frappe.generate_hash(length=10)}", "status": "received",
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "WhatsApp Message", doc.name, ignore_permissions=True, force=True)
		# frappe_whatsapp attaches the media File to its message.
		frappe.db.set_value("File", media.name, {"attached_to_doctype": "WhatsApp Message", "attached_to_name": doc.name})
		frappe.db.set_value("WhatsApp Message", doc.name, "creation", add_to_date(now_datetime(), days=-45), update_modified=False)
		wa_media_retention.purge_old_media()
		self.assertFalse(frappe.db.exists("File", media.name))
		self.assertEqual(frappe.db.get_value("WhatsApp Message", doc.name, "attach") or "", "")
