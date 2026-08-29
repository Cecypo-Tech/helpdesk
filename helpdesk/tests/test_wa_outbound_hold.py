"""The outbound hold setting that batches an agent's chunked WABA replies.

Meta bills every business message sent inside the 24-hour service window from
1 Oct 2026, so a reply split across four bubbles is billed four times. The
composer holds an outgoing message for `outbound_hold_seconds` and merges
whatever the agent types during that window into a single send.

The value reaches the composer through get_whatsapp_ticket_info(), and the
accessor carries the same unset-means-default trap as unread_window_days and
notification_quiet_minutes: Frappe only applies a field default to *new*
documents, so an existing singleton keeps no value for a newly added field.
Reading unset as 0 would silently disable the feature on every site that has
not opened the settings page — which is the whole point of the tests below.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.integrations.wa import (
	DEFAULT_OUTBOUND_HOLD_SECONDS,
	_outbound_hold_seconds,
	get_whatsapp_ticket_info,
)

_SETTINGS = "WhatsApp Helpdesk Settings"


class TestWAOutboundHold(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# Tests here run against the live site singleton, so the original value
		# is restored before anything else this class registers.
		self.addCleanup(self._restore_hold, self._raw_hold())
		self.suffix = frappe.generate_hash(length=6)

	@staticmethod
	def _raw_hold():
		"""The stored value, or None when the field has no row at all.

		Read straight from `tabSingles` rather than through
		frappe.db.get_single_value, which casts by fieldtype and so reports a
		missing Int as 0 — exactly the distinction these tests turn on. Reading
		through it would make an unconfigured site indistinguishable from one
		that has deliberately switched the hold off.
		"""
		rows = frappe.db.sql(
			"SELECT value FROM tabSingles WHERE doctype=%s AND field=%s",
			(_SETTINGS, "outbound_hold_seconds"),
		)
		return rows[0][0] if rows else None

	@staticmethod
	def _clear_hold():
		"""Remove the row entirely, reproducing a site that never set the field."""
		frappe.db.delete("Singles", {"doctype": _SETTINGS, "field": "outbound_hold_seconds"})
		frappe.clear_document_cache(_SETTINGS, _SETTINGS)

	@staticmethod
	def _set_hold(seconds):
		frappe.db.set_single_value(_SETTINGS, "outbound_hold_seconds", seconds)
		# _shared_settings() reads through get_cached_doc.
		frappe.clear_document_cache(_SETTINGS, _SETTINGS)

	@classmethod
	def _restore_hold(cls, original):
		if original is None:
			cls._clear_hold()
		else:
			cls._set_hold(original)

	def test_unset_falls_back_to_the_default(self):
		"""A singleton predating the field must still hold, not send instantly."""
		self._clear_hold()
		self.assertIsNone(self._raw_hold())
		self.assertEqual(_outbound_hold_seconds(), DEFAULT_OUTBOUND_HOLD_SECONDS)

	def test_explicit_zero_disables_the_hold(self):
		"""0 is the kill switch — it must survive the unset-means-default rule."""
		self._set_hold(0)
		self.assertEqual(_outbound_hold_seconds(), 0)

	def test_configured_value_is_used(self):
		self._set_hold(12)
		self.assertEqual(_outbound_hold_seconds(), 12)

	def test_negative_values_are_clamped_off(self):
		"""A negative hold is meaningless; it must not become a negative delay."""
		self._set_hold(-5)
		self.assertEqual(_outbound_hold_seconds(), 0)

	def test_waba_ticket_info_carries_the_hold(self):
		"""The composer only learns the hold through this endpoint."""
		self._set_hold(7)
		ticket = self._make_waba_ticket()

		info = get_whatsapp_ticket_info(ticket.name)

		self.assertTrue(info["has_whatsapp"])
		self.assertTrue(info["via_frappe_whatsapp"])
		self.assertEqual(info["outbound_hold_seconds"], 7)

	def test_wa_line_ticket_info_omits_the_hold(self):
		"""WA Line runs through Evolution, which Meta does not bill.

		Returning a hold here would delay replies on a channel that costs
		nothing to send on, so the Evolution branch must stay silent about it
		and let the composer's own default of 0 apply.
		"""
		self._set_hold(7)
		ticket = self._make_wa_line_ticket()

		info = get_whatsapp_ticket_info(ticket.name)

		self.assertTrue(info["has_whatsapp"])
		self.assertNotIn("outbound_hold_seconds", info)

	# ── fixtures ─────────────────────────────────────────────────────────────
	def _make_ticket(self):
		ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": f"outbound-hold-test-{self.suffix}",
				"description": "outbound hold fixture",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", ticket.name, force=True, ignore_permissions=True
		)
		return ticket

	def _make_waba_ticket(self):
		"""A ticket the WABA branch recognises: no baileys_jid, one linked message."""
		if not frappe.db.exists("DocType", "WhatsApp Message"):
			self.skipTest("frappe_whatsapp is not installed on this site")
		ticket = self._make_ticket()
		msg = frappe.get_doc(
			{
				"doctype": "WhatsApp Message",
				"type": "Incoming",
				"from": f"254700{self.suffix[:6]}",
				"message": "hello",
				"content_type": "text",
				"reference_doctype": "HD Ticket",
				"reference_name": ticket.name,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "WhatsApp Message", msg.name, force=True, ignore_permissions=True
		)
		return ticket

	def _make_wa_line_ticket(self):
		"""A ticket the Evolution branch recognises: baileys_jid is set."""
		ticket = self._make_ticket()
		try:
			frappe.db.set_value(
				"HD Ticket", ticket.name, "baileys_jid", f"254711{self.suffix[:6]}@s.whatsapp.net"
			)
		except Exception:
			self.skipTest("baileys_jid custom field is not present on this site")
		return ticket
