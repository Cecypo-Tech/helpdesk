# helpdesk/integrations/bot.py
import frappe


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
