import json
import math
import re

import frappe

# v2: vectors are gemini-embedding-001 (text-embedding-004 was retired by Google
# and now 404s). Cache keys bumped so no stale text-embedding-004 vectors are served.
_CACHE_KEY = "bot:ticket_embeddings_v2"
_ARTICLE_CACHE_KEY = "bot:article_embeddings_v2"
_EMBED_MODEL = "models/gemini-embedding-001"
_EMBED_DIMS = 768
_CACHE_TTL = 3600  # 1 hour; invalidated immediately on new insert
# Thresholds calibrated for gemini-embedding-001 (768 dims): on dev.localhost
# corpus, nonsense/greeting queries top out ≈0.64 while genuinely relevant
# matches score ≥0.67. Tickets stay stricter than articles.
_MIN_SIMILARITY = 0.70
_MIN_ARTICLE_SIMILARITY = 0.65


def _settings():
	return frappe.get_cached_doc("Helpdesk Bot Settings")


def embed(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
	"""Return a Gemini embedding vector. Same API key as the LLM.

	output_dimensionality=768 keeps vectors the same size text-embedding-004
	produced; cosine_similarity normalizes, so truncated vectors compare fine.
	"""
	import google.generativeai as genai

	settings = _settings()
	genai.configure(api_key=settings.get_password("gemini_api_key"))
	result = genai.embed_content(
		model=_EMBED_MODEL,
		content=text,
		task_type=task_type,
		output_dimensionality=_EMBED_DIMS,
	)
	return result["embedding"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
	if len(a) != len(b):
		# Vectors from different embedding models are not comparable; zip would
		# silently truncate and produce garbage similarities.
		return 0.0
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


# ── KB article embeddings ─────────────────────────────────────────────────────


def invalidate_article_cache() -> None:
	frappe.cache().delete_value(_ARTICLE_CACHE_KEY)


def _load_article_embeddings() -> list[dict]:
	"""Return all stored article embeddings, loading from Redis cache when warm."""
	cached = frappe.cache().get_value(_ARTICLE_CACHE_KEY)
	if cached:
		return cached

	rows = frappe.db.sql(
		"""
		SELECT article, embedding
		FROM `tabHD Article Embedding`
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
				"article": row.article,
				"embedding": json.loads(row.embedding),
			})
		except Exception:
			continue

	frappe.cache().set_value(_ARTICLE_CACHE_KEY, result, expires_in_sec=_CACHE_TTL)
	return result


def search_articles(query: str, top_k: int = 3, allowed_categories: list[str] | None = None) -> list[dict]:
	"""Return up to top_k published KB articles semantically similar to query.

	Article data (title, content, category, internal, status) is fetched fresh from
	HD Article at query time, so a stale embedding can never leak an article that
	has since been unpublished, internalized, or moved out of an allowed category.

	Each result has keys: name, title, content, outline_doc_id.
	Returns empty list on any failure so the caller always gets a safe value.
	"""
	try:
		query_vec = embed(query, task_type="RETRIEVAL_QUERY")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Embeddings: article query embed failed")
		return []

	embeddings = _load_article_embeddings()
	if not embeddings:
		return []

	scored = []
	for item in embeddings:
		sim = cosine_similarity(query_vec, item["embedding"])
		if sim >= _MIN_ARTICLE_SIMILARITY:
			scored.append((sim, item["article"]))

	if not scored:
		return []

	scored.sort(key=lambda x: x[0], reverse=True)
	# Overfetch so results dropped by the live filters below can be backfilled.
	candidates = [name for _, name in scored[: top_k * 4]]

	filters = {
		"name": ["in", candidates],
		"status": "Published",
		"internal": 0,
	}
	if allowed_categories:
		filters["category"] = ["in", allowed_categories]

	rows = frappe.db.get_all(
		"HD Article",
		filters=filters,
		fields=["name", "title", "content", "outline_doc_id"],
	)
	by_name = {r.name: r for r in rows}
	return [by_name[name] for name in candidates if name in by_name][:top_k]


def embed_articles() -> None:
	"""Daily scheduled job: embed published KB articles; re-embed modified ones."""
	try:
		if not _settings().is_enabled:
			return
	except Exception:
		return

	existing = {
		r.article: r
		for r in frappe.db.get_all(
			"HD Article Embedding", fields=["name", "article", "modified"]
		)
	}

	articles = frappe.db.get_all(
		"HD Article",
		filters={"status": "Published"},
		fields=["name", "title", "content", "modified"],
		order_by="modified desc",
		limit=500,
	)

	count = 0
	for article in articles:
		row = existing.get(article.name)
		if row:
			if article.modified <= row.modified:
				continue
			# Article changed since it was embedded — drop the stale row and re-embed.
			frappe.delete_doc(
				"HD Article Embedding", row.name, ignore_permissions=True, force=True
			)

		text = article.title or ""
		clean = _strip_html(article.content or "")
		if clean:
			text += "\n" + clean[:1500]
		if not text.strip():
			continue

		try:
			vec = embed(text, task_type="RETRIEVAL_DOCUMENT")
			frappe.get_doc({
				"doctype": "HD Article Embedding",
				"article": article.name,
				"title": article.title,
				"embedding": json.dumps(vec),
			}).insert(ignore_permissions=True)
			count += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Embeddings: failed for article {article.name}")

	if count:
		frappe.db.commit()
		frappe.logger().info(f"Embeddings: stored {count} article embeddings")


def on_article_update(doc, method=None) -> None:
	"""HD Article on_update hook: drop the stale embedding so the daily job re-embeds.

	Also covers unpublish: deleting the row removes the article from semantic search
	immediately (search filters on live status too, so this is belt and braces).
	"""
	try:
		if not (
			doc.has_value_changed("title")
			or doc.has_value_changed("content")
			or doc.has_value_changed("status")
		):
			return
		name = frappe.db.get_value("HD Article Embedding", {"article": doc.name}, "name")
		if name:
			frappe.delete_doc("HD Article Embedding", name, ignore_permissions=True, force=True)
		invalidate_article_cache()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Embeddings: on_article_update failed")
