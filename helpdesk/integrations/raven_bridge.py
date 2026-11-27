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


def _as_raven_user(fn, user=None, **kwargs):
	s = frappe.get_cached_doc(SETTINGS)
	if not available() or ("channel_id" in kwargs and not kwargs["channel_id"]):
		raise RuntimeError("Raven is not installed or no Raven channel is set")
	previous = frappe.session.user
	frappe.set_user(user or s.raven_user or "Administrator")
	try:
		return frappe.call(fn, **kwargs)
	finally:
		frappe.set_user(previous)


def create_poll(
	question: str, channel: str | None = None, user: str | None = None, options: tuple = ("Send", "Skip")
) -> tuple[str, str | None]:
	"""Post a poll (Send / Skip by default). Returns (poll_id, message_id)."""
	s = frappe.get_cached_doc(SETTINGS)
	poll_id = _as_raven_user(
		"raven.api.raven_poll.create_poll",
		user=user,
		channel_id=channel or s.raven_channel,
		question=question,
		options=[{"option": o} for o in options],
		is_multi_choice=0,
		is_anonymous=0,
	)
	message_id = frappe.db.get_value("Raven Message", {"poll_id": poll_id}, "name")
	return poll_id, message_id


def votes(message_id: str, user: str | None = None) -> dict[str, list[str]]:
	"""{option text: [voter emails, lower-cased]}."""
	res = _as_raven_user("raven.api.raven_poll.get_poll", user=user, message_id=message_id) or {}
	text = {o["name"]: o["option"] for o in (res.get("poll") or {}).get("options") or []}
	return {text.get(k, k): [u.lower() for u in v] for k, v in (res.get("votes") or {}).items()}


def close_poll(poll_id: str, user: str | None = None) -> None:
	try:
		_as_raven_user("raven.api.raven_poll.close_poll", user=user, poll_id=poll_id)
	except Exception as e:
		if "already closed" not in str(e):
			raise


def post(text: str, channel: str | None = None, user: str | None = None) -> None:
	"""Post to `channel` as `user`; both default to the payment reminder settings."""
	s = frappe.get_cached_doc(SETTINGS)
	_as_raven_user(
		"raven.api.raven_message.send_message", user=user, channel_id=channel or s.raven_channel, text=text
	)
