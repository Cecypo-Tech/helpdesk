import json
import math
import re

import frappe

_CACHE_KEY = "bot:ticket_embeddings_v1"
_CACHE_TTL = 3600  # 1 hour; invalidated immediately on new insert
_MIN_SIMILARITY = 0.75


def _settings():
	return frappe.get_cached_doc("Helpdesk Bot Settings")


def embed(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
	"""Return a Gemini text-embedding-004 vector. Same API key as the LLM."""
	import google.generativeai as genai

	settings = _settings()
	genai.configure(api_key=settings.get_password("gemini_api_key"))
	result = genai.embed_content(
		model="models/text-embedding-004",
		content=text,
		task_type=task_type,
	)
	return result["embedding"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
	dot = sum(x * y for x, y in zip(a, b))
	norm_a = math.sqrt(sum(x * x for x in a))
	norm_b = math.sqrt(sum(x * x for x in b))
	return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def invalidate_cache() -> None:
	frappe.cache().delete_value(_CACHE_KEY)


def _load_embeddings() -> list[dict]:
	"""Return all stored embeddings, loading from Redis cache when warm."""
	cached = frappe.cache().get_value(_CACHE_KEY)
	if cached:
		return cached

	rows = frappe.db.sql(
		"""
		SELECT ticket, subject, resolution_details, embedding
		FROM `tabHD Ticket Embedding`
		WHERE embedding IS NOT NULL AND embedding != ''
		ORDER BY creation DESC
		LIMIT 2000
		""",
		as_dict=True,
	)

	result = []
	for row in rows:
		try:
			result.append({
				"subject": row.subject or "",
				"resolution_details": row.resolution_details or "",
				"embedding": json.loads(row.embedding),
			})
		except Exception:
			continue

	frappe.cache().set_value(_CACHE_KEY, result, expires_in_sec=_CACHE_TTL)
	return result


def search_resolved_tickets(query: str, top_k: int = 3) -> list[dict]:
	"""Return up to top_k resolved tickets semantically similar to query.

	Each result has keys: subject, resolution_details.
	Returns empty list on any failure so the caller always gets a safe value.
	"""
	try:
		query_vec = embed(query, task_type="RETRIEVAL_QUERY")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Embeddings: query embed failed")
		return []

	embeddings = _load_embeddings()
	if not embeddings:
		return []

	scored = []
	for item in embeddings:
		sim = cosine_similarity(query_vec, item["embedding"])
		if sim >= _MIN_SIMILARITY:
			scored.append({
				"subject": item["subject"],
				"resolution_details": item["resolution_details"],
				"similarity": sim,
			})

	scored.sort(key=lambda x: x["similarity"], reverse=True)
	return scored[:top_k]


def _strip_html(html: str) -> str:
	return re.sub(r"<[^>]+>", " ", html).strip()


def embed_resolved_tickets() -> None:
	"""Daily scheduled job: embed newly resolved tickets."""
	try:
		if not _settings().is_enabled:
			return
	except Exception:
		return

	existing = {
		r.ticket for r in frappe.db.get_all("HD Ticket Embedding", fields=["ticket"])
	}

	resolved = frappe.db.get_all(
		"HD Ticket",
		filters={"status": "Resolved"},
		fields=["name", "subject", "resolution_details"],
		order_by="creation desc",
		limit=100,
	)

	count = 0
	for ticket in resolved:
		if ticket.name in existing or not ticket.subject:
			continue

		text = ticket.subject
		if ticket.resolution_details:
			clean = _strip_html(ticket.resolution_details)
			if clean:
				text += "\n" + clean[:1500]

		try:
			vec = embed(text, task_type="RETRIEVAL_DOCUMENT")
			frappe.get_doc({
				"doctype": "HD Ticket Embedding",
				"ticket": ticket.name,
				"subject": ticket.subject,
				"resolution_details": (ticket.resolution_details or "")[:3000],
				"embedding": json.dumps(vec),
			}).insert(ignore_permissions=True)
			count += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Embeddings: failed for {ticket.name}")

	if count:
		frappe.db.commit()
		# cache is invalidated via HDTicketEmbedding.after_insert; nothing more to do
		frappe.logger().info(f"Embeddings: stored {count} new resolved tickets")
