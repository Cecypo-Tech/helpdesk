"""The four Raven calls payment reminders need, in one place.

Raven is installed on support.cecypo.tech but not on dev or test, so every
call is guarded and the tests mock this module. The calls mirror
cecypo_erp_agent/raven.py, which drives the same Raven API over HTTP; here it
runs in-process as the configured `raven_user`.
"""

import frappe

SETTINGS = "HD Payment Reminder Settings"


def available() -> bool:
	return bool(frappe.db.exists("DocType", "Raven Message"))


def _as_raven_user(fn, **kwargs):
	s = frappe.get_cached_doc(SETTINGS)
	if not (available() and s.raven_channel):
		raise RuntimeError("Raven is not installed or no Raven channel is set")
	previous = frappe.session.user
	frappe.set_user(s.raven_user or "Administrator")
	try:
		return frappe.call(fn, **kwargs)
	finally:
		frappe.set_user(previous)


def create_poll(question: str) -> tuple[str, str | None]:
	"""Post a Send / Skip poll. Returns (poll_id, message_id)."""
	s = frappe.get_cached_doc(SETTINGS)
	poll_id = _as_raven_user(
		"raven.api.raven_poll.create_poll",
		channel_id=s.raven_channel,
		question=question,
		options=[{"option": "Send"}, {"option": "Skip"}],
		is_multi_choice=0,
		is_anonymous=0,
	)
	message_id = frappe.db.get_value("Raven Message", {"poll_id": poll_id}, "name")
	return poll_id, message_id


def votes(message_id: str) -> dict[str, list[str]]:
	"""{option text: [voter emails, lower-cased]}."""
	res = _as_raven_user("raven.api.raven_poll.get_poll", message_id=message_id) or {}
	text = {o["name"]: o["option"] for o in (res.get("poll") or {}).get("options") or []}
	return {text.get(k, k): [u.lower() for u in v] for k, v in (res.get("votes") or {}).items()}


def close_poll(poll_id: str) -> None:
	try:
		_as_raven_user("raven.api.raven_poll.close_poll", poll_id=poll_id)
	except Exception as e:
		if "already closed" not in str(e):
			raise


def post(text: str) -> None:
	s = frappe.get_cached_doc(SETTINGS)
	_as_raven_user("raven.api.raven_message.send_message", channel_id=s.raven_channel, text=text)
