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
	"""Free-form reply on whichever channel owns this ticket.

	`send_wa_reply` already does the routing: a ticket with a `baileys_jid` goes
	out over its WA Line, and one without falls through to `_send_fw_reply` for
	WABA. Calling `_send_fw_reply` directly, as this used to, meant the prompt
	only ever existed on the WABA path — a WA Line customer was never asked.

	Safe without a template on WABA: the customer's inbound message just opened
	the 24-hour window, so it is open by construction. WA Line has no window.

	`system=True` because this is not an agent reply. Without it the send would
	assign the ticket to whoever's session sent it and move it into the
	configured agent-reply status — so ticking verification_enabled would quietly
	pull every brand-new unknown-number ticket out of the agents' Open queue.
	`send_wa_reply` forwards the flag down both branches.
	"""
	from helpdesk.integrations.wa import send_wa_reply

	send_wa_reply(ticket=ticket, message=text, system=True)


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


def _is_incoming(doc) -> bool:
	"""True for an inbound message on either channel.

	The two doctypes disagree on the field name: `WhatsApp Message` (WABA) calls
	it `type`, `WA Message` (WA Line) calls it `direction`. Both use the literal
	"Incoming", and only one of the two fields is ever set.
	"""
	return (doc.get("type") or doc.get("direction")) == "Incoming"


def _handle(doc) -> None:
	if not _is_incoming(doc):
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


def handle_wa_message_insert(doc, method=None) -> None:
	"""after_insert hook for WA Message — the WA Line (Evolution API) path.

	Enqueued rather than run inline. `_handle` commits, and this fires inside the
	request handling the Evolution webhook, where committing early would land
	that whole request's transaction. `bot.handle_wa_message` defers for the same
	reason.

	The WABA equivalent needs no hook: `wa_ingest.process_incoming_message` is
	already a background job and calls `handle_incoming` directly. WA Line
	messages arrive with `reference_name` already set at insert, so there is
	nothing to wait for beyond the commit.
	"""
	if doc.direction != "Incoming":
		return
	if doc.reference_doctype != "HD Ticket" or not doc.reference_name:
		return

	frappe.enqueue(
		"helpdesk.integrations.wa_verification.process_wa_message",
		queue="short",
		job_id=f"wa_verify_{doc.name}",
		enqueue_after_commit=True,
		message_name=doc.name,
	)


def process_wa_message(message_name: str) -> None:
	"""Background entry point for the hook above."""
	if not frappe.db.exists("WA Message", message_name):
		# Deleted between enqueue and run.
		return
	handle_incoming(frappe.get_doc("WA Message", message_name))
