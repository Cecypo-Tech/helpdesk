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


from unittest.mock import patch

from helpdesk.integrations import wa_verification


def _settings(enabled=1, reask=7):
	return frappe._dict(
		verification_enabled=enabled,
		verification_prompt="What is your company name and KRA PIN?",
		verification_reask_days=reask,
	)


class _StateBase(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.cleanup()
		self.contact = frappe.get_doc({
			"doctype": "Contact",
			"first_name": PREFIX + "caller",
			"phone_nos": [{"phone": "254700111222", "is_primary_mobile_no": 1}],
		}).insert(ignore_permissions=True).name
		self.ticket = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": PREFIX + "ticket",
			"contact": self.contact,
		}).insert(ignore_permissions=True).name
		frappe.db.commit()

		self.sent = []
		p = patch.object(
			wa_verification, "send_prompt",
			side_effect=lambda ticket, text: self.sent.append((ticket, text)),
		)
		p.start()
		self.addCleanup(p.stop)
		p2 = patch.object(wa_verification, "settings", return_value=_settings())
		p2.start()
		self.addCleanup(p2.stop)
		self.addCleanup(self.cleanup)

	def cleanup(self):
		for t in frappe.get_all(
			"HD Ticket", filters={"subject": ["like", PREFIX + "%"]}, pluck="name"
		):
			frappe.delete_doc("HD Ticket", t, force=True, ignore_permissions=True,
			                  delete_permanently=True)
		for c in frappe.get_all(
			"Contact", filters={"first_name": ["like", PREFIX + "%"]}, pluck="name"
		):
			frappe.delete_doc("Contact", c, force=True, ignore_permissions=True,
			                  delete_permanently=True)
		for c in frappe.get_all(
			"HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
		):
			frappe.delete_doc("HD Customer", c, force=True, ignore_permissions=True)
		frappe.db.commit()

	def msg(self, text):
		return frappe._dict(
			name=PREFIX + "msg", type="Incoming", message=text,
			reference_doctype="HD Ticket", reference_name=self.ticket,
		)

	def status(self):
		return frappe.db.get_value("Contact", self.contact, "hd_verification_status")


class TestAsking(_StateBase):
	def test_an_unverified_contact_is_asked_once(self):
		wa_verification.handle_incoming(self.msg("my printer is broken"))
		self.assertEqual(len(self.sent), 1)
		self.assertEqual(self.status(), "Asked")

	def test_a_second_message_inside_the_window_does_not_reask(self):
		wa_verification.handle_incoming(self.msg("my printer is broken"))
		wa_verification.handle_incoming(self.msg("are you there?"))
		self.assertEqual(len(self.sent), 1, "must not nag on every message")

	def test_it_asks_again_after_the_reask_window(self):
		wa_verification.handle_incoming(self.msg("hello"))
		frappe.db.set_value(
			"Contact", self.contact, "hd_verification_asked_on",
			frappe.utils.add_days(frappe.utils.now_datetime(), -8),
		)
		wa_verification.handle_incoming(self.msg("hello again"))
		self.assertEqual(len(self.sent), 2)

	def test_a_contact_already_linked_to_a_customer_is_never_asked(self):
		customer = frappe.get_doc({
			"doctype": "HD Customer", "customer_name": PREFIX + "known",
		}).insert(ignore_permissions=True).name
		c = frappe.get_doc("Contact", self.contact)
		c.append("links", {"link_doctype": "HD Customer", "link_name": customer})
		c.save(ignore_permissions=True)
		frappe.db.commit()

		wa_verification.handle_incoming(self.msg("hello"))
		self.assertEqual(self.sent, [])

	def test_disabled_setting_does_nothing_at_all(self):
		with patch.object(wa_verification, "settings", return_value=_settings(enabled=0)):
			wa_verification.handle_incoming(self.msg("hello"))
		self.assertEqual(self.sent, [])
		self.assertIn(self.status(), (None, "Unverified"))


class TestRecordingTheClaim(_StateBase):
	def test_a_pin_in_the_reply_is_stored_and_status_moves_to_claimed(self):
		wa_verification.handle_incoming(self.msg("hello"))
		wa_verification.handle_incoming(self.msg("Blue Lake Ltd P051234567X"))

		row = frappe.db.get_value(
			"Contact", self.contact,
			["hd_verification_status", "hd_claimed_tax_id", "hd_claimed_company"],
			as_dict=True,
		)
		self.assertEqual(row.hd_verification_status, "Claimed")
		self.assertEqual(row.hd_claimed_tax_id, "P051234567X")
		self.assertIn("Blue Lake", row.hd_claimed_company)

	def test_a_reply_with_no_pin_leaves_the_state_alone(self):
		wa_verification.handle_incoming(self.msg("hello"))
		wa_verification.handle_incoming(self.msg("sorry what do you mean?"))
		self.assertEqual(self.status(), "Asked")
		self.assertEqual(len(self.sent), 1)

	def test_a_verified_contact_is_never_relinked_by_a_later_pin(self):
		frappe.db.set_value("Contact", self.contact, "hd_verification_status", "Verified")
		wa_verification.handle_incoming(self.msg("actually our PIN is P099999999Z"))
		self.assertEqual(
			frappe.db.get_value("Contact", self.contact, "hd_claimed_tax_id"), None
		)

	def test_a_rejected_contact_is_never_asked_again(self):
		frappe.db.set_value("Contact", self.contact, "hd_verification_status", "Rejected")
		wa_verification.handle_incoming(self.msg("hello"))
		self.assertEqual(self.sent, [])


class TestFailOpen(_StateBase):
	def test_a_failing_send_does_not_raise(self):
		with patch.object(wa_verification, "send_prompt", side_effect=Exception("boom")):
			wa_verification.handle_incoming(self.msg("hello"))
		self.assertTrue(frappe.db.exists("HD Ticket", self.ticket))
