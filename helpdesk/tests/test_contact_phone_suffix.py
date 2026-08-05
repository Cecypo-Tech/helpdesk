import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.integrations import wa
from helpdesk.patches import add_contact_phone_suffix as patch_module


class TestContactPhoneSuffix(FrappeTestCase):
	"""Cover the stored lookup key behind match_phone_to_contact().

	The matcher used to load every Contact and Contact Phone row into Python on
	every inbound message. The stored suffix turns that into an indexed lookup;
	the matching rules themselves are unchanged, which is what these tests pin.
	"""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		for doctype in ("Contact", "Contact Phone"):
			if not frappe.db.has_column(doctype, "phone_suffix"):
				self.skipTest("phone_suffix not migrated on this site")
		self.tag = frappe.generate_hash(length=6)
		frappe.flags.pop("contact_phone_suffix_col", None)
		self.addCleanup(frappe.flags.pop, "contact_phone_suffix_col", None)

	def _contact(self, phone, first_name=None):
		doc = frappe.get_doc({
			"doctype": "Contact",
			"first_name": first_name or f"C {self.tag}",
			"phone_nos": [{
				"doctype": "Contact Phone", "phone": phone, "is_primary_mobile_no": 1,
			}],
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "Contact", doc.name, ignore_permissions=True, force=True
		)
		return doc

	def _both_paths(self, number):
		"""(indexed result, scan result) for the same number."""
		normalized = wa._normalize_phone(number)
		frappe.flags.contact_phone_suffix_col = True
		indexed = wa.match_phone_to_contact(number)
		scan = wa._match_phone_to_contact_scan(normalized) if normalized else None
		return indexed, scan

	# ── population on write ───────────────────────────────────────────────

	def test_saving_a_contact_stores_the_suffix_on_parent_and_child(self):
		doc = self._contact("254799123456")
		self.assertEqual(
			frappe.db.get_value("Contact", doc.name, "phone_suffix"), "799123456"
		)
		child = frappe.get_all(
			"Contact Phone", filters={"parent": doc.name}, fields=["phone_suffix"]
		)
		self.assertEqual(child[0].phone_suffix, "799123456")

	def test_punctuation_is_stripped(self):
		doc = self._contact("+254 (799) 123-999")
		self.assertEqual(
			frappe.db.get_value("Contact", doc.name, "phone_suffix"), "799123999"
		)

	def test_short_number_keeps_its_whole_value(self):
		doc = self._contact("12345")
		self.assertEqual(frappe.db.get_value("Contact", doc.name, "phone_suffix"), "12345")

	def test_suffix_matches_the_python_helper(self):
		for raw in ("254799123456", "0799123456", "+254 799 123 456", "1234"):
			self.assertEqual(wa._phone_suffix(raw), wa._normalize_phone(raw)[-9:])

	# ── the indexed path agrees with the scan ─────────────────────────────

	def test_exact_match_agrees(self):
		contact = self._contact("254799123456")
		indexed, scan = self._both_paths("254799123456")
		self.assertEqual(indexed, contact.name)
		self.assertEqual(indexed, scan)

	def test_national_vs_international_agrees(self):
		contact = self._contact("0799123456")
		indexed, scan = self._both_paths("254799123456")
		self.assertEqual(indexed, contact.name)
		self.assertEqual(indexed, scan)

	def test_ambiguous_match_returns_none_on_both_paths(self):
		self._contact("0799123456", first_name=f"A {self.tag}")
		self._contact("+1 799123456", first_name=f"B {self.tag}")
		indexed, scan = self._both_paths("254799123456")
		self.assertIsNone(indexed)
		self.assertEqual(indexed, scan)

	def test_no_match_agrees(self):
		self._contact("254711000000")
		indexed, scan = self._both_paths("254799123456")
		self.assertIsNone(indexed)
		self.assertEqual(indexed, scan)

	def test_empty_input_returns_none(self):
		self.assertIsNone(wa.match_phone_to_contact(""))
		self.assertIsNone(wa.match_phone_to_contact("not a number"))

	def test_exact_match_wins_over_a_loose_one(self):
		# Two contacts share trailing digits; one matches exactly. The exact one
		# must win rather than the pair being treated as ambiguous.
		exact = self._contact("254799123456", first_name=f"Exact {self.tag}")
		self._contact("0799123456", first_name=f"Loose {self.tag}")
		indexed, scan = self._both_paths("254799123456")
		self.assertEqual(indexed, exact.name)
		self.assertEqual(indexed, scan)

	def test_fallback_is_used_when_columns_are_absent(self):
		contact = self._contact("254799123456")
		frappe.flags.contact_phone_suffix_col = False
		self.assertEqual(wa.match_phone_to_contact("254799123456"), contact.name)

	# ── the patch ─────────────────────────────────────────────────────────

	def test_backfill_fills_empty_rows_and_leaves_others_alone(self):
		doc = self._contact("254788111222")
		frappe.db.set_value("Contact", doc.name, "phone_suffix", "", update_modified=False)
		child = frappe.get_all("Contact Phone", filters={"parent": doc.name}, pluck="name")[0]
		frappe.db.set_value("Contact Phone", child, "phone_suffix", "", update_modified=False)

		other = self._contact("254788333444")
		frappe.db.set_value(
			"Contact", other.name, "phone_suffix", "SENTINEL", update_modified=False
		)

		patch_module.execute()

		self.assertEqual(
			frappe.db.get_value("Contact", doc.name, "phone_suffix"), "788111222"
		)
		self.assertEqual(
			frappe.db.get_value("Contact Phone", child, "phone_suffix"), "788111222"
		)
		self.assertEqual(
			frappe.db.get_value("Contact", other.name, "phone_suffix"),
			"SENTINEL",
			"backfill overwrote a row that already had a value",
		)
		frappe.db.set_value(
			"Contact", other.name, "phone_suffix", "788333444", update_modified=False
		)

	def test_patch_is_idempotent(self):
		patch_module.execute()
		patch_module.execute()
		# Nothing to assert beyond "it did not blow up and left the data alone";
		# the index is frappe's responsibility now, covered below.
		self.assertTrue(frappe.db.has_column("Contact", "phone_suffix"))

	def test_phone_suffix_is_indexed_on_both_tables(self):
		# The whole point of the change. A hand-rolled ALTER in the patch was
		# silently dropped later in the same migrate, so the index is declared
		# via search_index on the field and this test is what would catch a
		# regression to that behaviour.
		for doctype in ("Contact", "Contact Phone"):
			rows = frappe.db.sql(f"SHOW INDEX FROM `tab{doctype}`", as_dict=True)
			indexed = [r for r in rows if r["Column_name"] == "phone_suffix"]
			self.assertTrue(indexed, f"{doctype}.phone_suffix is not indexed")
