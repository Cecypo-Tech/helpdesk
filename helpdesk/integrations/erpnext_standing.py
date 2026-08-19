"""Live account standing, read from ERPNext per request.

Standing is deliberately NOT mirrored. It changes daily, it is advisory, and a
stale copy is worse than a live read that degrades visibly — so this is fetched
on demand rather than synced.

That makes latency and failure the real design problems, which is why most of
this module is cache and circuit breaker rather than parsing.

The binding rule, inherited from the whole feature: **standing never blocks**.
An ERPNext outage costs the badge, never the ticket. Every path here returns a
dict with a `status`; none of them raise.
"""

import frappe

from helpdesk.integrations.erpnext_remote import call

CACHE_TTL = 300  # seconds; a ticket view must not re-hit ERPNext on every render
BREAKER_THRESHOLD = 3
BREAKER_COOLOFF = 300

BREAKER_KEY = "erpnext:standing:failures"


def cache_key(customer: str) -> str:
	return f"erpnext:standing:{customer}"


def failure_count() -> int:
	try:
		return int(frappe.cache().get_value(BREAKER_KEY) or 0)
	except Exception:
		return 0


def reset_breaker() -> None:
	try:
		frappe.cache().delete_value(BREAKER_KEY)
	except Exception:
		pass


def record_failure() -> None:
	try:
		frappe.cache().set_value(BREAKER_KEY, failure_count() + 1, expires_in_sec=BREAKER_COOLOFF)
	except Exception:
		pass


def breaker_is_open() -> bool:
	"""True when ERPNext has failed enough times that we should stop asking.

	Without this, every ticket view pays a full network timeout while ERPNext is
	down — turning an outage over there into a slow helpdesk over here. The
	counter is global, not per customer: ERPNext being unreachable is not a
	property of one customer.
	"""
	return failure_count() >= BREAKER_THRESHOLD


def unavailable(error: str) -> dict:
	return {"status": "unavailable", "error": error, "customer": None}


def fetch(customer: str | None, use_cache: bool = True) -> dict:
	"""Account standing for one ERPNext customer.

	Returns a dict whose `status` is one of:
	  "ok"          — figures are present and current
	  "unknown"     — nothing to ask about (no linked ERPNext customer)
	  "unavailable" — ERPNext could not answer; show nothing, block nothing
	"""
	if not customer:
		# A ticket with no linked ERPNext customer is ordinary, not a failure.
		return {"status": "unknown", "error": None, "customer": None}

	if use_cache:
		try:
			cached = frappe.cache().get_value(cache_key(customer))
			if cached:
				return cached
		except Exception:
			pass

	if breaker_is_open():
		return unavailable("ERPNext is not responding; retrying shortly")

	try:
		result = call("helpdesk_customer_standing", {"customer": customer})
	except Exception as exc:
		# The client is not supposed to raise, but this is the last line between
		# an ERPNext problem and an agent's ticket view.
		record_failure()
		return unavailable(f"{type(exc).__name__}: {exc}")

	if not result.get("ok"):
		record_failure()
		return unavailable(result.get("error") or "ERPNext did not answer")

	reset_breaker()

	data = result.get("data") or {}
	rows = data.get("standing") or []
	row = next((r for r in rows if r.get("customer") == customer), None)

	if row is None:
		# Reached ERPNext, and it has nothing open for this customer.
		out = {
			"status": "ok",
			"error": None,
			"customer": customer,
			"outstanding": 0,
			"overdue": 0,
			"overdue_count": 0,
			"oldest_due_date": None,
			"days_overdue": 0,
			"currency": None,
			"is_overdue": False,
			"as_of": data.get("as_of"),
		}
	else:
		out = {
			"status": "ok",
			"error": None,
			"customer": customer,
			"outstanding": row.get("outstanding") or 0,
			"overdue": row.get("overdue") or 0,
			"overdue_count": row.get("overdue_count") or 0,
			"oldest_due_date": row.get("oldest_due_date"),
			"days_overdue": row.get("days_overdue") or 0,
			"currency": row.get("currency"),
			"is_overdue": bool(row.get("is_overdue")),
			"as_of": data.get("as_of"),
		}

	# Only successes are cached. Caching a failure would hide a recovered
	# ERPNext for the whole TTL.
	try:
		frappe.cache().set_value(cache_key(customer), out, expires_in_sec=CACHE_TTL)
	except Exception:
		pass

	return out
