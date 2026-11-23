import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.helpdesk.doctype.whatsapp_helpdesk_settings.whatsapp_helpdesk_settings import fill_unset_defaults

DT = "WhatsApp Helpdesk Settings"


class TestWASettingsDefaults(FrappeTestCase):
	"""A settings field added after the Single was first saved has no stored
	value; the form shows it as 0 and the next save stores that 0."""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		# Raw rows: get_single_value reads an unset or NULL number as 0.
		saved = frappe.db.sql("SELECT * FROM `tabSingles` WHERE doctype = %s", DT, as_dict=True)
		self.addCleanup(frappe.clear_document_cache, DT, DT)
		self.addCleanup(self._restore, saved)

	def _restore(self, rows):
		frappe.db.delete("Singles", {"doctype": DT})
		for r in rows:
			frappe.db.sql("INSERT INTO `tabSingles` (doctype, field, value) VALUES (%s, %s, %s)", (r.doctype, r.field, r.value))

	def _unset(self, field):
		frappe.db.delete("Singles", {"doctype": DT, "field": field})
		frappe.clear_document_cache(DT, DT)

	def test_unset_field_gets_its_default_and_survives_a_save(self):
		self._unset("unread_window_days")
		fill_unset_defaults()
		frappe.get_doc(DT).save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_single_value(DT, "unread_window_days"), 30)

	def test_a_null_number_gets_its_default(self):
		frappe.db.set_single_value(DT, "media_retention_days", None)
		fill_unset_defaults()
		self.assertEqual(frappe.db.get_single_value(DT, "media_retention_days"), 30)

	def test_an_explicit_zero_is_kept(self):
		frappe.db.set_single_value(DT, "media_retention_days", 0)
		fill_unset_defaults()
		self.assertEqual(frappe.db.get_single_value(DT, "media_retention_days"), 0)

	def test_runs_after_migrate(self):
		self.assertIn(
			"helpdesk.helpdesk.doctype.whatsapp_helpdesk_settings.whatsapp_helpdesk_settings.fill_unset_defaults",
			frappe.get_hooks("after_migrate"),
		)
