from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.integrations import wa
from helpdesk.patches import add_wa_message_normalized_phone as patch_module


class TestWANormalizedPhone(FrappeTestCase):
	"""Cover the stored conversation key on WhatsApp Message.

	The key used to be derived per query with REGEXP_REPLACE, which no index can
	serve, so every conversation page load scanned the whole table.
	"""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("DocType", "WhatsApp Message"):
			self.skipTest("frappe_whatsapp is not installed")
		if not frappe.db.has_column("WhatsApp Message", "normalized_phone"):
			self.skipTest("normalized_phone not migrated on this site")
		self.tag = frappe.generate_hash(length=6)
		# _waba_phone_sql caches the column check per request.
		frappe.flags.pop("wa_normalized_phone_col", None)
		self.addCleanup(frappe.flags.pop, "wa_normalized_phone_col", None)

	def _message(self, direction="Incoming", sender="254700111222", recipient="254700999888"):
		doc = frappe.get_doc({
			"doctype": "WhatsApp Message",
			"type": direction,
			"from": sender,
			"to": recipient,
			"message": f"m {self.tag}",
			"content_type": "text",
			"message_id": f"wamid.norm.{frappe.generate_hash(length=10)}",
			"status": "received" if direction == "Incoming" else "sent",
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "WhatsApp Message", doc.name, ignore_permissions=True, force=True
		)
		return doc

	# ── population on write ───────────────────────────────────────────────

	def test_incoming_stores_the_senders_digits(self):
		doc = self._message(direction="Incoming", sender="254700111222")
		self.assertEqual(
			frappe.db.get_value("WhatsApp Message", doc.name, "normalized_phone"),
			"254700111222",
		)

	def test_outgoing_stores_the_recipients_digits(self):
		doc = self._message(direction="Outgoing", recipient="254700999888")
		self.assertEqual(
			frappe.db.get_value("WhatsApp Message", doc.name, "normalized_phone"),
			"254700999888",
		)

	def test_punctuation_is_stripped_to_match_normalize_phone(self):
		raw = "+254 (700) 111-333"
		doc = self._message(direction="Incoming", sender=raw)
		stored = frappe.db.get_value("WhatsApp Message", doc.name, "normalized_phone")
		self.assertEqual(stored, wa._normalize_phone(raw))
		self.assertEqual(stored, "254700111333")

	# ── the switch is behaviour-preserving ────────────────────────────────

	def test_column_and_fallback_return_identical_conversations(self):
		# The test that matters: the stored column must produce exactly what the
		# inline expression did, or the optimisation silently changes the page.
		for i in range(4):
			self._message(direction="Incoming", sender=f"2547001234{i:02d}")
			self._message(direction="Outgoing", recipient=f"2547005678{i:02d}")

		frappe.flags.pop("wa_normalized_phone_col", None)
		with patch.object(wa, "_wa_has_normalized_phone", return_value=True):
			with_column = wa.get_whatsapp_conversations(limit=200)
		with patch.object(wa, "_wa_has_normalized_phone", return_value=False):
			with_fallback = wa.get_whatsapp_conversations(limit=200)

		self.assertEqual(with_column, with_fallback)
		self.assertTrue(with_column["conversations"], "seeded data produced no rows")

	def test_filters_agree_between_column_and_fallback(self):
		self._message(direction="Incoming", sender="254700222111")
		self._message(direction="Outgoing", recipient="254700333111")

		for conv_filter in ("all", "awaiting", "open"):
			with patch.object(wa, "_wa_has_normalized_phone", return_value=True):
				a = wa.get_whatsapp_conversations(conv_filter=conv_filter, limit=200)
			with patch.object(wa, "_wa_has_normalized_phone", return_value=False):
				b = wa.get_whatsapp_conversations(conv_filter=conv_filter, limit=200)
			self.assertEqual(a, b, f"{conv_filter} filter diverged")

	# ── the patch ─────────────────────────────────────────────────────────

	def test_backfill_fills_empty_rows_and_leaves_others_alone(self):
		doc = self._message(direction="Incoming", sender="254700444555")
		# Simulate a row written before the column existed.
		frappe.db.set_value(
			"WhatsApp Message", doc.name, "normalized_phone", "", update_modified=False
		)
		untouched = self._message(direction="Incoming", sender="254700666777")
		frappe.db.set_value(
			"WhatsApp Message", untouched.name, "normalized_phone", "SENTINEL",
			update_modified=False,
		)

		patch_module.execute()

		self.assertEqual(
			frappe.db.get_value("WhatsApp Message", doc.name, "normalized_phone"),
			"254700444555",
		)
		self.assertEqual(
			frappe.db.get_value("WhatsApp Message", untouched.name, "normalized_phone"),
			"SENTINEL",
			"backfill overwrote a row that already had a value",
		)
		# Restore so the sentinel doesn't outlive the test if delete_doc fails.
		frappe.db.set_value(
			"WhatsApp Message", untouched.name, "normalized_phone", "254700666777",
			update_modified=False,
		)

	def test_patch_is_idempotent(self):
		patch_module.execute()
		patch_module.execute()
		indexes = frappe.db.sql(
			"SHOW INDEX FROM `tabWhatsApp Message` WHERE Key_name = %s",
			patch_module.INDEX_NAME,
			as_dict=True,
		)
		# One composite index over two columns reports one row per column.
		self.assertEqual(len(indexes), 2)
		self.assertEqual(
			[i["Column_name"] for i in indexes], ["normalized_phone", "creation"]
		)

	def test_index_exists_after_the_patch(self):
		patch_module.execute()
		indexes = frappe.db.sql(
			"SHOW INDEX FROM `tabWhatsApp Message` WHERE Key_name = %s",
			patch_module.INDEX_NAME,
			as_dict=True,
		)
		self.assertTrue(indexes, "index missing after patch")
