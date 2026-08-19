"""Unknown-contact verification: parsing, matching, state machine, approval.

The rule the whole feature hangs off: a KRA PIN is a claim, not a credential.
It is printed on every invoice and ETR receipt, and 30 customers in the source
ERPNext share one with a namesake. So a match never links anyone automatically.
"""

import unittest

import frappe

from helpdesk import verification

PREFIX = "_test-verif-"


class TestNormalisePin(unittest.TestCase):
	def test_uppercases_and_strips_punctuation(self):
		self.assertEqual(verification.normalise_pin(" p051234567 x "), "P051234567X")

	def test_none_and_empty_are_empty_string(self):
		self.assertEqual(verification.normalise_pin(None), "")
		self.assertEqual(verification.normalise_pin("   "), "")


class TestExtractClaim(unittest.TestCase):
	def test_canonical_pin(self):
		self.assertEqual(verification.extract_claim("P051234567X")["tax_id"], "P051234567X")

	def test_pin_embedded_in_a_sentence(self):
		claim = verification.extract_claim("Hi, we are Blue Lake Ltd, PIN P051234567X thanks")
		self.assertEqual(claim["tax_id"], "P051234567X")
		self.assertIn("Blue Lake", claim["company"])

	def test_accepts_the_malformed_shapes_that_exist_in_real_data(self):
		"""Nine of 2,803 mirrored tax_ids are malformed. A strict PIN regex
		would tell those customers their own PIN is invalid."""
		for raw in ("P05122111X", "P0511478994", "A0011327804", "P05110212C"):
			self.assertEqual(verification.extract_claim(raw)["tax_id"], raw, raw)

	def test_a_phone_number_is_not_a_pin(self):
		self.assertIsNone(verification.extract_claim("254712345678")["tax_id"])

	def test_short_alphanumerics_are_not_pins(self):
		"""'A1 Supermarket' must not read as a PIN."""
		self.assertIsNone(verification.extract_claim("A1 Supermarket")["tax_id"])

	def test_empty_input_is_safe(self):
		self.assertEqual(verification.extract_claim(None), {"tax_id": None, "company": None})


class TestMatchClaim(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.cleanup()
		frappe.get_doc({
			"doctype": "HD Customer",
			"customer_name": PREFIX + "match-co",
			"tax_id": "P051234567X",
		}).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(self.cleanup)

	def cleanup(self):
		for name in frappe.get_all(
			"HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
		):
			frappe.delete_doc("HD Customer", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_exact_match(self):
		self.assertEqual(verification.match_claim("P051234567X"), [PREFIX + "match-co"])

	def test_matching_ignores_case_and_punctuation(self):
		self.assertEqual(verification.match_claim(" p051234567-x "), [PREFIX + "match-co"])

	def test_no_match_returns_empty_not_an_error(self):
		self.assertEqual(verification.match_claim("P999999999Z"), [])

	def test_empty_claim_matches_nothing(self):
		"""Must not return every customer whose tax_id is blank."""
		self.assertEqual(verification.match_claim(""), [])
		self.assertEqual(verification.match_claim(None), [])
