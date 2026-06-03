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


_SEARCH_STOPWORDS = {
	"a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of",
	"is", "are", "was", "were", "be", "been", "have", "has", "had", "do", "does",
	"i", "me", "you", "we", "it", "its", "my", "your",
	"give", "tell", "show", "get", "find", "need", "want", "know", "help",
	"can", "please", "some", "more", "about", "with", "what", "how", "where",
	"when", "who", "which", "that", "this", "these", "those", "information",
	"details", "info",
}


def _search_kb(query: str, limit: int) -> list[dict]:
	"""Full-text search against published, non-internal HD Article records.

	Returns a list of dicts with keys: name, title, content, outline_doc_id.
	"""
	if not query:
		return []

	terms = {query}
	for word in query.split():
		clean = word.strip(".,!?;:\"'").lower()
		if len(clean) >= 3 and clean not in _SEARCH_STOPWORDS:
			terms.add(clean)

	conditions = " OR ".join(
		f"(title LIKE %(t{i})s OR content LIKE %(t{i})s)"
		for i in range(len(terms))
	)
	params = {f"t{i}": f"%{term}%" for i, term in enumerate(sorted(terms))}
	params["limit"] = limit

	return frappe.db.sql(
		f"""
		SELECT name, title, content, outline_doc_id
		FROM `tabHD Article`
		WHERE status = 'Published'
		  AND (internal = 0 OR internal IS NULL)
		  AND ({conditions})
		LIMIT %(limit)s
		""",
		params,
		as_dict=True,
	)


def _combined_kb_search(query: str, limit: int) -> list[dict]:
	"""Query both local HD Articles and Outline directly, merge and deduplicate.

	Outline results take precedence for documents that exist in both (fresher content).
	"""
	hd_articles = _search_kb(query, limit)

	outline_results: list[dict] = []
	try:
		from helpdesk.integrations.outline import search as _outline_search

		outline_results = _outline_search(query, limit=limit, exclude_internal=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: Outline search failed")

	# Remove HD Article entries already covered by Outline (Outline is fresher)
	outline_ids = {r["outline_doc_id"] for r in outline_results if r.get("outline_doc_id")}
	hd_filtered = [a for a in hd_articles if a.get("outline_doc_id") not in outline_ids]

	return (hd_filtered + outline_results)[:limit]


def _record_gap(
	ticket_name: str | None,
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
				"ticket": ticket_name or "",
				"channel": channel,
				"query_text": query_text,
				"suggested_title": suggested_title,
				"suggested_category": suggested_category,
				"status": "Pending",
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: _record_gap failed")


# ── Bot state abstraction ──────────────────────────────────────────────────────


class _BotState:
	"""Wraps bot conversation state stored on either an HD Ticket or Redis cache.

	When a ticket exists, state is persisted in HD Ticket fields. When no ticket
	exists, state is stored in Redis with a 7-day TTL (keyed by line:jid).
	"""

	def __init__(self, ticket_name: str | None, jid: str, line_name: str | None):
		self.ticket_name = ticket_name
		self.jid = jid
		self.line_name = line_name
		self._ticket = None
		self._cache_key = f"wa_bot_session:{line_name}:{jid}"

		if ticket_name:
			self._ticket = frappe.get_doc("HD Ticket", ticket_name)

	def _get_cache(self) -> dict:
		return frappe.cache().get_value(self._cache_key) or {
			"bot_reply_count": 0,
			"bot_escalated": 0,
			"bot_active": 0,
		}

	def _set_cache(self, data: dict) -> None:
		frappe.cache().set_value(self._cache_key, data, expires_in_sec=86400 * 7)

	@property
	def bot_reply_count(self) -> int:
		if self._ticket:
			return self._ticket.bot_reply_count or 0
		return self._get_cache()["bot_reply_count"]

	@property
	def bot_escalated(self) -> bool:
		if self._ticket:
			return bool(self._ticket.bot_escalated)
		return bool(self._get_cache()["bot_escalated"])

	def update(self, **kwargs) -> None:
		if self._ticket:
			frappe.db.set_value("HD Ticket", self.ticket_name, kwargs, update_modified=False)
		else:
			session = self._get_cache()
			session.update(kwargs)
			self._set_cache(session)

	def send_reply(self, message: str) -> None:
		if not send_wa_reply:
			return
		if self.ticket_name:
			send_wa_reply(ticket=self.ticket_name, message=message)
		else:
			send_wa_reply(jid=self.jid, line=self.line_name, message=message)


# ── Conversation history ───────────────────────────────────────────────────────


def _get_conversation_history(
	ticket_name: str | None, channel: str, jid: str | None = None
) -> list[dict]:
	"""Return last 10 messages as role/content dicts, oldest first.

	Uses ticket reference when available, falls back to jid-based lookup.
	"""
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

	# wa_line — prefer ticket reference, fall back to jid
	filters = (
		{"reference_doctype": "HD Ticket", "reference_name": ticket_name}
		if ticket_name
		else {"jid": jid}
	)
	rows = frappe.db.get_all(
		"WA Message",
		filters=filters,
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


def _escalate(state: _BotState) -> None:
	"""Optionally send an escalation message then mark the conversation as escalated."""
	settings = _bot_settings()
	if settings.escalation_message_enabled and settings.escalation_message:
		try:
			state.send_reply(settings.escalation_message)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: escalation message failed")

	state.update(bot_escalated=1, bot_active=0)


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
		if not ticket_name:
			return  # WABA always requires a ticket
		text = msg.message or ""
		has_image = msg.content_type == "image"
		image_source = msg.attach if has_image else None
		jid = None
		channel_label = "WABA"
	else:
		msg = frappe.get_doc("WA Message", msg_name)
		ticket_name = msg.reference_name if msg.reference_doctype == "HD Ticket" else None
		text = msg.message or ""
		has_image = msg.content_type == "image"
		image_source = msg.media_url if has_image else None
		line_name = msg.line
		jid = msg.jid
		channel_label = "WA Line"

	state = _BotState(ticket_name, jid, line_name)

	if state.bot_escalated:
		return

	if ticket_name:
		_assign = frappe.db.get_value("HD Ticket", ticket_name, "_assign")
		if _assign and frappe.parse_json(_assign):
			return

	if ticket_name:
		_company_key = f"wa_bot_company:{ticket_name}"
		if frappe.cache().get_value(_company_key):
			# Previous bot message asked for company name — treat this reply as the answer
			company_name = text.strip()
			if company_name:
				existing = frappe.db.get_value("HD Customer", {"customer_name": company_name}, "name")
				if existing:
					cust_name = existing
				else:
					cust = frappe.get_doc({"doctype": "HD Customer", "customer_name": company_name})
					cust.insert(ignore_permissions=True)
					frappe.db.commit()
					cust_name = cust.name
				frappe.db.set_value("HD Ticket", ticket_name, "customer", cust_name)
				frappe.cache().delete_value(_company_key)
				try:
					state.send_reply(f"Thank you! I've noted your company as *{company_name}*. How can I help you?")
					state.update(bot_reply_count=state.bot_reply_count + 1, bot_active=1)
				except Exception:
					frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: company confirm message failed")
			return

		customer = frappe.db.get_value("HD Ticket", ticket_name, "customer")
		if not customer:
			try:
				state.send_reply("Before we get started, could you please share your company name?")
				frappe.cache().set_value(_company_key, 1, expires_in_sec=3600)
				state.update(bot_reply_count=state.bot_reply_count + 1, bot_active=1)
			except Exception:
				frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: company name prompt failed")
			return

	if _is_short_message(text, settings.min_message_words or 3):
		if state.bot_reply_count == 0 and settings.clarification_message_enabled and settings.clarification_message:
			try:
				state.send_reply(settings.clarification_message)
				state.update(bot_reply_count=1, bot_active=1)
			except Exception:
				frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: clarification message failed")
		return

	# Multi-turn reply limit
	if settings.conversation_mode == "Multi-turn":
		if state.bot_reply_count >= (settings.max_bot_replies or 3):
			_escalate(state)
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

	# KB search (local HD Articles + live Outline query, deduped)
	articles = _combined_kb_search(text, settings.kb_search_limit or 3)

	# Gap tracking
	if not articles and settings.enable_gap_tracking:
		_handle_kb_gap(ticket_name, channel_label, text, settings)
		if settings.auto_escalate_on_no_kb:
			if state.bot_reply_count == 0 and settings.clarification_message_enabled and settings.clarification_message:
				try:
					state.send_reply(settings.clarification_message)
					state.update(bot_reply_count=1, bot_active=1)
				except Exception:
					frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: clarification message failed")
				return
			_escalate(state)
			return

	# Build prompt
	kb_context = "\n\n".join(f"Article: {a['title']}\n{a['content']}" for a in articles)
	system_content = settings.system_prompt or "You are a helpful support assistant."
	if kb_context:
		system_content += f"\n\nKnowledge Base:\n{kb_context}"

	history = _get_conversation_history(ticket_name, channel, jid=jid)
	prior_turns = history[:-1]
	if prior_turns:
		system_content += (
			"\n\nOVERRIDE — FOLLOW-UP RULE (takes precedence over all other instructions): "
			"The conversation history shows prior exchanges. If the user's current message asks you to "
			"reformat, filter, modify, or clarify your PREVIOUS response (examples: 'remove the expiry date', "
			"'show only names', 'make it a table', 'without the key column') you MUST fulfil that request "
			"using your previous response as the source. Do NOT say you lack information. "
			"This rule overrides the KB-only restriction."
		)

	messages = [{"role": "system", "content": system_content}]
	for h in prior_turns:
		messages.append(h)
	messages.append({"role": "user", "content": text})

	# LLM call
	from helpdesk.integrations.llm import chat as llm_chat

	try:
		reply = llm_chat(messages, images or None)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: LLM call failed")
		_escalate(state)
		return

	# Send reply
	try:
		state.send_reply(reply)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: send reply failed")
		return

	state.update(bot_reply_count=state.bot_reply_count + 1, bot_active=1)

	if settings.conversation_mode == "Single Reply":
		_escalate(state)


def _handle_kb_gap(
	ticket_name: str | None,
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


# ── Doc-event handlers ────────────────────────────────────────────────────────


def handle_whatsapp_message(doc, method=None) -> None:
	"""after_insert handler for WhatsApp Message (WABA path)."""
	if doc.type != "Incoming":
		return
	if doc.reference_doctype != "HD Ticket" or not doc.reference_name:
		return

	settings = _bot_settings()
	if not settings.is_enabled:
		return

	if doc.whatsapp_account:
		bot_enabled = frappe.db.get_value("WhatsApp Account", doc.whatsapp_account, "bot_enabled")
		if not bot_enabled:
			return

	frappe.enqueue(
		"helpdesk.integrations.bot.process_message",
		queue="short",
		job_id=f"bot_msg_{doc.name}",
		enqueue_after_commit=True,
		msg_name=doc.name,
		channel="waba",
	)


def handle_wa_message(doc, method=None) -> None:
	"""after_insert handler for WA Message (Evolution API / WA Line path)."""
	if doc.direction != "Incoming":
		return

	settings = _bot_settings()
	if not settings.is_enabled:
		return

	if doc.line:
		bot_enabled = frappe.db.get_value("WA Line", doc.line, "bot_enabled")
		if not bot_enabled:
			return

	frappe.enqueue(
		"helpdesk.integrations.bot.process_message",
		queue="short",
		job_id=f"bot_msg_{doc.name}",
		enqueue_after_commit=True,
		msg_name=doc.name,
		channel="wa_line",
	)
