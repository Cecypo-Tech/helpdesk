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


def _notify_agents(ticket_name: str, preview: str, sender_name: str) -> None:
	"""Create HD Notification for each assigned agent (or default team if unassigned)."""
	settings = _settings()

	assign_json = frappe.db.get_value("HD Ticket", ticket_name, "_assign") or "[]"
	assignees = frappe.parse_json(assign_json) or []

	if not assignees and settings.default_team:
		assignees = frappe.get_all(
			"HD Team Member",
			filters={"parent": settings.default_team, "parenttype": "HD Team"},
			pluck="user",
		)

	if not assignees:
		return

	# Quiet-period check
	quiet_minutes = settings.notification_quiet_minutes or 0
	if quiet_minutes > 0:
		last_outgoing = frappe.get_all(
			"Baileys Message",
			filters={"reference_doctype": "HD Ticket", "reference_name": ticket_name, "direction": "Outgoing"},
			fields=["creation"],
			order_by="creation desc",
			limit=1,
		)
		if last_outgoing:
			minutes_since = time_diff_in_hours(now_datetime(), last_outgoing[0].creation) * 60
			if minutes_since < quiet_minutes:
				return

	existing = frappe.get_all(
		"HD Notification",
		filters={"reference_ticket": ticket_name, "notification_type": "WhatsApp", "read": 0},
		pluck="user_to",
	)

	short_preview = (preview or "sent a message")[:80]
	for agent in assignees:
		if agent in existing:
			continue
		try:
			frappe.get_doc({
				"doctype": "HD Notification",
				"user_from": "Administrator",
				"user_to": agent,
				"notification_type": "WhatsApp",
				"reference_ticket": ticket_name,
				"message": f"{sender_name}: {short_preview}",
			}).insert(ignore_permissions=True)
		except Exception:
			pass


# ── Ticket routing ────────────────────────────────────────────────────────────

def _find_open_group_ticket(jid: str) -> str | None:
	"""Return the name of the current open ticket for this group JID, or None."""
	open_statuses = frappe.get_all(
		"HD Ticket Status",
		filters={"status_category": "Open"},
		pluck="name",
	)
	if not open_statuses:
		return None

	tickets = frappe.get_all(
		"HD Ticket",
		filters={"baileys_jid": jid, "status": ["in", open_statuses]},
		pluck="name",
		order_by="creation desc",
		limit=1,
	)
	return tickets[0] if tickets else None


def _find_open_dm_ticket(phone: str, timeout_hours: int) -> str | None:
	"""Return the most recent open ticket for this phone within the timeout window."""
	from frappe.query_builder import DocType

	BM = DocType("Baileys Message")
	recent = (
		frappe.qb.from_(BM)
		.select(BM.reference_name)
		.where(BM.reference_doctype == "HD Ticket")
		.where(BM.jid.like(f"{phone}@%"))
		.where(BM.direction == "Incoming")
		.orderby(BM.creation, order=frappe.qb.desc)
		.limit(1)
		.run()
	)
	if not recent:
		return None

	ticket_name = recent[0][0]
	ticket = frappe.db.get_value("HD Ticket", ticket_name, ["status", "creation", "baileys_jid"], as_dict=True)
	if not ticket:
		return None

	open_statuses = frappe.get_all("HD Ticket Status", filters={"status_category": "Open"}, pluck="name")
	if ticket.status not in open_statuses:
		return None

	hours_since = time_diff_in_hours(now_datetime(), frappe.db.get_value("Baileys Message", {"reference_name": ticket_name, "direction": "Incoming"}, "creation", order_by="creation desc"))
	if hours_since > timeout_hours:
		return None

	return ticket_name


def _group_label(jid: str, settings) -> str:
	"""Return the configured group name for a JID, or a formatted fallback."""
	for row in (settings.group_jids or []):
		if row.jid == jid:
			return row.group_name or jid
	return jid


def _group_team(jid: str, settings) -> str | None:
	"""Return team override for a group JID, or None."""
	for row in (settings.group_jids or []):
		if row.jid == jid and row.team:
			return row.team
	return None


def _create_ticket(subject: str, raised_by: str, settings, team_override: str | None = None) -> str:
	"""Insert a new HD Ticket and return its name."""
	ticket_data = {
		"doctype": "HD Ticket",
		"subject": subject,
		"raised_by": raised_by,
		"via_customer_portal": 0,
		"ticket_channel": "WhatsApp",
	}
	if settings.default_ticket_type:
		ticket_data["ticket_type"] = settings.default_ticket_type
	team = team_override or settings.default_team
	if team:
		ticket_data["agent_group"] = team

	ticket_doc = frappe.get_doc(ticket_data)
	ticket_doc.insert(ignore_permissions=True)
	return ticket_doc.name


# ── Contact matching (reuse from whatsapp integration) ────────────────────────

def _match_phone_to_contact(phone: str) -> str | None:
	from helpdesk.integrations.whatsapp import match_phone_to_contact
	return match_phone_to_contact(phone)


# ── Webhook ───────────────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def webhook():
	"""Receive incoming messages from the Baileys gateway.

	Expected payload:
	  { jid, messageId, sender, senderName, message, contentType, timestamp }

	Security: validates X-API-Key header against Baileys Gateway Settings.
	"""
	if not frappe.db.exists("DocType", "Baileys Gateway Settings"):
		frappe.response["http_status_code"] = 503
		return {"error": "Baileys Gateway Settings not configured"}

	settings = _settings()
	if not settings.enabled:
		return {"status": "disabled"}

	# Validate API key
	api_key = frappe.get_request_header("X-API-Key") or frappe.get_request_header("x-api-key")
	stored_key = settings.get_password("api_key") if settings.api_key else ""
	if not stored_key or api_key != stored_key:
		frappe.response["http_status_code"] = 401
		return {"error": "Unauthorized"}

	try:
		payload = frappe.parse_json(frappe.request.data)
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

	if not jid:
		return {"status": "skipped", "reason": "no jid"}

	# Deduplicate
	if message_id and frappe.db.exists("Baileys Message", {"message_id": message_id}):
		return {"status": "duplicate"}

	frappe.set_user("Administrator")

	is_grp = _is_group(jid)
	placeholder_domain = settings.placeholder_email_domain or "baileys.placeholder.local"

	if is_grp:
		ticket_name = _find_open_group_ticket(jid)
		if not ticket_name:
			group_label = _group_label(jid, settings)
			team_override = _group_team(jid, settings)
			raised_by = f"group+{jid}@{placeholder_domain}"
			ticket_name = _create_ticket(f"WhatsApp Group: {group_label}", raised_by, settings, team_override)
			frappe.db.set_value("HD Ticket", ticket_name, "baileys_jid", jid, update_modified=False)
			frappe.db.commit()
	else:
		# Individual DM
		phone = _phone_from_jid(jid)
		if not phone:
			return {"status": "skipped", "reason": "no phone"}

		contact_name = _match_phone_to_contact(phone)
		action = settings.unknown_contact_action

		if not contact_name:
			if action == "Skip Ticket Creation":
				return {"status": "skipped", "reason": "unknown contact"}
			if action == "Create Contact and Ticket":
				contact_doc = frappe.get_doc({
					"doctype": "Contact",
					"first_name": sender_name,
					"phone_nos": [{"doctype": "Contact Phone", "phone": phone, "is_primary_mobile_no": 1}],
				})
				contact_doc.insert(ignore_permissions=True)
				contact_name = contact_doc.name

		timeout = settings.new_conversation_timeout_hours or 24
		ticket_name = _find_open_dm_ticket(phone, timeout)
		if not ticket_name:
			if contact_name:
				email = frappe.db.get_value("Contact", contact_name, "email_id")
				raised_by = email or f"whatsapp+{phone}@{placeholder_domain}"
			else:
				raised_by = f"whatsapp+{phone}@{placeholder_domain}"
			ticket_name = _create_ticket(f"WhatsApp from {sender_name}", raised_by, settings)
			frappe.db.set_value("HD Ticket", ticket_name, "baileys_jid", jid, update_modified=False)
			frappe.db.commit()

	# Reopen if resolved/closed
	customer_status = settings.customer_reply_status
	if customer_status:
		_set_ticket_status(ticket_name, customer_status)

	# Create Baileys Message record
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
		"status": "Delivered",
		"reference_doctype": "HD Ticket",
		"reference_name": ticket_name,
	}).insert(ignore_permissions=True)

	_notify_agents(ticket_name, message, sender_name)
	_publish_event(ticket_name, is_incoming=True)

	return {"status": "ok", "ticket": ticket_name}


# ── Agent send ────────────────────────────────────────────────────────────────

@frappe.whitelist()
def send_baileys_reply(
	ticket: str,
	message: str,
	content_type: str = "text",
	media_url: str | None = None,
	reply_to_message_id: str | None = None,
) -> dict:
	"""Send a text (or media) reply via the Baileys gateway."""
	settings = _settings()
	if not settings.enabled:
		frappe.throw(_("Baileys gateway is not enabled."))

	jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	if not jid:
		frappe.throw(_("This ticket is not linked to a Baileys chat."))

	agent_suffix = f"\n^{_agent_initials()}"
	full_message = f"{message}{agent_suffix}" if message else agent_suffix.strip()

	gateway_url = (settings.gateway_url or "").rstrip("/")
	api_key = settings.get_password("api_key") if settings.api_key else ""

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
		"message": message,
		"content_type": content_type,
		"media_url": media_url or "",
		"message_id": sent_id,
		"status": "Sent",
		"reference_doctype": "HD Ticket",
		"reference_name": ticket,
	})
	msg_doc.insert(ignore_permissions=True)

	# Auto-assign replying agent if ticket is unassigned
	assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
	if not frappe.parse_json(assign_json):
		try:
			frappe.get_doc("HD Ticket", ticket).assign_agent(frappe.session.user)
		except Exception:
			pass

	if settings.agent_reply_status:
		_set_ticket_status(ticket, settings.agent_reply_status)

	_publish_event(ticket, is_incoming=False)

	return {"name": msg_doc.name, "message_id": sent_id, "status": "Sent"}


@frappe.whitelist(allow_guest=False)
def send_baileys_media(ticket: str, message: str = "", content_type: str = "document") -> dict:
	"""Upload a file to Frappe storage and send its public URL via the gateway."""
	file_obj = frappe.request.files.get("file")
	if not file_obj:
		frappe.throw(_("No file provided."))

	filename = file_obj.filename or "attachment"
	mime_type = file_obj.content_type or "application/octet-stream"
	file_data = file_obj.read()

	# Derive content_type from mime
	if mime_type.startswith("image/"):
		content_type = "image"
	elif mime_type.startswith("video/"):
		content_type = "video"
	elif mime_type.startswith("audio/"):
		content_type = "audio"
	else:
		content_type = "document"

	# Save to Frappe public files to get a URL the gateway can fetch
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
		message=message,
		content_type=content_type,
		media_url=public_url,
	)


# ── Queries ───────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_baileys_messages(ticket: str) -> list[dict]:
	"""Return all Baileys Messages for a ticket in WhatsAppBubble-compatible shape."""
	from frappe.query_builder import DocType

	BM = DocType("Baileys Message")
	User = DocType("User")

	rows = (
		frappe.qb.from_(BM)
		.left_join(User).on(User.name == BM.owner)
		.select(
			BM.name, BM.creation, BM.direction, BM.jid, BM.message,
			BM.content_type, BM.media_url, BM.sender_jid, BM.sender_name,
			BM.profile_name, BM.message_id, BM.status, BM.owner,
			User.full_name.as_("sender_full_name"),
		)
		.where(BM.reference_doctype == "HD Ticket")
		.where(BM.reference_name == ticket)
		.orderby(BM.creation)
		.run(as_dict=True)
	)

	for m in rows:
		if m.get("creation") and not isinstance(m["creation"], str):
			m["creation"] = str(m["creation"])
		# Map direction → type so WhatsAppBubble works without modification
		m["type"] = "Outgoing" if m["direction"] == "Outgoing" else "Incoming"
		# Map media_url → attach for bubble media rendering
		m["attach"] = m.get("media_url") or ""
		# Bubble shows is_reply / reply_to_message_id — not implemented in v1
		m["is_reply"] = 0
		m["reply_to_message_id"] = ""

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
		api_key = settings.get_password("api_key") if settings.api_key else ""
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
