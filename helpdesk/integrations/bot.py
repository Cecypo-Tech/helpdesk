# helpdesk/integrations/bot.py
import frappe

try:
	from helpdesk.integrations.wa import send_wa_reply
except Exception:
	send_wa_reply = None  # type: ignore[assignment]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _bot_settings():
	return frappe.get_cached_doc("Helpdesk Bot Settings")


def _is_short_message(text: str | None, min_words: int) -> bool:
	"""Return True when text has fewer words than min_words."""
	return len((text or "").split()) < min_words


def _search_kb(query: str, limit: int) -> list[dict]:
	"""Full-text search against published HD Article records.

	Returns a list of dicts with keys: name, title, content.
	"""
	if not query:
		return []
	like = f"%{query}%"
	return frappe.db.sql(
		"""
		SELECT name, title, content
		FROM `tabHD Article`
		WHERE status = 'Published'
		  AND (title LIKE %(like)s OR content LIKE %(like)s)
		LIMIT %(limit)s
		""",
		{"like": like, "limit": limit},
		as_dict=True,
	)


def _record_gap(
	ticket_name: str,
	channel: str,
	query_text: str,
	suggested_title: str,
	suggested_category: str,
) -> None:
	"""Insert an HD Bot Missing KB Query record. Swallows exceptions."""
	try:
		frappe.get_doc(
			{
				"doctype": "HD Bot Missing KB Query",
				"ticket": ticket_name,
				"channel": channel,
				"query_text": query_text,
				"suggested_title": suggested_title,
				"suggested_category": suggested_category,
				"status": "Pending",
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: _record_gap failed")


# ── Conversation history ───────────────────────────────────────────────────────


def _get_conversation_history(ticket_name: str, channel: str) -> list[dict]:
	"""Return last 10 messages for the ticket as role/content dicts, oldest first."""
	if channel == "waba":
		rows = frappe.db.get_all(
			"WhatsApp Message",
			filters={"reference_doctype": "HD Ticket", "reference_name": ticket_name},
			fields=["type", "message", "creation"],
			order_by="creation desc",
			limit=10,
		)
		rows = list(reversed(rows))
		return [
			{"role": "user" if r.type == "Incoming" else "assistant", "content": r.message or ""}
			for r in rows
		]
	# wa_line
	rows = frappe.db.get_all(
		"WA Message",
		filters={"reference_doctype": "HD Ticket", "reference_name": ticket_name},
		fields=["direction", "message", "creation"],
		order_by="creation desc",
		limit=10,
	)
	rows = list(reversed(rows))
	return [
		{"role": "user" if r.direction == "Incoming" else "assistant", "content": r.message or ""}
		for r in rows
	]


# ── Image download ─────────────────────────────────────────────────────────────


def _download_image_waba(attach_url: str) -> bytes | None:
	try:
		from frappe.utils.file_manager import get_file

		_, content = get_file(attach_url)
		return content
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: WABA image download failed")
		return None


def _download_image_wa_line(media_url: str, line_name: str) -> bytes | None:
	try:
		from helpdesk.integrations.wa import _evo_session, _headers, _line

		line_doc = _line(line_name)
		resp = _evo_session.get(media_url, headers=_headers(line_doc), timeout=15)
		resp.raise_for_status()
		return resp.content
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: WA Line image download failed")
		return None


# ── Escalation ────────────────────────────────────────────────────────────────


def _escalate(ticket_name: str) -> None:
	"""Optionally send an escalation message then mark the ticket as escalated."""
	settings = _bot_settings()
	if settings.escalation_message_enabled and settings.escalation_message:
		try:
			if send_wa_reply:
				send_wa_reply(ticket=ticket_name, message=settings.escalation_message)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: escalation message failed")

	frappe.db.set_value(
		"HD Ticket",
		ticket_name,
		{"bot_escalated": 1, "bot_active": 0},
		update_modified=False,
	)


# ── Background job ────────────────────────────────────────────────────────────


def process_message(msg_name: str, channel: str) -> None:
	"""Background job: process one incoming message and send a bot reply.

	channel: "waba" or "wa_line"
	"""
	settings = _bot_settings()
	if not settings.is_enabled:
		return

	line_name = None

	# Load message and extract fields
	if channel == "waba":
		msg = frappe.get_doc("WhatsApp Message", msg_name)
		ticket_name = msg.reference_name if msg.reference_doctype == "HD Ticket" else None
		text = msg.message or ""
		has_image = msg.content_type == "image"
		image_source = msg.attach if has_image else None
		channel_label = "WABA"
	else:
		msg = frappe.get_doc("WA Message", msg_name)
		ticket_name = msg.reference_name if msg.reference_doctype == "HD Ticket" else None
		text = msg.message or ""
		has_image = msg.content_type == "image"
		image_source = msg.media_url if has_image else None
		line_name = msg.line
		channel_label = "WA Line"

	if not ticket_name:
		return

	ticket = frappe.get_doc("HD Ticket", ticket_name)

	if ticket.bot_escalated:
		return

	if _is_short_message(text, settings.min_message_words or 3):
		return

	# Multi-turn reply limit
	if settings.conversation_mode == "Multi-turn":
		if (ticket.bot_reply_count or 0) >= (settings.max_bot_replies or 3):
			_escalate(ticket_name)
			return

	# Download image
	images = []
	if has_image and image_source:
		if channel == "waba":
			img_bytes = _download_image_waba(image_source)
		else:
			img_bytes = _download_image_wa_line(image_source, line_name)
		if img_bytes:
			images.append(img_bytes)

	# KB search
	articles = _search_kb(text, settings.kb_search_limit or 3)

	# Gap tracking
	if not articles and settings.enable_gap_tracking:
		_handle_kb_gap(ticket_name, channel_label, text, settings)
		if settings.auto_escalate_on_no_kb:
			_escalate(ticket_name)
			return

	# Build prompt
	kb_context = "\n\n".join(f"Article: {a['title']}\n{a['content']}" for a in articles)
	system_content = settings.system_prompt or "You are a helpful support assistant."
	if kb_context:
		system_content += f"\n\nKnowledge Base:\n{kb_context}"

	history = _get_conversation_history(ticket_name, channel)
	messages = [{"role": "system", "content": system_content}]
	# Include history excluding the current message (last item)
	for h in history[:-1]:
		messages.append(h)
	messages.append({"role": "user", "content": text})

	# LLM call
	from helpdesk.integrations.llm import chat as llm_chat

	try:
		reply = llm_chat(messages, images or None)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: LLM call failed")
		_escalate(ticket_name)
		return

	# Send reply
	try:
		if send_wa_reply:
			send_wa_reply(ticket=ticket_name, message=reply)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: send reply failed")
		return

	# Update ticket state
	frappe.db.set_value(
		"HD Ticket",
		ticket_name,
		{"bot_reply_count": (ticket.bot_reply_count or 0) + 1, "bot_active": 1},
		update_modified=False,
	)

	if settings.conversation_mode == "Single Reply":
		_escalate(ticket_name)


def _handle_kb_gap(
	ticket_name: str,
	channel_label: str,
	text: str,
	settings,
) -> None:
	"""Ask the LLM for a gap suggestion and record it. Non-critical; swallows errors."""
	import json

	try:
		from helpdesk.integrations.llm import chat as llm_chat

		gap_messages = [
			{
				"role": "system",
				"content": 'Given a customer support query, suggest a knowledge base article title and category. Respond ONLY with valid JSON: {"title": "...", "category": "..."}',
			},
			{"role": "user", "content": text},
		]
		raw = llm_chat(gap_messages)
		raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
		gap_data = json.loads(raw)
		suggested_title = gap_data.get("title", "")
		suggested_category = gap_data.get("category", "")
	except Exception:
		suggested_title = ""
		suggested_category = ""

	_record_gap(ticket_name, channel_label, text, suggested_title, suggested_category)
