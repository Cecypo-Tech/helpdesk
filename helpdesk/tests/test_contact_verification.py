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

	def test_several_customers_can_share_one_tax_id(self):
		"""tax_id being 1:1 today is a property of the mirror's name-folding, not
		a guarantee: 30 customers in the source ERPNext share one with a namesake.
		The many-match path exists for the day that shows up here, so it is
		tested rather than merely built."""
		frappe.get_doc({
			"doctype": "HD Customer",
			"customer_name": PREFIX + "twin-a",
			"tax_id": "P099888777Q",
		}).insert(ignore_permissions=True)
		frappe.get_doc({
			"doctype": "HD Customer",
			"customer_name": PREFIX + "twin-b",
			"tax_id": "P099888777Q",
		}).insert(ignore_permissions=True)
		frappe.db.commit()

		self.assertEqual(
			sorted(verification.match_claim("p099888777q")),
			[PREFIX + "twin-a", PREFIX + "twin-b"],
		)


from unittest.mock import patch

from helpdesk.integrations import wa_verification


def _settings(enabled=1, reask=7, prompt="What is your company name and KRA PIN?"):
	return frappe._dict(
		verification_enabled=enabled,
		verification_prompt=prompt,
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

	def test_a_second_different_pin_before_approval_overwrites_the_claim(self):
		"""A still-unapproved contact correcting a typo, not a bug: the agent
		must see the newest claim, so CLAIMED stays in the guard alongside ASKED."""
		wa_verification.handle_incoming(self.msg("hello"))
		wa_verification.handle_incoming(self.msg("Blue Lake Ltd P051234567X"))
		wa_verification.handle_incoming(self.msg("sorry typo, it is P099999999Z"))

		row = frappe.db.get_value(
			"Contact", self.contact,
			["hd_verification_status", "hd_claimed_tax_id"],
			as_dict=True,
		)
		self.assertEqual(row.hd_verification_status, "Claimed")
		self.assertEqual(row.hd_claimed_tax_id, "P099999999Z")

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


class TestPromptIsNotAnAgentReply(unittest.TestCase):
	def test_send_prompt_marks_the_send_as_system(self):
		"""_send_fw_reply is not pure transport: by default it assigns the ticket
		and moves it into agent_reply_status. Ticking verification_enabled must
		not start pulling brand-new tickets out of the agents' Open queue."""
		with patch("helpdesk.integrations.wa._send_fw_reply") as send:
			wa_verification.send_prompt("SOME-TICKET", "who are you?")

		self.assertTrue(send.called)
		self.assertIs(send.call_args.kwargs.get("system"), True)


class TestBlankPrompt(_StateBase):
	"""An operator clearing the free-text prompt must not send the word "None".

	`s.get("verification_prompt")` passed straight through reaches the customer
	as the literal "None" plus the bot suffix — and stamping Asked on the way out
	would mean it is never retried.
	"""

	def _blank(self, prompt):
		return patch.object(
			wa_verification, "settings", return_value=_settings(prompt=prompt)
		)

	def test_an_empty_prompt_sends_nothing(self):
		for prompt in (None, "", "   "):
			with self.subTest(prompt=prompt), self._blank(prompt):
				wa_verification.handle_incoming(self.msg("hello"))
			self.assertEqual(self.sent, [], f"sent something for {prompt!r}")

	def test_an_empty_prompt_leaves_the_contact_in_its_prior_state(self):
		"""Bail before *both* side effects: marking Asked without asking would
		strand the contact forever, and it must resume once the field is set."""
		with self._blank(""):
			wa_verification.handle_incoming(self.msg("hello"))

		self.assertIn(self.status(), (None, "Unverified"))
		self.assertIsNone(
			frappe.db.get_value("Contact", self.contact, "hd_verification_asked_on")
		)

		# Prompt restored: the very next message asks, no manual repair needed.
		wa_verification.handle_incoming(self.msg("still here?"))
		self.assertEqual(len(self.sent), 1)
		self.assertEqual(self.status(), "Asked")


from helpdesk.api import verification as verification_api
from helpdesk.utils import get_customer


class TestApprovalApi(_StateBase):
	def setUp(self):
		super().setUp()
		self.customer = frappe.get_doc({
			"doctype": "HD Customer",
			"customer_name": PREFIX + "approve-co",
			"tax_id": "P051234567X",
		}).insert(ignore_permissions=True).name
		frappe.db.set_value("Contact", self.contact, {
			"hd_verification_status": "Claimed",
			"hd_claimed_tax_id": "P051234567X",
			"hd_claimed_company": "Blue Lake Ltd",
		})
		frappe.db.commit()

	def test_the_claim_payload_carries_the_matches(self):
		payload = verification_api.get_contact_claim(self.ticket)
		self.assertEqual(payload["status"], "Claimed")
		self.assertEqual([m["name"] for m in payload["matches"]], [self.customer])

	def test_the_claim_payload_carries_every_match_when_a_pin_is_shared(self):
		"""Two customers on one tax_id must both reach the agent. Collapsing to
		the first would hand them somebody else's account on a coin flip."""
		twin = frappe.get_doc({
			"doctype": "HD Customer",
			"customer_name": PREFIX + "approve-twin",
			"tax_id": "P051234567X",
		}).insert(ignore_permissions=True).name
		frappe.db.commit()

		payload = verification_api.get_contact_claim(self.ticket)
		self.assertEqual(sorted(m["name"] for m in payload["matches"]),
		                 sorted([self.customer, twin]))
		self.assertTrue(all(m["customer_name"] for m in payload["matches"]))
		# Still nobody linked: several matches is information, not a decision.
		self.assertEqual(get_customer(self.contact), [])

	def test_a_single_exact_match_does_not_auto_link(self):
		"""The load-bearing rule. A PIN is printed on every invoice; matching one
		is evidence for an agent, never authorisation."""
		verification_api.get_contact_claim(self.ticket)
		self.assertEqual(get_customer(self.contact), [])
		self.assertEqual(self.status(), "Claimed")

	def test_approve_creates_the_link_that_get_customer_reads(self):
		verification_api.approve_contact_link(self.contact, self.customer)
		self.assertEqual(get_customer(self.contact), [self.customer])
		self.assertEqual(self.status(), "Verified")

	def test_approving_twice_does_not_duplicate_the_link(self):
		verification_api.approve_contact_link(self.contact, self.customer)
		verification_api.approve_contact_link(self.contact, self.customer)
		self.assertEqual(get_customer(self.contact), [self.customer])

	def test_approve_backfills_the_customer_on_the_open_ticket(self):
		"""HD Ticket.customer is only written by set_customer() on save, and the
		entitlement/standing endpoints read the ticket, not the contact. Without
		the backfill the agent clicks Link and the coverage panel stays empty."""
		self.assertFalse(frappe.db.get_value("HD Ticket", self.ticket, "customer"))

		verification_api.approve_contact_link(self.contact, self.customer)

		self.assertEqual(
			frappe.db.get_value("HD Ticket", self.ticket, "customer"), self.customer
		)

	def test_approve_does_not_overwrite_a_ticket_that_already_has_a_customer(self):
		"""Somebody attributed that ticket deliberately; a later link decision
		about the number must not silently re-file their work."""
		other = frappe.get_doc({
			"doctype": "HD Customer", "customer_name": PREFIX + "already-set",
		}).insert(ignore_permissions=True).name
		frappe.db.set_value("HD Ticket", self.ticket, "customer", other)
		frappe.db.commit()

		verification_api.approve_contact_link(self.contact, self.customer)

		self.assertEqual(
			frappe.db.get_value("HD Ticket", self.ticket, "customer"), other
		)

	def test_reject_marks_it_and_creates_no_link(self):
		verification_api.reject_contact_claim(self.contact)
		self.assertEqual(self.status(), "Rejected")
		self.assertEqual(get_customer(self.contact), [])

	def test_endpoints_refuse_a_non_agent(self):
		"""Whitelisting alone bypasses permissions; these read and write customer
		linkage, so they need the agent guard."""
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		for call in (
			lambda: verification_api.get_contact_claim(self.ticket),
			lambda: verification_api.approve_contact_link(self.contact, self.customer),
			lambda: verification_api.reject_contact_claim(self.contact),
		):
			with self.assertRaises(frappe.PermissionError):
				call()


class TestPinOnlyPromptPatch(unittest.TestCase):
	"""The prompt asks for the PIN alone, and the patch respects rewording.

	The company name was never matched on — match_claim compares tax_id only —
	so asking for it gave the customer a second thing to get wrong for nothing.
	"""

	DOCTYPE = "WhatsApp Helpdesk Settings"
	FIELD = "verification_prompt"

	def setUp(self):
		frappe.set_user("Administrator")
		self.saved = frappe.db.get_value(
			"Singles", {"doctype": self.DOCTYPE, "field": self.FIELD}, "value", order_by=None
		)
		self.addCleanup(self.restore)

	def restore(self):
		if self.saved is None:
			frappe.db.delete("Singles", {"doctype": self.DOCTYPE, "field": self.FIELD})
		else:
			frappe.db.set_single_value(self.DOCTYPE, self.FIELD, self.saved)
		frappe.db.commit()

	def _run(self):
		from helpdesk.patches.pin_only_verification_prompt import execute

		execute()
		frappe.db.commit()
		return frappe.db.get_value(
			"Singles", {"doctype": self.DOCTYPE, "field": self.FIELD}, "value", order_by=None
		)

	def test_the_old_default_is_replaced(self):
		from helpdesk.patches import pin_only_verification_prompt as patch_mod

		frappe.db.set_single_value(self.DOCTYPE, self.FIELD, patch_mod.PREVIOUS_DEFAULT)
		frappe.db.commit()

		self.assertEqual(self._run(), patch_mod.NEW_DEFAULT)

	def test_a_reworded_prompt_is_left_alone(self):
		"""An operator's wording is theirs. A patch that overwrites it once will
		overwrite it again on the next migrate."""
		mine = "Karibu! Tuma PIN yako ya KRA tafadhali."
		frappe.db.set_single_value(self.DOCTYPE, self.FIELD, mine)
		frappe.db.commit()

		self.assertEqual(self._run(), mine)

	def test_an_unwritten_field_is_left_for_the_install_patch(self):
		frappe.db.delete("Singles", {"doctype": self.DOCTYPE, "field": self.FIELD})
		frappe.db.commit()

		self.assertIsNone(self._run())

	def test_the_new_default_does_not_mention_a_company_name(self):
		from helpdesk.patches import pin_only_verification_prompt as patch_mod

		self.assertNotIn("company name", patch_mod.NEW_DEFAULT.lower())
		self.assertIn("kra pin", patch_mod.NEW_DEFAULT.lower())


class TestScopeIsWabaOnly(unittest.TestCase):
	"""Verification is WABA-only on purpose.

	The two WhatsApp channels are handled symmetrically nearly everywhere else,
	so a `WA Message` entry pointing at wa_verification reads like an obvious
	missing line. It is not: WA Line conversations are not asked for a KRA PIN.
	This was once added as a "fix" and reverted. Assert the absence so the next
	attempt fails loudly instead of shipping.
	"""

	def test_wa_line_is_deliberately_not_verified(self):
		from helpdesk import hooks

		handlers = hooks.doc_events["WA Message"]["after_insert"]
		if isinstance(handlers, str):
			handlers = [handlers]

		self.assertNotIn(
			"helpdesk.integrations.wa_verification.handle_wa_message_insert",
			handlers,
			"WA Line must not dispatch PIN verification -- it is WABA-only by design.",
		)
		for handler in handlers:
			self.assertNotIn(
				"wa_verification", handler,
				f"{handler} wires verification into the WA Line path; it is WABA-only.",
			)

	def test_the_bot_still_runs_on_wa_line(self):
		"""The revert must not have taken the bot with it."""
		from helpdesk import hooks

		handlers = hooks.doc_events["WA Message"]["after_insert"]
		if isinstance(handlers, str):
			handlers = [handlers]
		self.assertIn("helpdesk.integrations.bot.handle_wa_message", handlers)

	def test_the_prompt_still_goes_out_over_waba(self):
		"""And that the WABA path itself is untouched by the revert."""
		with patch("helpdesk.integrations.wa._send_fw_reply") as send:
			wa_verification.send_prompt("SOME-TICKET", "who are you?")

		self.assertTrue(send.called)
		self.assertIs(send.call_args.kwargs.get("system"), True)


class TestUnpromptedPin(_StateBase):
	"""A PIN the customer volunteered without being asked.

	Reproduced from a live report: contact unlinked, PIN shared, nothing
	recorded. The claim used to be read only when the contact was already
	ASKED/CLAIMED, so a PIN arriving in any other state was silently dropped --
	no error, no log, nothing for an agent to see.
	"""

	def test_a_pin_in_the_very_first_message_is_recorded(self):
		"""The customer leads with it. Dropping the PIN here meant the bot asked
		for exactly what it had just been given."""
		wa_verification.handle_incoming(
			self.msg("Hi, my PIN is P051234567X, printer broken")
		)

		self.assertEqual(self.status(), "Claimed")
		self.assertEqual(
			frappe.db.get_value("Contact", self.contact, "hd_claimed_tax_id"),
			"P051234567X",
		)

	def test_no_prompt_is_sent_when_the_pin_is_already_there(self):
		"""Asking anyway would be the rudest possible version of this feature."""
		wa_verification.handle_incoming(self.msg("my PIN is P051234567X"))
		self.assertEqual(self.sent, [])

	def test_a_pin_is_recorded_after_the_prompt_failed_to_send(self):
		"""A failed send leaves the contact Unverified on purpose, so the ask
		retries. That also used to mean every later PIN was dropped and the
		prompt re-sent, forever."""
		with patch.object(
			wa_verification, "send_prompt", side_effect=Exception("outside 24h window")
		):
			wa_verification.handle_incoming(self.msg("printer broken"))
		self.assertIn(self.status(), (None, "", "Unverified"))

		wa_verification.handle_incoming(self.msg("P051234567X"))

		self.assertEqual(self.status(), "Claimed")
		self.assertEqual(
			frappe.db.get_value("Contact", self.contact, "hd_claimed_tax_id"),
			"P051234567X",
		)

	def test_a_message_with_no_pin_still_triggers_the_ask(self):
		"""The ordinary path must be untouched."""
		wa_verification.handle_incoming(self.msg("my printer is broken"))
		self.assertEqual(len(self.sent), 1)
		self.assertEqual(self.status(), "Asked")

	def test_a_verified_contact_volunteering_a_pin_is_still_ignored(self):
		"""TERMINAL is checked before the claim is read. An agent's decision stays
		final -- this must not become a way to relink a settled contact."""
		frappe.db.set_value("Contact", self.contact, "hd_verification_status", "Verified")
		frappe.db.commit()

		wa_verification.handle_incoming(self.msg("actually my PIN is P999999999Z"))

		self.assertEqual(self.status(), "Verified")
		self.assertIsNone(
			frappe.db.get_value("Contact", self.contact, "hd_claimed_tax_id")
		)

	def test_a_rejected_contact_volunteering_a_pin_is_still_ignored(self):
		frappe.db.set_value("Contact", self.contact, "hd_verification_status", "Rejected")
		frappe.db.commit()

		wa_verification.handle_incoming(self.msg("my PIN is P999999999Z"))

		self.assertEqual(self.status(), "Rejected")
		self.assertEqual(self.sent, [])


class TestPendingClaimsQueue(_StateBase):
	"""The queue exists because the approve controls live on a ticket's Contact
	tab, so a claim was only discoverable by opening the one ticket it came from.
	Thirteen claims sat unread on production for two days.

	It lists; it does not decide. Linking still happens only through
	approve_contact_link, on the ticket, next to the conversation.
	"""

	def setUp(self):
		super().setUp()
		from helpdesk.api import verification as api
		self.api = api

	def test_a_claimed_contact_appears(self):
		wa_verification.handle_incoming(self.msg("my PIN is P051234567X"))
		self.assertEqual(self.status(), "Claimed")

		rows = self.api.get_pending_claims()
		mine = [r for r in rows if r["contact"] == self.contact]
		self.assertEqual(len(mine), 1)
		self.assertEqual(mine[0]["claimed_tax_id"], "P051234567X")

	def test_the_row_carries_a_ticket_to_open(self):
		"""Without it the row is a dead end -- the agent is told someone is
		waiting and given nowhere to act."""
		wa_verification.handle_incoming(self.msg("my PIN is P051234567X"))
		row = [r for r in self.api.get_pending_claims() if r["contact"] == self.contact][0]
		self.assertEqual(str(row["ticket"]), str(self.ticket))

	def test_an_asked_contact_is_not_in_the_queue(self):
		"""Asked means waiting on the CUSTOMER. Only a claim is waiting on an agent."""
		wa_verification.handle_incoming(self.msg("my printer is broken"))
		self.assertEqual(self.status(), "Asked")
		self.assertNotIn(self.contact, [r["contact"] for r in self.api.get_pending_claims()])

	def test_settled_contacts_leave_the_queue(self):
		for settled in ("Verified", "Rejected"):
			frappe.db.set_value("Contact", self.contact, "hd_verification_status", settled)
			frappe.db.commit()
			self.assertNotIn(
				self.contact, [r["contact"] for r in self.api.get_pending_claims()],
				f"a {settled} contact is still queued",
			)

	def test_count_matches_the_list(self):
		"""The badge and the page are separate endpoints -- the cheap one must not
		drift from the one that does the work."""
		wa_verification.handle_incoming(self.msg("my PIN is P051234567X"))
		self.assertEqual(
			self.api.get_pending_claim_count(),
			len(self.api.get_pending_claims()),
		)

	def test_the_queue_is_agent_only(self):
		"""A claim names an unrecognised caller and the company they say they are."""
		self.assertTrue(hasattr(self.api.get_pending_claims, "__wrapped__"))
		self.assertTrue(hasattr(self.api.get_pending_claim_count, "__wrapped__"))
