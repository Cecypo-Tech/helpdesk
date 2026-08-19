"""Run WhatsApp inbound ingestion off the webhook request.

Meta retries any webhook delivery it does not get a timely 200 for, and
sustained retries get an endpoint deprioritised — on 2026-08-12 the same status
wamid arrived ten times in three minutes. Contact resolution, ticket creation,
agent notifications and the bot all used to run inside that request, which is a
lot to hold it open for.

on_whatsapp_message_insert now stores the row and enqueues; everything else
happens here.

The trade this makes: an inbound message can now exist with no ticket, if the
job fails. sweep_unlinked_messages exists to close that window.
"""

import frappe

# Old enough that a job which is merely slow or queued is not treated as failed.
SWEEP_MIN_AGE_MINUTES = 5
# Past this, a message is history rather than a backlog, and re-running
# ingestion over it would raise tickets for conversations long since handled.
SWEEP_MAX_AGE_DAYS = 7
SWEEP_BATCH = 50


def process_incoming_message(message_name: str) -> None:
	"""Resolve the contact, ticket and bot reply for one inbound message.

	Idempotent, and it has to be: RQ retries failed jobs, and
	sweep_unlinked_messages may enqueue a message whose original job is merely
	slow. Both would otherwise raise a second ticket for one message.
	"""
	if not frappe.db.exists("WhatsApp Message", message_name):
		# Deleted between enqueue and run — most likely the duplicate guard.
		return

	doc = frappe.get_doc("WhatsApp Message", message_name)
	if doc.type != "Incoming":
		return
	if doc.reference_doctype == "HD Ticket" and doc.reference_name:
		# Already linked, by an earlier run of this job or by an agent.
		return

	from helpdesk.integrations import bot, wa, wa_verification

	wa.link_incoming_message(doc)

	# Re-read: link_incoming_message writes the reference with db_set, and the
	# bot's own guard requires it. Skipping the bot when linking did not happen
	# is deliberate — a reply that quotes no ticket is worse than no reply.
	doc.reload()
	if not (doc.reference_doctype == "HD Ticket" and doc.reference_name):
		return

	# The ticket exists and the message points at it; that is the part that
	# matters and it is already committed. An automated reply failing must not
	# undo it — on 2026-08-13 a single unguarded notification (an acknowledgement
	# email with no outgoing account configured) threw from a ticket's
	# after_insert and took the whole insert with it, which is how new
	# conversations stopped becoming tickets at all.
	try:
		bot.handle_whatsapp_message(doc)
	except Exception:
		frappe.log_error(
			title="WhatsApp bot dispatch failed",
			message=f"Message {doc.name} is linked to ticket {doc.reference_name}; the bot did not run.",
		)

	# Same discipline as the bot dispatch above: the ticket is already committed
	# and an automated question must not be able to undo it. handle_incoming
	# swallows its own errors too; this is belt and braces.
	try:
		wa_verification.handle_incoming(doc)
	except Exception:
		frappe.log_error(
			title="WhatsApp verification dispatch failed",
			message=f"Message {doc.name} is linked to ticket {doc.reference_name}.",
		)


def sweep_unlinked_messages() -> dict:
	"""Re-enqueue inbound messages that never got a ticket.

	Ingestion moving off the request created a state the inline version could
	not produce: a stored message with no ticket, invisible to agents, because
	its job died. Nothing else would ever notice.
	"""
	rows = frappe.db.sql(
		"""
		SELECT `name` FROM `tabWhatsApp Message`
		WHERE `type` = 'Incoming'
		  AND IFNULL(`reference_name`, '') = ''
		  AND `creation` < NOW() - INTERVAL %(min_age)s MINUTE
		  AND `creation` > NOW() - INTERVAL %(max_age)s DAY
		ORDER BY `creation`
		LIMIT %(batch)s
		""",
		{
			"min_age": SWEEP_MIN_AGE_MINUTES,
			"max_age": SWEEP_MAX_AGE_DAYS,
			"batch": SWEEP_BATCH,
		},
		as_dict=True,
	)
	for row in rows:
		frappe.enqueue(
			"helpdesk.integrations.wa_ingest.process_incoming_message",
			queue="short",
			job_id=f"wa_ingest_{row.name}",
			message_name=row.name,
		)

	if rows:
		frappe.log_error(
			title="WhatsApp ingestion swept unlinked messages",
			message=f"Re-enqueued {len(rows)}: {', '.join(r.name for r in rows)}",
		)
	return {"swept": len(rows)}
