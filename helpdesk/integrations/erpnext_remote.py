"""HTTP client for a REMOTE ERPNext bench.

Helpdesk and ERPNext run on separate benches, so everything ERPNext-related is
a network call rather than an app import. That has one non-negotiable
consequence, which every function here honours: **calls fail open**. An
unreachable, misconfigured or erroring ERPNext must leave the helpdesk fully
working, because nothing in this feature is permitted to block a ticket, a
reply, or a bot answer.

`call()` therefore never raises. It returns {"ok", "data", "error"} and callers
decide what a failure means for them — which is almost always "carry on with
what we already had".

Deliberately a SIBLING of `helpdesk/integrations/erpnext/` rather than a module
inside it. That package is upstream's same-bench integration, gated on erpnext
being installed locally and inert here; putting cross-bench code in it would
invite exactly the confusion this separation avoids.
"""

import frappe
import requests
from frappe import _
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DOCTYPE = "ERPNext Sync Settings"
DEFAULT_TIMEOUT = 30


def build_session() -> requests.Session:
	sess = requests.Session()
	# Retry only idempotent methods. Every endpoint here is a read, but keeping
	# POST out of the retry set means a future write cannot be duplicated by a
	# retry — the same rule the Evolution client follows.
	retry = Retry(
		total=2,
		backoff_factor=0.5,
		status_forcelist=[429, 500, 502, 503, 504],
		allowed_methods=frozenset(["GET", "HEAD", "OPTIONS"]),
	)
	adapter = HTTPAdapter(max_retries=retry)
	sess.mount("http://", adapter)
	sess.mount("https://", adapter)
	return sess


session: requests.Session = build_session()


def settings():
	"""The singleton, cached. Never raises if the doctype is missing."""
	try:
		return frappe.get_cached_doc(DOCTYPE)
	except Exception:
		return frappe._dict(enabled=0, server_url="", api_key="", sync_interval_hours=6)


def api_secret() -> str:
	"""Decrypt the stored secret. Password fieldtype, so it lives in __Auth."""
	try:
		return settings().get_password("api_secret", raise_exception=False) or ""
	except Exception:
		return ""


def is_configured() -> bool:
	cfg = settings()
	return bool(
		cfg.get("enabled")
		and (cfg.get("server_url") or "").strip()
		and (cfg.get("api_key") or "").strip()
		and api_secret()
	)


def call(method: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
	"""Call a whitelisted method on the ERPNext bench.

	Returns {"ok": bool, "data": Any | None, "error": str | None} and never
	raises. Frappe wraps whitelisted return values in a "message" envelope;
	that is unwrapped here so callers never have to know about it.
	"""
	if not is_configured():
		return {"ok": False, "data": None, "error": "ERPNext sync is not configured"}

	cfg = settings()
	base = (cfg.get("server_url") or "").rstrip("/")
	url = f"{base}/api/method/{method}"

	try:
		resp = session.get(
			url,
			headers={
				"Authorization": f"token {cfg.get('api_key')}:{api_secret()}",
				"Accept": "application/json",
			},
			params=params or {},
			timeout=timeout,
		)
	except Exception as exc:
		return {"ok": False, "data": None, "error": f"{type(exc).__name__}: {exc}"}

	if not resp.ok:
		# Truncated: an ERPNext error page can be a full HTML document, and this
		# string ends up in an Error Log and a UI toast.
		return {
			"ok": False,
			"data": None,
			"error": f"HTTP {resp.status_code}: {(resp.text or '')[:300]}",
		}

	try:
		payload = resp.json()
	except Exception as exc:
		return {"ok": False, "data": None, "error": f"Bad JSON from ERPNext: {exc}"}

	data = payload.get("message") if isinstance(payload, dict) else payload
	return {"ok": True, "data": data, "error": None}


@frappe.whitelist()
def test_connection() -> dict:
	"""Prove the wire works, from the settings screen.

	Asks for a single customer: cheap, read-only, and it exercises the whole
	path — URL, credentials, the Server Script being installed, and permissions.
	"""
	frappe.only_for(["System Manager", "Administrator"])

	result = call("helpdesk_customer_sync", {"limit": 1})
	if not result["ok"]:
		return result

	data = result["data"] or {}
	return {
		"ok": True,
		"error": None,
		"data": {
			"version": data.get("version"),
			"synced_at": data.get("synced_at"),
			"sample_count": len(data.get("customers") or []),
		},
	}
