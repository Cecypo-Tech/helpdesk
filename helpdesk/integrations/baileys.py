import json
import re

import frappe
import requests as _requests
from frappe import _
from frappe.utils import now_datetime, time_diff_in_hours


# ── Helpers ───────────────────────────────────────────────────────────────────

def _settings():
	return frappe.get_cached_doc("Baileys Gateway Settings")


def _normalize_phone(number: str) -> str:
	return re.sub(r"[^\d]", "", number or "")


def _phone_from_jid(jid: str) -> str:
	"""Extract the phone number from an individual JID like 254712345678@s.whatsapp.net."""
	return _normalize_phone(jid.split("@")[0])


def _is_group(jid: str) -> bool:
	return jid.endswith("@g.us")


def _set_ticket_status(ticket_name: str, status_name: str) -> None:
	if not status_name or not frappe.db.exists("HD Ticket Status", status_name):
		return
	try:
		frappe.db.set_value("HD Ticket", ticket_name, "status", status_name, update_modified=True)
		frappe.db.commit()
	except Exception:
		pass


def _agent_initials() -> str:
	full_name = frappe.db.get_value("User", frappe.session.user, "full_name") or ""
	parts = full_name.strip().split()
	if len(parts) >= 2:
		return parts[0][0].upper() + parts[-1][0].upper()
	return (parts[0][0].upper() if parts else frappe.session.user[:2].upper())


def _publish_event(ticket_name: str, is_incoming: bool) -> None:
	frappe.db.commit()
	frappe.publish_realtime(
		"helpdesk:whatsapp-message",
		message={"ticket": str(ticket_name), "is_incoming": is_incoming},
	)


def _publish_baileys_event(jid: str, is_incoming: bool) -> None:
	frappe.db.commit()
	frappe.publish_realtime(
		"helpdesk:baileys-message",
		message={"jid": jid, "is_incoming": is_incoming},
	)


def _notify_agents_baileys(jid: str, message_text: str, sender_name: str) -> None:
	"""Publish bell notification for standalone WhatsApp chat."""
	settings = _settings()
	quiet_minutes = int(settings.notification_quiet_minutes or 0)
	if quiet_minutes > 0:
		last_outgoing = frappe.get_all(
			"Baileys Message",
			filters={"jid": jid, "direction": "Outgoing"},
			fields=["creation"],
			order_by="creation desc",
			limit=1,
		)
		if last_outgoing:
			minutes_since = time_diff_in_hours(now_datetime(), last_outgoing[0].creation) * 60
			if minutes_since < quiet_minutes:
				return

	frappe.publish_realtime(
		"helpdesk:baileys-notification",
		message={"jid": jid, "message": (message_text or "")[:80], "sender": sender_name},
	)


# ── Ticket routing ────────────────────────────────────────────────────────────

def _is_blocked(jid: str, sender: str, settings) -> bool:
	"""Return True if the jid or sender phone is on the blocklist."""
	blocked = settings.get("blocked_jids") or []
	if not blocked:
		return False
	phone = _phone_from_jid(sender or jid)
	for row in blocked:
		entry = (row.jid or "").strip()
		if not entry:
			continue
		if entry == jid or entry == sender:
			return True
		if _normalize_phone(entry) == phone:
			return True
	return False


def _group_label(jid: str, settings) -> str:
	"""Return the configured group name for a JID, or a formatted fallback."""
	for row in (settings.group_jids or []):
		if row.jid == jid:
			return row.group_name or jid
	return jid


def _upsert_contact_name(jid: str, sender_name: str) -> None:
	"""Silently record sender_name in Baileys Contact if no custom override exists."""
	if not jid or not sender_name or _is_group(jid):
		return
	try:
		if frappe.db.exists("Baileys Contact", {"jid": jid}):
			existing = frappe.db.get_value("Baileys Contact", {"jid": jid}, "custom_name")
			if existing:
				return
			frappe.db.set_value(
				"Baileys Contact", {"jid": jid}, "custom_name", sender_name, update_modified=False
			)
		else:
			frappe.get_doc({
				"doctype": "Baileys Contact",
				"jid": jid,
				"phone": _phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else "",
				"custom_name": sender_name,
				"company": "",
				"assigned_team": "",
			}).insert(ignore_permissions=True)
	except Exception:
		pass


# ── Webhook ───────────────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def webhook():
	"""Receive incoming messages from the Baileys gateway (standalone mode — no ticket creation)."""
	if not frappe.db.exists("DocType", "Baileys Gateway Settings"):
		frappe.response["http_status_code"] = 503
		return {"error": "Baileys Gateway Settings not configured"}

	settings = _settings()
	if not settings.enabled:
		return {"status": "disabled"}

	api_key = frappe.get_request_header("X-API-Key") or frappe.get_request_header("x-api-key")
	stored_key = settings.api_key or ""
	if not stored_key or api_key != stored_key:
		frappe.response["http_status_code"] = 401
		return {"error": "Unauthorized"}

	try:
		raw_body = frappe.request.data.decode("utf-8")
		payload = frappe.parse_json(raw_body)
	except Exception:
		frappe.response["http_status_code"] = 400
		return {"error": "Invalid JSON"}

	jid = payload.get("jid", "")
	message_id = payload.get("messageId", "")
	sender = payload.get("sender", jid)
	sender_name = payload.get("senderName") or sender.split("@")[0]
	message = payload.get("message", "")
	content_type = payload.get("contentType", "text")
	media_url = payload.get("mediaUrl") or ""
	quoted_message_id = payload.get("quotedMessageId") or ""

	if not jid:
		return {"status": "skipped", "reason": "no jid"}

	# Ignore WhatsApp Stories/Status updates — these are not real conversations
	if jid == "status@broadcast" or jid.endswith("@broadcast"):
		return {"status": "skipped", "reason": "status broadcast"}

	if _is_blocked(jid, sender, settings):
		return {"status": "blocked"}

	if message_id and frappe.db.exists("Baileys Message", {"message_id": message_id}):
		# Backfill media_url if the stored record has none but we now have one
		if media_url:
			existing_name = frappe.db.get_value("Baileys Message", {"message_id": message_id}, "name")
			if existing_name and not frappe.db.get_value("Baileys Message", existing_name, "media_url"):
				frappe.set_user("Administrator")
				frappe.db.set_value("Baileys Message", existing_name, "media_url", media_url)
				_publish_baileys_event(jid, is_incoming=True)
		return {"status": "duplicate"}

	frappe.set_user("Administrator")

	frappe.get_doc({
		"doctype": "Baileys Message",
		"direction": "Incoming",
		"jid": jid,
		"sender_jid": sender,
		"sender_name": sender_name,
		"profile_name": sender_name,
		"message": message,
		"content_type": content_type or "text",
		"media_url": media_url,
		"message_id": message_id,
		"reply_to_message_id": quoted_message_id,
		"status": "Delivered",
		"reference_doctype": "",
		"reference_name": "",
	}).insert(ignore_permissions=True)

	_upsert_contact_name(jid, sender_name)
	_publish_baileys_event(jid, is_incoming=True)
	if content_type != "reaction":
		_notify_agents_baileys(jid, message, sender_name)

	return {"status": "ok"}


@frappe.whitelist(allow_guest=True)
def upload_baileys_media():
	"""Accept a base64-encoded media file from the Baileys gateway and return its public URL."""
	import base64

	if not frappe.db.exists("DocType", "Baileys Gateway Settings"):
		frappe.response["http_status_code"] = 503
		return {"error": "not configured"}

	settings = _settings()
	api_key = frappe.get_request_header("X-API-Key") or frappe.get_request_header("x-api-key")
	if not settings.api_key or api_key != settings.api_key:
		frappe.response["http_status_code"] = 401
		return {"error": "Unauthorized"}

	try:
		payload = frappe.parse_json(frappe.request.data.decode("utf-8"))
	except Exception:
		frappe.response["http_status_code"] = 400
		return {"error": "Invalid JSON"}

	filename = payload.get("filename") or "wa_media"
	content_b64 = payload.get("content_b64") or ""
	if not content_b64:
		frappe.response["http_status_code"] = 400
		return {"error": "No content"}

	frappe.set_user("Administrator")
	content = base64.b64decode(content_b64)

	f = frappe.get_doc({
		"doctype": "File",
		"file_name": filename,
		"is_private": 0,
		"content": content,
	})
	f.save(ignore_permissions=True)
	return {"file_url": f.file_url}


@frappe.whitelist(allow_guest=True)
def status_webhook():
	"""Receive delivery status updates (Sent→Delivered→Read) from the Baileys gateway."""
	if not frappe.db.exists("DocType", "Baileys Gateway Settings"):
		frappe.response["http_status_code"] = 503
		return {"error": "not configured"}

	settings = _settings()
	if not settings.enabled:
		return {"status": "disabled"}

	api_key = frappe.get_request_header("X-API-Key") or frappe.get_request_header("x-api-key")
	stored_key = settings.api_key or ""
	if not stored_key or api_key != stored_key:
		frappe.response["http_status_code"] = 401
		return {"error": "Unauthorized"}

	try:
		payload = frappe.parse_json(frappe.request.data.decode("utf-8"))
	except Exception:
		frappe.response["http_status_code"] = 400
		return {"error": "Invalid JSON"}

	message_id = payload.get("messageId") or ""
	raw_status = (payload.get("status") or "").lower()
	status_map = {"sent": "Sent", "delivered": "Delivered", "read": "Read", "failed": "Failed"}
	status = status_map.get(raw_status)

	if not message_id or not status:
		return {"status": "skipped", "reason": "missing messageId or status"}

	msg = frappe.db.get_value(
		"Baileys Message",
		{"message_id": message_id},
		["name", "jid", "reference_doctype", "reference_name"],
		as_dict=True,
	)
	if not msg:
		return {"status": "skipped", "reason": "message not found"}

	frappe.set_user("Administrator")
	frappe.db.set_value("Baileys Message", msg.name, "status", status, update_modified=False)
	frappe.db.commit()

	frappe.publish_realtime(
		"helpdesk:baileys-status-update",
		message={"message_id": message_id, "status": status, "jid": msg.jid},
		after_commit=True,
	)

	return {"status": "ok"}


# ── Agent send ────────────────────────────────────────────────────────────────

@frappe.whitelist()
def send_baileys_reply(
	ticket: str = None,
	jid: str = None,
	message: str = "",
	content_type: str = "text",
	media_url: str | None = None,
	reply_to_message_id: str | None = None,
	reply_to_text: str | None = None,
	reply_to_from_me: bool = False,
) -> dict:
	"""Send a text (or media) reply via the Baileys gateway."""
	settings = _settings()
	if not settings.enabled:
		frappe.throw(_("Baileys gateway is not enabled."))

	if not jid and ticket:
		jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	if not jid:
		frappe.throw(_("No WhatsApp JID provided."))

	if settings.append_agent_initials:
		agent_suffix = f"\n^{_agent_initials()}"
		full_message = f"{message}{agent_suffix}" if message else agent_suffix.strip()
	else:
		full_message = message or ""

	gateway_url = (settings.gateway_url or "").rstrip("/")
	api_key = settings.api_key or ""

	payload: dict = {
		"sessionName": settings.session_name or "helpdesk",
		"jid": jid,
		"message": full_message,
		"contentType": content_type,
	}
	if media_url:
		payload["mediaUrl"] = media_url
	if reply_to_message_id:
		payload["replyToMessageId"] = reply_to_message_id
		payload["replyToText"] = reply_to_text or ""
		payload["replyToFromMe"] = bool(reply_to_from_me)

	try:
		resp = _requests.post(
			f"{gateway_url}/send",
			json=payload,
			headers={"X-API-Key": api_key, "Content-Type": "application/json"},
			timeout=15,
		)
		resp.raise_for_status()
		sent_id = resp.json().get("messageId", "")
	except Exception as e:
		frappe.throw(_("Baileys gateway send failed: {0}").format(str(e)))

	msg_doc = frappe.get_doc({
		"doctype": "Baileys Message",
		"direction": "Outgoing",
		"jid": jid,
		"sender_jid": "",
		"sender_name": frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user,
		"profile_name": "",
		"message": full_message,
		"content_type": content_type,
		"media_url": media_url or "",
		"message_id": sent_id,
		"reply_to_message_id": reply_to_message_id or "",
		"status": "Sent",
		"reference_doctype": "HD Ticket" if ticket else "",
		"reference_name": ticket or "",
	})
	msg_doc.insert(ignore_permissions=True)

	if ticket:
		assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
		if not frappe.parse_json(assign_json):
			try:
				frappe.get_doc("HD Ticket", ticket).assign_agent(frappe.session.user)
			except Exception:
				pass
		if settings.agent_reply_status:
			_set_ticket_status(ticket, settings.agent_reply_status)
		_publish_event(ticket, is_incoming=False)
	else:
		_publish_baileys_event(jid, is_incoming=False)

	return {"name": msg_doc.name, "message_id": sent_id, "status": "Sent"}


@frappe.whitelist(allow_guest=False)
def send_baileys_media(ticket: str = None, jid: str = None, message: str = "", content_type: str = "document") -> dict:
	"""Upload a file to Frappe storage and send its public URL via the gateway."""
	file_obj = frappe.request.files.get("file")
	if not file_obj:
		frappe.throw(_("No file provided."))

	filename = file_obj.filename or "attachment"
	mime_type = file_obj.content_type or "application/octet-stream"
	file_data = file_obj.read()

	if mime_type.startswith("image/"):
		content_type = "image"
	elif mime_type.startswith("video/"):
		content_type = "video"
	elif mime_type.startswith("audio/"):
		content_type = "audio"
	else:
		content_type = "document"

	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": filename,
		"content": file_data,
		"is_private": 0,
	})
	file_doc.insert(ignore_permissions=True)
	public_url = frappe.utils.get_url(file_doc.file_url)

	return send_baileys_reply(
		ticket=ticket,
		jid=jid,
		message=message,
		content_type=content_type,
		media_url=public_url,
	)


@frappe.whitelist()
def send_baileys_reaction(
	ticket: str = None,
	jid: str = None,
	target_message_id: str = "",
	emoji: str = "",
) -> dict:
	"""Send an emoji reaction to a specific Baileys message."""
	settings = _settings()
	if not settings.enabled:
		frappe.throw(_("Baileys gateway is not enabled."))

	if not jid and ticket:
		jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	if not jid:
		frappe.throw(_("No WhatsApp JID provided."))

	gateway_url = (settings.gateway_url or "").rstrip("/")
	api_key = settings.api_key or ""
	target_direction = frappe.db.get_value("Baileys Message", {"message_id": target_message_id}, "direction") or "Incoming"

	try:
		resp = _requests.post(
			f"{gateway_url}/react",
			json={
				"sessionName": settings.session_name or "helpdesk",
				"jid": jid,
				"messageId": target_message_id,
				"emoji": emoji,
				"fromMe": target_direction == "Outgoing",
			},
			headers={"X-API-Key": api_key, "Content-Type": "application/json"},
			timeout=10,
		)
		resp.raise_for_status()
	except Exception as e:
		frappe.throw(_("Baileys reaction failed: {0}").format(str(e)))

	frappe.get_doc({
		"doctype": "Baileys Message",
		"direction": "Outgoing",
		"jid": jid,
		"sender_jid": "",
		"sender_name": frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user,
		"profile_name": "",
		"message": emoji,
		"content_type": "reaction",
		"media_url": "",
		"message_id": "",
		"reply_to_message_id": target_message_id,
		"status": "Sent",
		"reference_doctype": "HD Ticket" if ticket else "",
		"reference_name": ticket or "",
	}).insert(ignore_permissions=True)

	if ticket:
		_publish_event(ticket, is_incoming=False)
	else:
		_publish_baileys_event(jid, is_incoming=False)

	return {}


# ── Queries ───────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_baileys_messages(jid: str = None, ticket: str = None) -> list[dict]:
	"""Return messages for a conversation. Accepts jid directly or ticket name (backward compat)."""
	from frappe.query_builder import DocType

	if not jid and ticket:
		jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	if not jid:
		return []

	BM = DocType("Baileys Message")
	User = DocType("User")

	rows = (
		frappe.qb.from_(BM)
		.left_join(User).on(User.name == BM.owner)
		.select(
			BM.name, BM.creation, BM.direction, BM.jid, BM.message,
			BM.content_type, BM.media_url, BM.sender_jid, BM.sender_name,
			BM.profile_name, BM.message_id, BM.reply_to_message_id, BM.status, BM.owner,
			User.full_name.as_("sender_full_name"),
		)
		.where(BM.jid == jid)
		.orderby(BM.creation)
		.run(as_dict=True)
	)

	for m in rows:
		if m.get("creation") and not isinstance(m["creation"], str):
			m["creation"] = str(m["creation"])
		m["type"] = "Outgoing" if m["direction"] == "Outgoing" else "Incoming"
		m["attach"] = m.get("media_url") or ""
		m["is_reply"] = 1 if (m.get("reply_to_message_id") and m.get("content_type") != "reaction") else 0

	return rows


@frappe.whitelist()
def get_ticket_baileys_info(ticket: str) -> dict:
	"""Return Baileys-specific metadata for the frontend tab."""
	jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	if not jid:
		return {"has_baileys": False}

	settings = _settings()
	is_grp = _is_group(jid)

	group_name = None
	if is_grp:
		group_name = _group_label(jid, settings)

	assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
	assigned_users = frappe.parse_json(assign_json) or []

	return {
		"has_baileys": True,
		"jid": jid,
		"is_group": is_grp,
		"group_name": group_name,
		"is_assigned": frappe.session.user in assigned_users,
		"assignees": assigned_users,
		"reply_window_open": True,  # no 24h restriction with Baileys
	}


@frappe.whitelist()
def mark_baileys_messages_read(ticket: str = None, jid: str = None) -> int:
	"""Mark unread incoming Baileys Messages as read, send read receipts to gateway."""
	settings = _settings()

	if not jid and ticket:
		jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	if not jid:
		return 0

	filters: dict = {"jid": jid, "direction": "Incoming", "status": ["not in", ["Read"]]}
	if ticket:
		filters["reference_doctype"] = "HD Ticket"
		filters["reference_name"] = ticket

	unread = frappe.get_all("Baileys Message", filters=filters, fields=["name", "message_id"])

	if not unread:
		return 0

	message_ids = [r.message_id for r in unread if r.message_id]

	if message_ids and settings.enabled and settings.gateway_url:
		gateway_url = (settings.gateway_url or "").rstrip("/")
		api_key = settings.api_key or ""
		try:
			_requests.post(
				f"{gateway_url}/markRead",
				json={"sessionName": settings.session_name or "helpdesk", "jid": jid, "messageIds": message_ids},
				headers={"X-API-Key": api_key},
				timeout=5,
			)
		except Exception:
			pass

	for row in unread:
		frappe.db.set_value("Baileys Message", row.name, "status", "Read", update_modified=False)

	if ticket:
		try:
			for notif in frappe.get_all(
				"HD Notification",
				filters={"user_to": frappe.session.user, "reference_ticket": ticket, "notification_type": "WhatsApp", "read": 0},
				pluck="name",
			):
				frappe.db.set_value("HD Notification", notif, "read", 1, update_modified=False)
		except Exception:
			pass

	return len(unread)


@frappe.whitelist()
def get_gateway_status() -> dict:
	"""Proxy the gateway /health endpoint so the form JS can poll without CORS issues."""
	settings = _settings()
	if not settings.enabled or not settings.gateway_url:
		return {"connected": False, "error": "Gateway not configured or disabled"}
	try:
		resp = _requests.get(
			f"{settings.gateway_url.rstrip('/')}/health",
			headers={"X-API-Key": settings.api_key or ""},
			timeout=5,
		)
		return resp.json()
	except Exception as e:
		return {"connected": False, "error": str(e)}


@frappe.whitelist()
def fetch_gateway_groups() -> list:
	"""Fetch the list of WhatsApp groups from the gateway and return [{jid, subject, size}]."""
	settings = _settings()
	if not settings.enabled or not settings.gateway_url:
		frappe.throw(_("Gateway not configured or disabled"))
	try:
		resp = _requests.get(
			f"{settings.gateway_url.rstrip('/')}/groups",
			headers={"X-API-Key": settings.api_key or ""},
			timeout=15,
		)
		resp.raise_for_status()
		return resp.json().get("groups", [])
	except Exception as e:
		frappe.throw(_("Failed to fetch groups from gateway: {0}").format(str(e)))


@frappe.whitelist()
def get_connected_phone() -> dict:
	"""Return phone number of the connected WhatsApp account from the gateway /health endpoint."""
	settings = _settings()
	if not settings.enabled or not settings.gateway_url:
		return {"phone": None, "connected": False}
	try:
		resp = _requests.get(
			f"{settings.gateway_url.rstrip('/')}/health",
			headers={"X-API-Key": settings.api_key or ""},
			timeout=5,
		)
		data = resp.json()
		return {"phone": data.get("phone"), "connected": data.get("connected", False)}
	except Exception:
		return {"phone": None, "connected": False}


@frappe.whitelist()
def get_baileys_conversations() -> list[dict]:
	"""Return one entry per unique JID sorted by most-recent message first."""
	from frappe.query_builder import DocType
	from frappe.query_builder.functions import Max

	BM = DocType("Baileys Message")

	latest = (
		frappe.qb.from_(BM)
		.select(BM.jid, Max(BM.creation).as_("latest_creation"))
		.where(~BM.jid.like("%@broadcast"))
		.groupby(BM.jid)
	)

	BM2 = DocType("Baileys Message")
	rows = (
		frappe.qb.from_(BM2)
		.join(latest).on(
			(BM2.jid == latest.jid) & (BM2.creation == latest.latest_creation)
		)
		.select(
			BM2.jid, BM2.sender_name, BM2.message,
			BM2.content_type, BM2.direction, BM2.creation,
		)
		.orderby(BM2.creation, order=frappe.qb.desc)
		.run(as_dict=True)
	)

	seen: set[str] = set()
	deduped = []
	for r in rows:
		if r.jid and r.jid not in seen:
			seen.add(r.jid)
			deduped.append(r)

	settings = _settings()
	group_names = {row.jid: (row.group_name or row.jid) for row in (settings.group_jids or [])}

	jids = [r.jid for r in deduped]
	contacts: dict[str, dict] = {}
	if jids and frappe.db.exists("DocType", "Baileys Contact"):
		for c in frappe.get_all(
			"Baileys Contact",
			filters={"jid": ["in", jids]},
			fields=["jid", "custom_name", "company", "assigned_team", "phone"],
		):
			contacts[c.jid] = c

	restrict = settings.get("restrict_chats_by_team")
	user_teams: set[str] = set()
	user_has_any_team = False
	if restrict:
		tm_rows = frappe.get_all("HD Team Member", filters={"user": frappe.session.user}, pluck="parent")
		user_teams = set(tm_rows)
		user_has_any_team = bool(user_teams)

	result = []
	for r in deduped:
		jid = r.jid
		is_grp = _is_group(jid)
		contact = contacts.get(jid, {})
		assigned_team = contact.get("assigned_team") or ""

		if restrict and user_has_any_team and assigned_team and assigned_team not in user_teams:
			continue

		display_name = (
			contact.get("custom_name")
			or (group_names.get(jid) if is_grp else None)
			or r.get("sender_name")
			or jid.split("@")[0]
		)
		result.append({
			"jid": jid,
			"display_name": display_name or jid,
			"company": contact.get("company") or "",
			"assigned_team": assigned_team,
			"phone": contact.get("phone") or (_phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else ""),
			"is_group": is_grp,
			"last_message": r.get("message") or f"[{r.get('content_type', 'media')}]",
			"last_message_time": str(r["creation"]),
			"last_direction": r.get("direction", "Incoming"),
			"content_type": r.get("content_type", "text"),
		})

	return result


@frappe.whitelist()
def save_baileys_contact(jid: str, custom_name: str = "", company: str = "", assigned_team: str = "", phone: str = "") -> dict:
	"""Create or update a Baileys Contact override for a JID."""
	custom_name = (custom_name or "").strip()
	company = (company or "").strip()
	assigned_team = (assigned_team or "").strip()
	phone = _normalize_phone(phone or "")

	if frappe.db.exists("Baileys Contact", {"jid": jid}):
		doc = frappe.get_doc("Baileys Contact", {"jid": jid})
		doc.custom_name = custom_name
		doc.company = company
		doc.assigned_team = assigned_team
		if phone:
			doc.phone = phone
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc({
			"doctype": "Baileys Contact",
			"jid": jid,
			"phone": phone or (_phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else ""),
			"custom_name": custom_name,
			"company": company,
			"assigned_team": assigned_team,
		}).insert(ignore_permissions=True)

	return {"status": "ok", "jid": jid, "custom_name": custom_name, "company": company, "phone": phone, "assigned_team": assigned_team}


@frappe.whitelist()
def get_baileys_contact(jid: str) -> dict:
	"""Return stored contact overrides for a JID, or empty defaults."""
	if not frappe.db.exists("DocType", "Baileys Contact"):
		return {"jid": jid, "custom_name": "", "company": ""}
	row = frappe.db.get_value("Baileys Contact", {"jid": jid}, ["custom_name", "company", "assigned_team"], as_dict=True)
	return {
		"jid": jid,
		"custom_name": (row or {}).get("custom_name") or "",
		"company": (row or {}).get("company") or "",
		"assigned_team": (row or {}).get("assigned_team") or "",
	}


@frappe.whitelist()
def get_hd_teams() -> list[dict]:
	"""Return all HD Teams for the team assignment dropdown."""
	return frappe.get_all("HD Team", fields=["name"], order_by="name asc")


@frappe.whitelist()
def search_baileys_contacts(query: str = "") -> list[dict]:
	"""Search Baileys Contacts and standard Contacts by name/phone. Returns up to 30 matches."""
	q = (query or "").strip()
	like = f"%{q}%"

	# 1. Baileys Contacts (have a known JID / chat history)
	if q:
		baileys_rows = frappe.db.sql(
			"""
			SELECT jid, custom_name, phone, company
			FROM `tabBaileys Contact`
			WHERE jid NOT LIKE '%%@broadcast'
			  AND (custom_name LIKE %s OR phone LIKE %s OR company LIKE %s)
			ORDER BY custom_name ASC
			LIMIT 30
			""",
			(like, like, like),
			as_dict=True,
		)
	else:
		baileys_rows = frappe.get_all(
			"Baileys Contact",
			filters=[["jid", "not like", "%@broadcast"]],
			fields=["jid", "custom_name", "phone", "company"],
			order_by="custom_name asc",
			limit=30,
		)

	seen_phones: set[str] = {_normalize_phone(r.phone) for r in baileys_rows if r.phone}
	result = list(baileys_rows)

	# 2. Standard Frappe Contacts with a mobile number
	if q:
		frappe_rows = frappe.db.sql(
			"""
			SELECT c.full_name, c.mobile_no, c.company_name
			FROM `tabContact` c
			WHERE c.mobile_no IS NOT NULL AND c.mobile_no != ''
			  AND (c.full_name LIKE %s OR c.mobile_no LIKE %s OR c.company_name LIKE %s)
			ORDER BY c.full_name ASC
			LIMIT 50
			""",
			(like, like, like),
			as_dict=True,
		)
	else:
		frappe_rows = frappe.db.sql(
			"""
			SELECT c.full_name, c.mobile_no, c.company_name
			FROM `tabContact` c
			WHERE c.mobile_no IS NOT NULL AND c.mobile_no != ''
			ORDER BY c.full_name ASC
			LIMIT 50
			""",
			as_dict=True,
		)

	for r in frappe_rows:
		phone = _normalize_phone(r.mobile_no)
		if not phone or phone in seen_phones:
			continue
		seen_phones.add(phone)
		result.append({
			"jid": f"{phone}@s.whatsapp.net",
			"custom_name": r.full_name or "",
			"phone": phone,
			"company": r.company_name or "",
		})
		if len(result) >= 30:
			break

	return result


@frappe.whitelist()
def get_baileys_analytics(from_date: str = None, to_date: str = None) -> dict:
	"""Return WhatsApp analytics for the given date range."""
	from collections import defaultdict
	from frappe.utils import add_days, today

	if not from_date:
		from_date = add_days(today(), -30)
	if not to_date:
		to_date = today()

	from_dt = f"{from_date} 00:00:00"
	to_dt = f"{to_date} 23:59:59"

	# Summary counts
	summary = frappe.db.sql(
		"""
		SELECT
			COUNT(*) as total,
			SUM(direction = 'Incoming') as incoming,
			SUM(direction = 'Outgoing') as outgoing,
			COUNT(DISTINCT jid) as conversations
		FROM `tabBaileys Message`
		WHERE creation BETWEEN %s AND %s
		  AND content_type != 'reaction'
		""",
		(from_dt, to_dt),
		as_dict=True,
	)[0]

	# Messages per day
	daily = frappe.db.sql(
		"""
		SELECT
			DATE(creation) as date,
			COUNT(*) as total,
			SUM(direction = 'Incoming') as incoming,
			SUM(direction = 'Outgoing') as outgoing
		FROM `tabBaileys Message`
		WHERE creation BETWEEN %s AND %s
		  AND content_type != 'reaction'
		GROUP BY DATE(creation)
		ORDER BY date ASC
		""",
		(from_dt, to_dt),
		as_dict=True,
	)

	# Messages by hour of day
	hourly = frappe.db.sql(
		"""
		SELECT HOUR(creation) as hour, COUNT(*) as total
		FROM `tabBaileys Message`
		WHERE creation BETWEEN %s AND %s
		  AND content_type != 'reaction'
		GROUP BY HOUR(creation)
		ORDER BY hour ASC
		""",
		(from_dt, to_dt),
		as_dict=True,
	)
	hourly_map = {r.hour: r.total for r in hourly}
	hourly_full = [{"hour": h, "total": hourly_map.get(h, 0)} for h in range(24)]

	# Top active contacts/groups
	top_raw = frappe.db.sql(
		"""
		SELECT
			jid,
			COUNT(*) as total,
			SUM(direction = 'Incoming') as incoming,
			SUM(direction = 'Outgoing') as outgoing,
			MAX(sender_name) as sender_name
		FROM `tabBaileys Message`
		WHERE creation BETWEEN %s AND %s
		  AND jid NOT LIKE '%%@broadcast'
		  AND content_type != 'reaction'
		GROUP BY jid
		ORDER BY total DESC
		LIMIT 15
		""",
		(from_dt, to_dt),
		as_dict=True,
	)

	jids = [r.jid for r in top_raw]
	contacts: dict = {}
	if jids and frappe.db.exists("DocType", "Baileys Contact"):
		for c in frappe.get_all(
			"Baileys Contact",
			filters={"jid": ["in", jids]},
			fields=["jid", "custom_name", "company"],
		):
			contacts[c.jid] = c

	settings = _settings()
	group_names = {row.jid: (row.group_name or row.jid) for row in (settings.group_jids or [])}

	top_contacts = []
	for r in top_raw:
		contact = contacts.get(r.jid, {})
		is_grp = _is_group(r.jid)
		display_name = (
			contact.get("custom_name")
			or (group_names.get(r.jid) if is_grp else None)
			or r.get("sender_name")
			or r.jid.split("@")[0]
		)
		top_contacts.append({
			"jid": r.jid,
			"display_name": display_name,
			"company": contact.get("company") or "",
			"is_group": is_grp,
			"total": r.total,
			"incoming": r.incoming or 0,
			"outgoing": r.outgoing or 0,
		})

	# Agent response times
	# For each outgoing message, find the most recent incoming before it (same JID, within 24h)
	raw_replies = frappe.db.sql(
		"""
		SELECT
			bm_out.owner AS agent_user,
			bm_out.sender_name AS agent_name,
			TIMESTAMPDIFF(MINUTE, bm_in.creation, bm_out.creation) AS response_minutes
		FROM `tabBaileys Message` bm_out
		INNER JOIN `tabBaileys Message` bm_in ON (
			bm_in.jid = bm_out.jid
			AND bm_in.direction = 'Incoming'
			AND bm_in.content_type != 'reaction'
			AND bm_in.creation = (
				SELECT MAX(b2.creation)
				FROM `tabBaileys Message` b2
				WHERE b2.jid = bm_out.jid
				  AND b2.direction = 'Incoming'
				  AND b2.content_type != 'reaction'
				  AND b2.creation < bm_out.creation
			)
		)
		WHERE bm_out.direction = 'Outgoing'
		  AND bm_out.content_type NOT IN ('reaction')
		  AND bm_out.creation BETWEEN %s AND %s
		  AND TIMESTAMPDIFF(MINUTE, bm_in.creation, bm_out.creation) BETWEEN 0 AND 1440
		""",
		(from_dt, to_dt),
		as_dict=True,
	)

	agent_map: dict = defaultdict(lambda: {"replies": 0, "total_minutes": 0, "lt5": 0, "lt30": 0, "lt120": 0, "gt120": 0})
	agent_names: dict = {}
	for r in raw_replies:
		key = r.agent_user or r.agent_name or "Unknown"
		agent_names[key] = r.agent_name or r.agent_user or "Unknown"
		agent_map[key]["replies"] += 1
		m = r.response_minutes or 0
		agent_map[key]["total_minutes"] += m
		if m < 5:
			agent_map[key]["lt5"] += 1
		elif m < 30:
			agent_map[key]["lt30"] += 1
		elif m < 120:
			agent_map[key]["lt120"] += 1
		else:
			agent_map[key]["gt120"] += 1

	agent_stats = sorted(
		[
			{
				"agent_name": agent_names.get(k, k),
				"replies": v["replies"],
				"avg_minutes": round(v["total_minutes"] / v["replies"]) if v["replies"] else 0,
				"lt5": v["lt5"],
				"lt30": v["lt30"],
				"lt120": v["lt120"],
				"gt120": v["gt120"],
			}
			for k, v in agent_map.items()
		],
		key=lambda x: x["replies"],
		reverse=True,
	)

	return {
		"summary": {k: (int(v) if v is not None else 0) for k, v in summary.items()},
		"daily": [dict(r) for r in daily],
		"hourly": hourly_full,
		"top_contacts": top_contacts,
		"agent_stats": agent_stats,
	}


@frappe.whitelist()
def check_baileys_number(phone: str) -> dict:
	"""Normalise a phone number to a JID and check if it is registered on WhatsApp."""
	phone = _normalize_phone(phone)
	if not phone:
		frappe.throw(_("Enter a valid phone number."))
	jid = f"{phone}@s.whatsapp.net"
	return {"jid": jid, "phone": phone}


@frappe.whitelist()
def pickup_baileys_ticket(ticket: str) -> dict:
	"""Assign the current agent to a Baileys ticket."""
	user = frappe.session.user
	if not frappe.db.exists("HD Agent", {"user": user}):
		frappe.throw(_("You are not registered as a Helpdesk Agent."))

	agent_group = frappe.db.get_value("HD Ticket", ticket, "agent_group")
	if agent_group:
		members = frappe.get_all(
			"HD Team Member",
			filters={"parent": agent_group, "parenttype": "HD Team"},
			pluck="user",
		)
		if user not in members:
			frappe.throw(_("You are not a member of the team '{0}'.").format(agent_group))

	frappe.get_doc("HD Ticket", ticket).assign_agent(user)
	return {"assigned_to": user}
