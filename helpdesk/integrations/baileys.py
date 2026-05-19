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
	frappe.publish_realtime(
		"helpdesk:baileys-message",
		message={"jid": jid, "is_incoming": is_incoming},
		after_commit=True,
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

	# DEBUG: log full payload for incoming media to understand gateway format
	content_type_raw = payload.get("contentType", "text")
	if content_type_raw in ("image", "video", "audio", "document"):
		frappe.logger("baileys").info("MEDIA WEBHOOK PAYLOAD: %s", raw_body[:2000])

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

	_publish_baileys_event(jid, is_incoming=True)
	_notify_agents_baileys(jid, message, sender_name)

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

	return {"ok": True}


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
def mark_baileys_messages_read(ticket: str) -> int:
	"""Mark unread incoming Baileys Messages as read, send read receipts to gateway."""
	settings = _settings()
	jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	if not jid:
		return 0

	unread = frappe.get_all(
		"Baileys Message",
		filters={
			"reference_doctype": "HD Ticket",
			"reference_name": ticket,
			"direction": "Incoming",
			"status": ["not in", ["Read"]],
		},
		fields=["name", "message_id"],
	)

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

	# Clear HD Notifications
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

	BM = DocType("Baileys Message")
	rows = (
		frappe.qb.from_(BM)
		.select(BM.jid, BM.sender_name, BM.message, BM.content_type, BM.direction, BM.creation)
		.orderby(BM.creation, order=frappe.qb.desc)
		.run(as_dict=True)
	)

	seen: dict[str, dict] = {}
	for r in rows:
		jid_val = r.get("jid") or ""
		if jid_val and jid_val not in seen and not jid_val.endswith("@broadcast"):
			seen[jid_val] = r

	settings = _settings()
	group_names = {row.jid: (row.group_name or row.jid) for row in (settings.group_jids or [])}

	# Bulk-load custom contact overrides
	jids = list(seen.keys())
	contacts: dict[str, dict] = {}
	if jids and frappe.db.exists("DocType", "Baileys Contact"):
		for c in frappe.get_all("Baileys Contact", filters={"jid": ["in", jids]}, fields=["jid", "custom_name", "company", "assigned_team"]):
			contacts[c.jid] = c

	# Team-based access control
	restrict = settings.get("restrict_chats_by_team")
	user_teams: set[str] = set()
	user_has_any_team = False
	if restrict:
		tm_rows = frappe.get_all("HD Team Member", filters={"user": frappe.session.user}, pluck="parent")
		user_teams = set(tm_rows)
		user_has_any_team = bool(user_teams)

	result = []
	for jid, r in seen.items():
		is_grp = _is_group(jid)
		contact = contacts.get(jid, {})
		assigned_team = contact.get("assigned_team") or ""

		# Access control: skip chats this user's team is not assigned to
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
			"is_group": is_grp,
			"last_message": r.get("message") or f"[{r.get('content_type', 'media')}]",
			"last_message_time": str(r["creation"]),
			"last_direction": r.get("direction", "Incoming"),
			"content_type": r.get("content_type", "text"),
		})

	return result


@frappe.whitelist()
def save_baileys_contact(jid: str, custom_name: str = "", company: str = "", assigned_team: str = "") -> dict:
	"""Create or update a Baileys Contact override for a JID."""
	custom_name = (custom_name or "").strip()
	company = (company or "").strip()
	assigned_team = (assigned_team or "").strip()

	if frappe.db.exists("Baileys Contact", {"jid": jid}):
		doc = frappe.get_doc("Baileys Contact", {"jid": jid})
		doc.custom_name = custom_name
		doc.company = company
		doc.assigned_team = assigned_team
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc({
			"doctype": "Baileys Contact",
			"jid": jid,
			"phone": _phone_from_jid(jid) if not _is_group(jid) else "",
			"custom_name": custom_name,
			"company": company,
			"assigned_team": assigned_team,
		}).insert(ignore_permissions=True)

	return {"status": "ok", "jid": jid, "custom_name": custom_name, "company": company}


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
