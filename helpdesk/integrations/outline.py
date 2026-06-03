import re

import frappe
from frappe import _
from frappe.utils.password import get_decrypted_password

SKIP_COLLECTIONS = {"welcome (imported)"}


# ── Credentials ───────────────────────────────────────────────────────────────


def _get_credentials() -> tuple[str, str]:
	"""Return (base_url, token). Returns ("", "") if not configured."""
	s = frappe.get_cached_doc("HD Settings")
	base_url = (getattr(s, "outline_api_url", None) or "").rstrip("/")
	if not base_url:
		return "", ""
	token = (
		get_decrypted_password(
			"HD Settings", "HD Settings", "outline_api_token", raise_exception=False
		)
		or ""
	)
	return base_url, token


def _outline_request(base_url: str, token: str, endpoint: str, payload: dict) -> dict:
	import requests

	resp = requests.post(
		f"{base_url}/api/{endpoint}",
		json=payload,
		headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
		timeout=30,
	)
	resp.raise_for_status()
	data = resp.json()
	if not data.get("ok"):
		frappe.throw(_(f"Outline API error ({endpoint}): {data.get('message', 'unknown')}"))
	return data


# ── Collection map (cached 1 h) ───────────────────────────────────────────────


def _get_collection_map() -> dict:
	"""Return {collection_id: {name, is_internal}} — cached in Redis 1 hr."""
	cache_key = "outline_collection_map"
	cached = frappe.cache().get_value(cache_key)
	if cached:
		return cached

	base_url, token = _get_credentials()
	if not base_url or not token:
		return {}

	try:
		data = _outline_request(base_url, token, "collections.list", {"limit": 100})
		result = {
			c["id"]: {
				"name": c["name"],
				"is_internal": "internal" in c["name"].lower(),
			}
			for c in data.get("data", [])
		}
		frappe.cache().set_value(cache_key, result, expires_in_sec=3600)
		return result
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Outline: _get_collection_map failed")
		return {}


# ── Content conversion ────────────────────────────────────────────────────────


def _md_to_html(text: str, base_url: str) -> str:
	"""Convert Outline markdown to HTML for HD Article.content (Text Editor field)."""
	try:
		import markdown

		text = re.sub(r":::[\w]*\n?", "", text)  # strip Outline callout blocks
		text = text.replace("(/api/attachments", f"({base_url}/api/attachments")
		return markdown.markdown(text, extensions=["extra"])
	except ImportError:
		import html as _html

		return f"<pre>{_html.escape(text)}</pre>"


def _md_to_text(text: str) -> str:
	"""Strip markdown syntax for plain-text LLM context."""
	text = re.sub(r":::[\w]*\n?", "", text)
	text = re.sub(r"!\[.*?\]\(.*?\)", "", text)           # remove images
	text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links → text
	text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
	text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
	text = re.sub(r"\n{3,}", "\n\n", text)
	return text.strip()


# ── Sync helpers ──────────────────────────────────────────────────────────────


def _get_or_create_category(name: str) -> str:
	existing = frappe.db.get_value("HD Article Category", {"category_name": name}, "name")
	if existing:
		return existing
	return frappe.get_doc({"doctype": "HD Article Category", "category_name": name}).insert(
		ignore_permissions=True
	).name


def _fetch_all_documents(base_url: str, token: str, collection_id: str) -> list[dict]:
	docs, offset, limit = [], 0, 100
	while True:
		data = _outline_request(
			base_url, token, "documents.list",
			{"collectionId": collection_id, "limit": limit, "offset": offset},
		)
		batch = data.get("data", [])
		docs.extend(batch)
		total = data.get("pagination", {}).get("total", 0)
		offset += limit
		if offset >= total:
			break
	return docs


# ── Public API ────────────────────────────────────────────────────────────────


@frappe.whitelist()
def sync_outline_docs() -> dict:
	"""Pull all Outline documents into HD Article records (for the customer portal).

	Runs hourly via scheduler. Can also be triggered manually from HD Settings.
	Collections with "INTERNAL" in their name sync as internal=1 (agents only).
	Articles removed from Outline are archived (not deleted).
	"""
	base_url, token = _get_credentials()
	if not base_url or not token:
		frappe.throw(_("Configure Outline API URL and token in HD Settings → Knowledge Base."))

	col_map = _get_collection_map()
	synced_ids: set[str] = set()
	created = updated = archived = 0

	for col_id, col_info in col_map.items():
		col_name = col_info["name"]
		if col_name.lower() in SKIP_COLLECTIONS:
			continue

		is_internal = col_info["is_internal"]
		category = _get_or_create_category(col_name.title())
		docs = _fetch_all_documents(base_url, token, col_id)

		for doc in docs:
			if doc.get("archivedAt") or doc.get("deletedAt"):
				continue

			outline_id = doc["id"]
			synced_ids.add(outline_id)
			title = doc.get("title") or "Untitled"
			content = _md_to_html(doc.get("text") or "", base_url)
			source_url = base_url + doc.get("url", "")

			existing = frappe.db.get_value(
				"HD Article", {"outline_doc_id": outline_id}, "name"
			)
			if existing:
				frappe.db.set_value(
					"HD Article",
					existing,
					{
						"title": title,
						"content": content,
						"category": category,
						"source_url": source_url,
						"internal": 1 if is_internal else 0,
						"status": "Published",
					},
					update_modified=False,
				)
				updated += 1
			else:
				frappe.get_doc({
					"doctype": "HD Article",
					"title": title,
					"content": content,
					"category": category,
					"status": "Published",
					"outline_doc_id": outline_id,
					"source_url": source_url,
					"internal": 1 if is_internal else 0,
				}).insert(ignore_permissions=True)
				created += 1

	# Archive articles whose Outline source was removed
	synced_articles = frappe.get_all(
		"HD Article",
		filters=[["outline_doc_id", "is", "set"]],
		fields=["name", "outline_doc_id"],
	)
	for art in synced_articles:
		if art.outline_doc_id not in synced_ids:
			frappe.db.set_value(
				"HD Article", art.name, "status", "Archived", update_modified=False
			)
			archived += 1

	frappe.db.commit()
	return {"created": created, "updated": updated, "archived": archived}


def search(query: str, limit: int = 5, exclude_internal: bool = True) -> list[dict]:
	"""Query Outline search API directly (always fresh — used by the bot).

	Returns list of dicts with keys: name, title, content, source_url, outline_doc_id.
	content is a plain-text snippet suitable for LLM context.
	"""
	base_url, token = _get_credentials()
	if not base_url or not token:
		return []

	try:
		col_map = _get_collection_map()
		data = _outline_request(
			base_url, token, "documents.search",
			{"query": query, "limit": limit * 3},  # overfetch to allow filtering
		)

		results = []
		for item in data.get("data", []):
			doc = item.get("document") or {}
			if not doc:
				continue
			if doc.get("archivedAt") or doc.get("deletedAt"):
				continue

			col_id = doc.get("collectionId")
			col_info = col_map.get(col_id, {})
			if col_info.get("name", "").lower() in SKIP_COLLECTIONS:
				continue
			if exclude_internal and col_info.get("is_internal"):
				continue

			# Use search context snippet (most relevant part) or truncated text
			context = item.get("context") or _md_to_text(doc.get("text") or "")[:600]

			results.append({
				"name": doc["id"],
				"title": doc.get("title", "Untitled"),
				"content": context,
				"source_url": base_url + doc.get("url", ""),
				"outline_doc_id": doc["id"],
			})

			if len(results) >= limit:
				break

		return results

	except Exception:
		frappe.log_error(frappe.get_traceback(), "Outline: search failed")
		return []
