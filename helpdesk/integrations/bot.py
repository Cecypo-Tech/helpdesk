# helpdesk/integrations/bot.py
import re
import frappe

from helpdesk import entitlement

try:
	from helpdesk.integrations.wa import send_wa_reply
except Exception:
	send_wa_reply = None  # type: ignore[assignment]

# Matches the agent-initials stamp appended by send_wa_reply / _send_fw_reply,
# e.g. "\n^BOT", "\n^AB". Strip these from assistant messages before feeding
# conversation history to the LLM to prevent the model from mimicking the suffix.
_AGENT_SUFFIX_RE = re.compile(r"\n\^[A-Z]{1,5}\s*$")

# How many messages from the customer's PREVIOUS conversations to include as
# context (repeat-issue detection). Tune here.
_PRIOR_CONTEXT_MESSAGES = 15
_PRIOR_CONTEXT_MSG_CHARS = 200
_PRIOR_CONTEXT_TOTAL_CHARS = 2500


def _strip_agent_suffix(text: str) -> str:
	return _AGENT_SUFFIX_RE.sub("", text)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _bot_settings():
	return frappe.get_cached_doc("Helpdesk Bot Settings")


def _is_short_message(text: str | None, min_words: int) -> bool:
	"""Return True when text has fewer words than min_words."""
	return len((text or "").split()) < min_words


def _get_allowed_categories() -> list[str]:
	"""Return HD Article Category docnames the bot may use. Empty list = no restriction."""
	try:
		settings = _bot_settings()
		return [row.category for row in (settings.allowed_categories or []) if row.category]
	except Exception:
		return []


def _search_kb(
	query: str,
	limit: int,
	allowed_categories: list[str] | None = None,
	product: str | None = None,
) -> list[dict]:
	"""Semantic search against published, non-internal HD Article records.

	Falls back to a single whole-query LIKE when no embeddings are available
	(index not built yet, or the embedding API failed).

	Returns a list of dicts with keys: name, title, content, outline_doc_id.
	"""
	if not query:
		return []

	try:
		from helpdesk.integrations.embeddings import search_articles

		results = search_articles(
			query, top_k=limit, allowed_categories=allowed_categories, product=product
		)
		if results:
			return results
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: semantic KB search failed")

	category_condition = ""
	# Overfetch so the product filter can drop wrong-product articles without
	# shrinking the result set; SQL LIMIT would otherwise truncate first.
	params = {"q": f"%{query}%", "limit": limit * 4 if product else limit}
	if allowed_categories:
		category_condition = "AND category IN %(categories)s"
		params["categories"] = tuple(allowed_categories)

	rows = frappe.db.sql(
		f"""
		SELECT name, title, content, outline_doc_id
		FROM `tabHD Article`
		WHERE status = 'Published'
		  AND (internal = 0 OR internal IS NULL)
		  {category_condition}
		  AND (title LIKE %(q)s OR content LIKE %(q)s)
		LIMIT %(limit)s
		""",
		params,
		as_dict=True,
	)

	if product:
		rows = entitlement.filter_articles_for_product(rows, product)

	return rows[:limit]


def _filter_outline_by_category(
	results: list[dict],
	allowed_categories: list[str],
	product: str | None = None,
) -> list[dict]:
	"""Keep only Outline results whose synced HD Article passes every filter.

	Conservative by design, and this predates the product work: when a category
	allowlist is configured, results that can't be mapped to a local article are
	dropped — unknown documents must never reach the LLM past a configured
	restriction. That drop rule fires ONLY when allowed_categories is non-empty.

	When allowed_categories is empty and only a product is set, unmapped rows
	pass through untouched, matching filter_articles_for_product: an
	unidentifiable row is not evidence of a wrong product, and rollout must stay
	inert until articles are actually tagged. Rows that DO resolve to an HD
	Article are still filtered by product.
	"""
	doc_ids = [r["outline_doc_id"] for r in results if r.get("outline_doc_id")]
	if not doc_ids:
		return results if not allowed_categories else []

	filters = {"outline_doc_id": ["in", doc_ids]}
	if allowed_categories:
		filters["category"] = ["in", allowed_categories]

	# Rows that resolve to a local HD Article (category-filtered already when an
	# allowlist is configured; otherwise every resolvable row).
	rows = frappe.db.get_all(
		"HD Article",
		filters=filters,
		fields=["name", "outline_doc_id"],
	)
	mapped_ids = {r.get("outline_doc_id") for r in rows}

	if product:
		rows = entitlement.filter_articles_for_product(rows, product)

	allowed_ids = {r.get("outline_doc_id") for r in rows}

	if allowed_categories:
		# Conservative path: a document that can't be resolved at all is an
		# unknown document, drop it.
		return [r for r in results if r.get("outline_doc_id") in allowed_ids]

	# No category allowlist: a document that can't be resolved to any HD
	# Article is not evidence of a wrong product — let it through. A document
	# that DOES resolve is still subject to the product filter above.
	return [
		r
		for r in results
		if r.get("outline_doc_id") not in mapped_ids or r.get("outline_doc_id") in allowed_ids
	]


def _combined_kb_search(query: str, limit: int, product: str | None = None) -> list[dict]:
	"""Query both local HD Articles and Outline directly, merge and deduplicate.

	Outline results take precedence for documents that exist in both (fresher content).
	Both paths respect the Allowed Categories list in Helpdesk Bot Settings, and
	both drop articles tagged for a different product when one is known.
	"""
	allowed_categories = _get_allowed_categories()
	hd_articles = _search_kb(
		query, limit, allowed_categories=allowed_categories, product=product
	)

	outline_results: list[dict] = []
	try:
		from helpdesk.integrations.outline import search as _outline_search

		outline_results = _outline_search(query, limit=limit, exclude_internal=True)
		if allowed_categories or product:
			outline_results = _filter_outline_by_category(
				outline_results, allowed_categories, product=product
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: Outline search failed")

	# Remove HD Article entries already covered by Outline (Outline is fresher)
	outline_ids = {r["outline_doc_id"] for r in outline_results if r.get("outline_doc_id")}
	hd_filtered = [a for a in hd_articles if a.get("outline_doc_id") not in outline_ids]

	return (outline_results + hd_filtered)[:limit]


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
				"product": entitlement.resolve_product_for_ticket(ticket_name),
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
			{
				"role": "user" if r.type == "Incoming" else "assistant",
				"content": (r.message or "") if r.type == "Incoming" else _strip_agent_suffix(r.message or ""),
			}
			for r in rows
		]

	# wa_line: try reference first; fall back to jid when reference returns nothing.
	# Messages created before a ticket was linked (or via phone-mirror path) only have jid set.
	rows = []
	if ticket_name:
		rows = frappe.db.get_all(
			"WA Message",
			filters={"reference_doctype": "HD Ticket", "reference_name": ticket_name},
			fields=["direction", "message", "creation"],
			order_by="creation desc",
			limit=10,
		)
	if not rows and jid:
		rows = frappe.db.get_all(
			"WA Message",
			filters={"jid": jid},
			fields=["direction", "message", "creation"],
			order_by="creation desc",
			limit=10,
		)
	rows = list(reversed(rows))
	return [
		{
			"role": "user" if r.direction == "Incoming" else "assistant",
			"content": (r.message or "") if r.direction == "Incoming" else _strip_agent_suffix(r.message or ""),
		}
		for r in rows
	]


def _format_prior_context(rows: list[tuple[str, str]]) -> str:
	"""rows: (speaker, text) oldest first → compact block capped for prompt budget."""
	lines = []
	total = 0
	for speaker, text in rows:
		text = _strip_agent_suffix(text or "").strip()
		if not text:
			continue
		line = f"{speaker}: {text[:_PRIOR_CONTEXT_MSG_CHARS]}"
		total += len(line)
		if total > _PRIOR_CONTEXT_TOTAL_CHARS:
			break
		lines.append(line)
	return "\n".join(lines)


def _get_prior_customer_context(
	channel: str,
	jid: str | None,
	line_name: str | None,
	ticket_name: str | None,
) -> str:
	"""Return a compact transcript of the customer's PREVIOUS conversations.

	Excludes the current ticket's thread (already provided as conversation
	history). Best-effort: returns "" on any failure — this must never break
	message processing (custom-field queries can raise OperationalError on
	unmigrated sites).
	"""
	try:
		rows: list[tuple[str, str]] = []

		if channel == "wa_line" and jid:
			filters: dict = {"jid": jid}
			if line_name:
				filters["line"] = line_name
			messages = frappe.db.get_all(
				"WA Message",
				filters=filters,
				fields=["direction", "message", "reference_name", "creation"],
				order_by="creation desc",
				limit=_PRIOR_CONTEXT_MESSAGES + 25,
			)
			prior = [
				m for m in messages
				if not (ticket_name and m.reference_name == ticket_name)
			][:_PRIOR_CONTEXT_MESSAGES]
			rows = [
				("Customer" if m.direction == "Incoming" else "Agent", m.message)
				for m in reversed(prior)
			]

		elif channel == "waba" and ticket_name:
			contact, raised_by = frappe.db.get_value(
				"HD Ticket", ticket_name, ["contact", "raised_by"]
			)
			ticket_filters = {"name": ["!=", ticket_name]}
			if contact:
				ticket_filters["contact"] = contact
			elif raised_by:
				ticket_filters["raised_by"] = raised_by
			else:
				return ""
			prior_tickets = frappe.db.get_all(
				"HD Ticket",
				filters=ticket_filters,
				pluck="name",
				order_by="creation desc",
				limit=3,
			)
			if not prior_tickets:
				return ""
			messages = frappe.db.get_all(
				"WhatsApp Message",
				filters={
					"reference_doctype": "HD Ticket",
					"reference_name": ["in", prior_tickets],
				},
				fields=["type", "message", "creation"],
				order_by="creation desc",
				limit=_PRIOR_CONTEXT_MESSAGES,
			)
			rows = [
				("Customer" if m.type == "Incoming" else "Agent", m.message)
				for m in reversed(messages)
			]

		if not rows:
			return ""
		return _format_prior_context(rows)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: prior customer context failed")
		return ""


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


# ── Company name extraction ───────────────────────────────────────────────────


def _extract_company_name(text: str) -> str:
	"""Use the LLM to pull just the company/business name out of a free-form message.

	Returns empty string if no company name is found or the LLM call fails.
	"""
	from helpdesk.integrations.llm import chat as llm_chat

	try:
		messages = [
			{
				"role": "system",
				"content": (
					"You are a company-name extractor. Return ONLY the company or business name — "
					"nothing else, no explanation, no punctuation around it.\n\n"
					"Rules:\n"
					"- Company names are short (1–6 words), typically proper nouns.\n"
					"- If the message is a question, complaint, or does not clearly state a company name → return empty string.\n"
					"- Do NOT return sentences, prices, dates, invoice references, or partial phrases.\n"
					"- Be conservative: when in doubt, return empty string.\n\n"
					"Examples:\n"
					"User: 'We are Acme Corporation' → Acme Corporation\n"
					"User: 'safaricom' → Safaricom\n"
					"User: 'the company is TechCorp Ltd' → TechCorp Ltd\n"
					"User: 'Also we agreed you'd charge 2k for the template' → \n"
					"User: 'I need help with my invoice' → \n"
					"User: 'you raised 2 invoices of 2k instead of one' → "
				),
			},
			{"role": "user", "content": text},
		]
		result = llm_chat(messages).strip().strip(".,;:")
		# Reject anything that looks like a sentence rather than a name:
		# too long, contains a question mark, too many words, or sentence-ending punctuation mid-string.
		if (
			not result
			or len(result) > 60
			or "?" in result
			or result.count(" ") > 6
			or any(result[i] in ".!" for i in range(len(result) - 1))
		):
			return ""
		return result
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: company name extraction failed")
		return ""


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
		_company_retry_key = f"wa_bot_company_retry:{ticket_name}"
		# Atomic-ish: fetch and immediately delete so a concurrent job won't also claim it.
		_waiting_for_company = frappe.cache().get_value(_company_key)
		if _waiting_for_company:
			frappe.cache().delete_value(_company_key)
			# Previous bot message asked for company name — extract it from the reply.
			company_name = _extract_company_name(text)
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
				frappe.cache().delete_value(_company_retry_key)
				try:
					state.send_reply(f"Thank you! I've noted your company as *{company_name}*. How can I help you?")
					state.update(bot_reply_count=state.bot_reply_count + 1, bot_active=1)
				except Exception:
					frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: company confirm message failed")
				return
			# Extraction failed — re-ask once; on second failure fall through to normal handling
			# so the user's actual message still gets a response.
			already_retried = frappe.cache().get_value(_company_retry_key)
			if not already_retried:
				frappe.cache().set_value(_company_retry_key, 1, expires_in_sec=3600)
				frappe.cache().set_value(_company_key, 1, expires_in_sec=3600)
				try:
					state.send_reply("Sorry, I didn't catch that — could you share just your company name? (e.g. *Acme Ltd*)")
					state.update(bot_reply_count=state.bot_reply_count + 1, bot_active=1)
				except Exception:
					frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: company re-ask failed")
				return
			# Second failure: clear retry flag and fall through so the message is handled normally.
			frappe.cache().delete_value(_company_retry_key)

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
	articles = _combined_kb_search(
		text,
		settings.kb_search_limit or 3,
		product=entitlement.resolve_product_for_ticket(ticket_name),
	)

	# Semantic search over resolved tickets (RAG — same Gemini API key)
	resolved_context = ""
	try:
		from helpdesk.integrations.embeddings import search_resolved_tickets
		similar = search_resolved_tickets(text, top_k=2)
		if similar:
			parts = []
			for r in similar:
				if r["resolution_details"]:
					parts.append(f"Past ticket: {r['subject']}\nResolution: {r['resolution_details'][:600]}")
			if parts:
				resolved_context = "\n\n".join(parts)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: resolved ticket search failed")

	# Gap tracking
	if not articles and not resolved_context and settings.enable_gap_tracking:
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
	if resolved_context:
		system_content += f"\n\nResolved past tickets (use as reference, do not quote directly):\n{resolved_context}"

	prior_context = _get_prior_customer_context(channel, jid, line_name, ticket_name)
	if prior_context:
		system_content += (
			"\n\nEarlier conversations with this customer (may be a repeat issue — "
			f"use for context, do not quote verbatim):\n{prior_context}"
		)

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


@frappe.whitelist()
def suggest_agent_reply(ticket: str, channel: str = "wa_line") -> str:
	"""Return an LLM-drafted reply suggestion for the agent UI.

	Never sent automatically — the agent reviews and edits before sending.
	"""
	jid = None
	if channel == "wa_line":
		try:
			jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
		except Exception:
			pass

	history = _get_conversation_history(ticket, channel, jid=jid)
	if not history:
		return ""

	last_customer_msg = next(
		(h["content"] for h in reversed(history) if h["role"] == "user"), ""
	)

	articles = (
		_combined_kb_search(
			last_customer_msg, 3, product=entitlement.resolve_product_for_ticket(ticket)
		)
		if last_customer_msg
		else []
	)
	kb_context = "\n\n".join(f"Article: {a['title']}\n{a['content']}" for a in articles)

	resolved_context = ""
	try:
		from helpdesk.integrations.embeddings import search_resolved_tickets

		similar = search_resolved_tickets(last_customer_msg, top_k=2)
		if similar:
			parts = [
				f"Past ticket: {r['subject']}\nResolution: {r['resolution_details'][:500]}"
				for r in similar
				if r["resolution_details"]
			]
			resolved_context = "\n\n".join(parts)
	except Exception:
		pass

	system = (
		"You are an experienced customer support agent. "
		"Draft a clear, concise, professional reply to the customer's latest message. "
		"Write in first person as the agent — friendly but to the point, 2 to 4 sentences. "
		"Do not mention AI or that this is a suggestion."
	)
	if kb_context:
		system += f"\n\nRelevant knowledge base:\n{kb_context}"
	if resolved_context:
		system += f"\n\nHow similar past issues were resolved:\n{resolved_context}"

	line_name = None
	if channel == "wa_line":
		try:
			line_name = frappe.db.get_value("HD Ticket", ticket, "baileys_line")
		except Exception:
			pass
	prior_context = _get_prior_customer_context(channel, jid, line_name, ticket)
	if prior_context:
		system += (
			"\n\nEarlier conversations with this customer (may be a repeat issue — "
			f"use for context, do not quote verbatim):\n{prior_context}"
		)

	from helpdesk.integrations.llm import chat as llm_chat

	try:
		return llm_chat([{"role": "system", "content": system}] + history, max_tokens=256)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Bot: suggest_agent_reply failed")
		return ""


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
