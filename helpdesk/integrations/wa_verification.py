"""Ask an unrecognised WhatsApp number who it is, and record what it answers.

Deterministic on purpose — no LLM. The bot answers the customer's question;
this asks one housekeeping question alongside it, at most once a week, and
records the reply as a claim for an agent to approve.

Nothing here links anybody. See the spec: a KRA PIN is printed on every invoice
and ETR receipt, and a WhatsApp number can be a shared office handset.
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

	# CLAIMED is included here, not just ASKED: a later PIN from a contact an
	# agent has not yet approved is treated as the customer correcting a typo,
	# and the agent must see the newest claim rather than the first one. This
	# is intentional overwrite-on-correction, not a missed status guard.
	if status in (ASKED, CLAIMED):
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

	send_prompt(ticket, s.get("verification_prompt"))
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
