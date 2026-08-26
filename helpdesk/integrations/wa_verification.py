"""Ask an unrecognised WhatsApp number who it is, and record what it answers.

Deterministic on purpose — no LLM. The bot answers the customer's question;
this asks one housekeeping question alongside it, at most once a week, and
records the reply as a claim for an agent to approve.

Nothing here links anybody. See the spec: a KRA PIN is printed on every invoice
and ETR receipt, and a WhatsApp number can be a shared office handset.

WABA ONLY, deliberately. `wa_ingest.process_incoming_message` is the single
dispatch site, and `WA Message` (the WA Line / Evolution path) is intentionally
NOT wired to it: WA Line conversations do not ask for a PIN. This reads like a
gap -- the two channels are otherwise handled symmetrically -- and it was once
"fixed" by wiring WA Line up. It is not a gap. `test_wa_line_is_deliberately_not_verified`
guards it.
"""

import frappe

from helpdesk import verification
from helpdesk.utils import get_customer

DOCTYPE = "WhatsApp Helpdesk Settings"
STATUS_FIELD = "hd_verification_status"

UNVERIFIED = "Unverified"
ASKED = "Asked"
CLAIMED = "Claimed"
VERIFIED = "Verified"
REJECTED = "Rejected"

# An agent's decision, either way, is final as far as automation is concerned.
TERMINAL = (VERIFIED, REJECTED)


def settings():
	return frappe.get_cached_doc(DOCTYPE)


def send_prompt(ticket: str, text: str) -> None:
	"""Free-form WABA reply. Safe without a template: the customer's inbound
	message just opened the 24-hour window, so it is open by construction.

	`system=True` because this is not an agent reply. Without it _send_fw_reply
	would assign the ticket to whoever's session sent it and move it into the
	configured agent-reply status — so ticking verification_enabled would quietly
	pull every brand-new unknown-number ticket out of the agents' Open queue.
	"""
	from helpdesk.integrations.wa import _send_fw_reply

	_send_fw_reply(ticket, text, system=True)


def contact_state(contact: str) -> dict:
	"""Verification fields for a contact, or empty on an unmigrated site."""
	try:
		return frappe.db.get_value(
			"Contact", contact,
			[STATUS_FIELD, "hd_claimed_tax_id", "hd_claimed_company",
			 "hd_verification_asked_on"],
			as_dict=True,
		) or {}
	except Exception as e:
		# Custom Fields absent — same hazard as baileys_jid on an unmigrated site.
		# Anything else is a real failure and must not be mistaken for that.
		if frappe.db.is_missing_column(e):
			return {}
		raise


def handle_incoming(doc) -> None:
	"""Advance the verification state for one inbound WABA message.

	Never raises. Ticket creation has already happened and committed by the time
	this runs; an automated question failing must not undo it.
	"""
	try:
		_handle(doc)
	except Exception:
		frappe.log_error(
			title="WhatsApp verification failed",
			message=f"Message {doc.get('name')} on ticket {doc.get('reference_name')}",
		)


def _handle(doc) -> None:
	if doc.get("type") != "Incoming":
		return
	if doc.get("reference_doctype") != "HD Ticket" or not doc.get("reference_name"):
		return

	s = settings()
	if not s.get("verification_enabled"):
		return

	ticket = doc.get("reference_name")
	contact = frappe.db.get_value("HD Ticket", ticket, "contact")
	if not contact:
		return

	if get_customer(contact):
		# Already linked to a customer; there is nothing to ask.
		return

	state = contact_state(contact)
	if not state:
		return

	status = state.get(STATUS_FIELD) or UNVERIFIED
	if status in TERMINAL:
		return

	# Read a PIN out of ANY non-terminal message, not only one that follows an
	# ask. Gating this on ASKED/CLAIMED silently discarded a PIN in two ordinary
	# situations, both of which end with the customer having volunteered their
	# PIN and nothing being recorded:
	#
	#   1. It arrives in the customer's FIRST message ("hi, my PIN is P0512...,
	#      printer broken"). The contact is still Unverified, so the PIN was
	#      dropped and the bot asked for the very thing it had just been given.
	#   2. The prompt send failed — Meta rejecting it outside the 24-hour window
	#      is enough. The contact deliberately stays Unverified so the ask
	#      retries, but that also meant every later PIN was dropped and the
	#      prompt re-sent, forever.
	#
	# Recording without an ask is safe because a claim is not authorisation: it
	# is evidence an agent approves. TERMINAL is checked above, so an agent's
	# decision is still final either way.
	#
	# CLAIMED is included: a later PIN from a contact an agent has not yet
	# approved is the customer correcting a typo, and the agent must see the
	# newest claim rather than the first. Intentional overwrite-on-correction.
	claim = verification.extract_claim(doc.get("message"))
	if claim["tax_id"]:
		frappe.db.set_value("Contact", contact, {
			"hd_claimed_tax_id": claim["tax_id"],
			"hd_claimed_company": claim["company"],
			STATUS_FIELD: CLAIMED,
		})
		frappe.db.commit()
		return

	if status == CLAIMED:
		# Waiting on an agent. Do not keep asking.
		return

	if status == ASKED and not _reask_due(state, s):
		return

	prompt = (s.get("verification_prompt") or "").strip()
	if not prompt:
		# An operator who clears the free-text field would otherwise have `None`
		# formatted into the outgoing body — the customer receives the literal
		# "None" plus the bot suffix, and the contact is marked Asked so it is
		# never retried. Bail before *both* side effects: leaving the contact in
		# its prior state means filling the prompt back in resumes normally on
		# the next inbound message.
		frappe.log_error(
			title="WhatsApp verification prompt is blank",
			message=(
				f"verification_enabled is on but verification_prompt is empty; "
				f"ticket {ticket} / contact {contact} was not prompted."
			),
		)
		return

	send_prompt(ticket, prompt)
	frappe.db.set_value("Contact", contact, {
		STATUS_FIELD: ASKED,
		"hd_verification_asked_on": frappe.utils.now_datetime(),
	})
	frappe.db.commit()


def _reask_due(state: dict, s) -> bool:
	asked_on = state.get("hd_verification_asked_on")
	if not asked_on:
		return True
	days = int(s.get("verification_reask_days") or 7)
	return frappe.utils.date_diff(frappe.utils.now_datetime(), asked_on) >= days
