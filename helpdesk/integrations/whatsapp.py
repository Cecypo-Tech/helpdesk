import json
import re

import frappe
import requests as _requests
from frappe import _
from frappe.utils import now_datetime, time_diff_in_hours


def normalize_phone(number: str) -> str:
	"""Strip +, spaces, dashes, parens → digits only."""
	return re.sub(r"[^\d]", "", number or "")


def match_phone_to_contact(phone: str) -> str | None:
	"""Query Contact DocType phone/mobile_no and phone_nos child table, return Contact name."""
	normalized = normalize_phone(phone)
	if not normalized:
		return None

	# Check primary mobile_no / phone fields directly on Contact
	contacts = frappe.get_all(
		"Contact",
		fields=["name", "phone", "mobile_no"],
		or_filters={
			"phone": ("is", "set"),
			"mobile_no": ("is", "set"),
		},
	)
	for c in contacts:
		if normalize_phone(c.phone) == normalized or normalize_phone(c.mobile_no) == normalized:
			return c.name

	# Fallback: check Contact Phone child table (covers contacts with multiple numbers
	# or where the primary was not synced to the parent)
	phone_rows = frappe.get_all(
		"Contact Phone",
		fields=["parent", "phone"],
		filters={"parenttype": "Contact"},
	)
	for row in phone_rows:
		if normalize_phone(row.phone) == normalized:
			return row.parent

	return None


def get_contact_phone(ticket: str) -> str | None:
	"""Given an HD Ticket, resolve the contact's phone number.

	First tries the Contact linked to the ticket, then falls back to
	the `from` field of any incoming WhatsApp Message linked to this ticket.
	"""
	contact_name = frappe.db.get_value("HD Ticket", ticket, "contact")
	if contact_name:
		phone = frappe.db.get_value("Contact", contact_name, "mobile_no")
		if not phone:
			phone = frappe.db.get_value("Contact", contact_name, "phone")
		if phone:
			return phone

	# Fallback: get phone from the most recent incoming WhatsApp message on this ticket
	from frappe.query_builder import DocType

	WM = DocType("WhatsApp Message")
	result = (
		frappe.qb.from_(WM)
		.select(WM["from"])
		.where(WM.reference_doctype == "HD Ticket")
		.where(WM.reference_name == ticket)
		.where(WM.type == "Incoming")
		.orderby(WM.creation, order=frappe.qb.desc)
		.limit(1)
		.run()
	)
	return result[0][0] if result else None


def _agent_initials() -> str:
	"""Return the ^XY initials suffix for the current session user."""
	full_name = frappe.db.get_value("User", frappe.session.user, "full_name") or ""
	parts = full_name.strip().split()
	if len(parts) >= 2:
		initials = parts[0][0].upper() + parts[-1][0].upper()
	elif parts:
		initials = parts[0][0].upper()
	else:
		initials = frappe.session.user[:2].upper()
	return initials


def _set_ticket_status(ticket_name: str, status_name: str) -> None:
	"""Set HD Ticket status by name, ignore errors gracefully."""
	if not status_name:
		return
	if not frappe.db.exists("HD Ticket Status", status_name):
		return
	try:
		frappe.db.set_value("HD Ticket", ticket_name, "status", status_name, update_modified=True)
		frappe.db.commit()
	except Exception:
		pass


@frappe.whitelist()
def mark_messages_read(ticket: str) -> int:
	"""Send WhatsApp read receipts for all unread incoming messages on this ticket.

	Runs silently — no msgprint/dialog. Returns the count of messages marked.
	"""
	from frappe.query_builder import DocType

	WM = DocType("WhatsApp Message")
	unread = (
		frappe.qb.from_(WM)
		.select(WM.name, WM.message_id, WM.whatsapp_account)
		.where(WM.reference_doctype == "HD Ticket")
		.where(WM.reference_name == ticket)
		.where(WM.type == "Incoming")
		.where((WM.status != "marked as read") | WM.status.isnull())
		.run(as_dict=True)
	)

	count = 0
	for row in unread:
		if not row.message_id:
			continue
		try:
			msg_doc = frappe.get_doc("WhatsApp Message", row.name)
			msg_doc.send_read_receipt()
			count += 1
		except Exception:
			pass

	# Clear any unread WhatsApp notifications for this ticket for the current user
	try:
		for notif in frappe.get_all(
			"HD Notification",
			filters={
				"user_to": frappe.session.user,
				"reference_ticket": ticket,
				"notification_type": "WhatsApp",
				"read": 0,
			},
			pluck="name",
		):
			frappe.db.set_value("HD Notification", notif, "read", 1, update_modified=False)
	except Exception:
		pass

	return count


def _notify_assigned_agents(ticket_name: str, message: str | None, sender_name: str) -> None:
	"""Create one HD Notification per assigned agent for a new incoming WhatsApp message.

	Deduplicates: skips agents who already have an unread WhatsApp notification for
	this ticket, so a chatty customer never floods the bell.

	Falls back to notifying the default_team members when the ticket has no assignees
	(e.g. brand-new tickets that haven't been picked up yet).

	Respects notification_quiet_minutes: if an agent replied within that window,
	the notification is suppressed (the conversation is being actively managed).
	"""
	settings = frappe.get_cached_doc("WhatsApp Helpdesk Settings")

	assign_json = frappe.db.get_value("HD Ticket", ticket_name, "_assign") or "[]"
	assignees = frappe.parse_json(assign_json) or []

	if not assignees and settings.default_team:
		# New / unassigned tickets: notify the default team so someone sees the bell.
		assignees = frappe.get_all(
			"HD Team Member",
			filters={"parent": settings.default_team, "parenttype": "HD Team"},
			pluck="user",
		)

	if not assignees:
		return

	# Quiet-period check: skip notification if an agent replied recently.
	quiet_minutes = settings.notification_quiet_minutes or 0
	if quiet_minutes > 0:
		last_outgoing = frappe.get_all(
			"WhatsApp Message",
			filters={
				"reference_doctype": "HD Ticket",
				"reference_name": ticket_name,
				"type": "Outgoing",
			},
			fields=["creation"],
			order_by="creation desc",
			limit=1,
		)
		if last_outgoing:
			minutes_since_reply = time_diff_in_hours(now_datetime(), last_outgoing[0].creation) * 60
			if minutes_since_reply < quiet_minutes:
				return

	preview = (message or "")[:80] or "sent a WhatsApp message"

	# Find agents who already have an unread WhatsApp notification for this ticket
	existing = frappe.get_all(
		"HD Notification",
		filters={
			"reference_ticket": ticket_name,
			"notification_type": "WhatsApp",
			"read": 0,
		},
		pluck="user_to",
	)

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
				"message": f"{sender_name}: {preview}",
			}).insert(ignore_permissions=True)
		except Exception:
			pass


def _reopen_ticket(ticket_name: str, settings) -> None:
	"""Reopen a resolved/closed ticket when the customer messages again via WhatsApp.

	Uses customer_reply_status if configured, otherwise falls back to the first
	status in the "Open" category.
	"""
	target_status = settings.customer_reply_status
	if not target_status:
		target_status = frappe.db.get_value("HD Ticket Status", {"status_category": "Open"}, "name")
	if target_status:
		_set_ticket_status(ticket_name, target_status)


def _render_template_message(template_name: str, params: dict) -> str:
	"""Render a WhatsApp template body by substituting {{1}}, {{2}}… placeholders."""
	try:
		body = frappe.db.get_value("WhatsApp Templates", template_name, "template") or ""
		for key, val in params.items():
			body = body.replace(f"{{{{{key}}}}}", str(val or ""))
		return body
	except Exception:
		return ""


def _send_auto_reply(phone: str, ticket_name: str, template_name: str, contact_name: str) -> None:
	"""Send a template-based auto-reply when a new ticket is created. Swallows errors."""
	try:
		body_param = {"1": contact_name, "2": ticket_name}
		rendered = _render_template_message(template_name, body_param)
		frappe.get_doc({
			"doctype": "WhatsApp Message",
			"type": "Outgoing",
			"message_type": "Template",
			"message": rendered,
			"to": phone,
			"content_type": "text",
			"template": template_name,
			"body_param": json.dumps(body_param),
			"reference_doctype": "HD Ticket",
			"reference_name": ticket_name,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "WhatsApp Auto-Reply Failed")


def _publish_whatsapp_message(ticket_name: str, is_incoming: bool) -> None:
	"""Publish a realtime event to all System Users.

	Uses the "all" room (frappe's site room) so every logged-in Desk user
	receives the event regardless of socket reconnects or room-join timing.
	The frontend filters by ticket ID, so only agents viewing this ticket react.
	Commits first to ensure the frontend reload sees committed data.
	"""
	frappe.db.commit()
	frappe.publish_realtime(
		"helpdesk:whatsapp-message",
		message={"ticket": str(ticket_name), "is_incoming": is_incoming},
		# no room → frappe defaults to "all" (all System Users)
	)


@frappe.whitelist()
def get_whatsapp_messages(ticket: str) -> list[dict]:
	"""Fetch all WhatsApp Messages linked to this ticket."""
	from frappe.query_builder import DocType

	WM = DocType("WhatsApp Message")
	User = DocType("User")
	messages = (
		frappe.qb.from_(WM)
		.left_join(User).on(User.name == WM.owner)
		.select(
			WM.name, WM.creation, WM.type, WM.message, WM.content_type,
			WM.attach, WM.status, WM.profile_name, WM["from"], WM["to"],
			WM.owner, User.full_name.as_("sender_full_name"),
			WM.template, WM.template_parameters,
			WM.message_id, WM.is_reply, WM.reply_to_message_id,
		)
		.where(WM.reference_doctype == "HD Ticket")
		.where(WM.reference_name == ticket)
		.orderby(WM.creation)
		.run(as_dict=True)
	)

	# Cache template bodies for any template messages with empty message field
	template_cache: dict[str, str] = {}
	for m in messages:
		if m.get("creation") and not isinstance(m["creation"], str):
			m["creation"] = str(m["creation"])

		if not m.get("message") and m.get("template"):
			tpl_name = m["template"]
			if tpl_name not in template_cache:
				template_cache[tpl_name] = (
					frappe.db.get_value("WhatsApp Templates", tpl_name, "template") or ""
				)
			body = template_cache[tpl_name]
			# template_parameters is a JSON list ["val1", "val2", ...]
			# where index 0 → {{1}}, index 1 → {{2}}, etc.
			if body and m.get("template_parameters"):
				try:
					params = json.loads(m["template_parameters"])
					for i, val in enumerate(params, start=1):
						body = body.replace(f"{{{{{i}}}}}", str(val or ""))
				except Exception:
					pass
			m["message"] = body

	return messages


@frappe.whitelist()
def send_whatsapp_reply(
	ticket: str,
	message: str,
	content_type: str = "text",
	attachment: str | None = None,
	reply_to_message_id: str | None = None,
) -> dict:
	"""Send a WhatsApp reply from the agent on this ticket."""
	phone = get_contact_phone(ticket)
	if not phone:
		frappe.throw(_("No phone number found for the contact linked to this ticket."))

	message = f"{message}\n^{_agent_initials()}"

	msg_doc = frappe.get_doc({
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": phone,
		"message": message,
		"content_type": content_type,
		"attach": attachment,
		"is_reply": 1 if reply_to_message_id else 0,
		"reply_to_message_id": reply_to_message_id or "",
		"reference_doctype": "HD Ticket",
		"reference_name": ticket,
	})
	msg_doc.insert(ignore_permissions=True)

	# Auto-assign to replying agent if ticket is unassigned
	assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
	if not frappe.parse_json(assign_json):
		try:
			ticket_doc = frappe.get_doc("HD Ticket", ticket)
			ticket_doc.assign_agent(frappe.session.user)
		except Exception:
			pass

	# Update ticket status on agent reply
	settings = frappe.get_cached_doc("WhatsApp Helpdesk Settings")
	if settings.enabled and settings.agent_reply_status:
		_set_ticket_status(ticket, settings.agent_reply_status)

	return {"name": msg_doc.name, "status": msg_doc.status}


@frappe.whitelist(allow_guest=False)
def send_whatsapp_media(
	ticket: str,
	message: str = "",
	content_type: str = "document",
	reply_to_message_id: str = "",
) -> dict:
	"""Upload a file directly to WhatsApp Media API and send — no Frappe File storage."""
	from frappe_whatsapp.utils import get_whatsapp_account, format_number

	phone = get_contact_phone(ticket)
	if not phone:
		frappe.throw(_("No phone number found for the contact linked to this ticket."))

	file_obj = frappe.request.files.get("file")
	if not file_obj:
		frappe.throw(_("No file provided."))

	filename = file_obj.filename or "attachment"
	mime_type = file_obj.content_type or "application/octet-stream"
	file_data = file_obj.read()

	# Derive content_type from mime if not specified by caller
	if content_type == "document":
		if mime_type.startswith("image/"):
			content_type = "image"
		elif mime_type.startswith("video/"):
			content_type = "video"
		elif mime_type.startswith("audio/"):
			content_type = "audio"

	account = get_whatsapp_account(account_type="outgoing")
	if not account:
		frappe.throw(_("No default outgoing WhatsApp Account configured."))

	token = account.get_password("token")
	base_url = f"{account.url}/{account.version}/{account.phone_id}"
	auth_headers = {"Authorization": f"Bearer {token}"}

	# 1. Upload media to WhatsApp
	media_resp = _requests.post(
		f"{base_url}/media",
		headers=auth_headers,
		files={
			"file": (filename, file_data, mime_type),
			"messaging_product": (None, "whatsapp"),
			"type": (None, mime_type),
		},
	)
	if not media_resp.ok:
		frappe.throw(f"WhatsApp media upload failed: {media_resp.text}")

	media_id = media_resp.json().get("id")
	if not media_id:
		frappe.throw("WhatsApp did not return a media ID.")

	# 2. Send the message using media_id (no public URL needed)
	media_payload: dict = {"id": media_id}
	if content_type in ("image", "video", "document"):
		caption = f"{message}\n^{_agent_initials()}" if message else f"^{_agent_initials()}"
		media_payload["caption"] = caption
	if content_type == "document":
		media_payload["filename"] = filename

	msg_payload: dict = {
		"messaging_product": "whatsapp",
		"to": format_number(phone),
		"type": content_type,
		content_type: media_payload,
	}
	if reply_to_message_id:
		msg_payload["context"] = {"message_id": reply_to_message_id}

	send_resp = _requests.post(
		f"{base_url}/messages",
		headers={**auth_headers, "Content-Type": "application/json"},
		data=json.dumps(msg_payload),
	)
	if not send_resp.ok:
		frappe.throw(f"WhatsApp send failed: {send_resp.text}")

	wm_id = send_resp.json()["messages"][0]["id"]

	# 3. Save file to Frappe storage so agents can preview it in the chat.
	#    We only do this for images/videos — documents are downloaded via WhatsApp CDN.
	attach_url = None
	if content_type in ("image", "video"):
		try:
			file_doc = frappe.get_doc({
				"doctype": "File",
				"file_name": filename,
				"content": file_data,
				"is_private": 0,
			})
			file_doc.insert(ignore_permissions=True)
			attach_url = file_doc.file_url
		except Exception:
			pass  # preview unavailable, not a hard failure

	# 4. Record the message in DB.
	# Set message_type="Template" + message_id=<sent_id> so frappe_whatsapp's
	# before_insert skips its own send logic (both branches check these conditions).
	msg_doc = frappe.get_doc({
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": phone,
		"message": message,
		"content_type": content_type,
		"message_type": "Template",   # prevents before_insert from re-sending
		"message_id": wm_id,          # prevents template branch from firing
		"status": "Success",
		"attach": attach_url,         # URL for in-chat preview (images/videos only)
		"is_reply": 1 if reply_to_message_id else 0,
		"reply_to_message_id": reply_to_message_id or "",
		"reference_doctype": "HD Ticket",
		"reference_name": ticket,
	})
	msg_doc.insert(ignore_permissions=True)

	# Auto-assign replying agent if unassigned
	assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
	if not frappe.parse_json(assign_json):
		try:
			ticket_doc = frappe.get_doc("HD Ticket", ticket)
			ticket_doc.assign_agent(frappe.session.user)
		except Exception:
			pass

	return {
		"name": msg_doc.name,
		"status": "Success",
		"content_type": content_type,
		"filename": filename,
	}


@frappe.whitelist()
def pickup_ticket(ticket: str) -> dict:
	"""Assign the current user to the ticket, respecting HD Team rules."""
	user = frappe.session.user

	# Check user is an HD Agent
	if not frappe.db.exists("HD Agent", {"user": user}):
		frappe.throw(_("You are not registered as a Helpdesk Agent."))

	# Check team membership if ticket has an agent_group
	agent_group = frappe.db.get_value("HD Ticket", ticket, "agent_group")
	if agent_group:
		team_members = frappe.get_all(
			"HD Team Member",
			filters={"parent": agent_group, "parenttype": "HD Team"},
			fields=["user"],
		)
		member_users = [m.user for m in team_members]
		if user not in member_users:
			frappe.throw(
				_("You are not a member of the team '{0}' assigned to this ticket.").format(agent_group)
			)

	# Use helpdesk's own assignment method via the ticket document
	ticket_doc = frappe.get_doc("HD Ticket", ticket)
	ticket_doc.assign_agent(user)

	return {"assigned_to": user}


@frappe.whitelist()
def get_ticket_whatsapp_info(ticket: str) -> dict:
	"""Return WhatsApp-related info for a ticket."""
	phone = get_contact_phone(ticket)

	# Check assignment via _assign field (how helpdesk tracks it)
	assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
	assigned_users = frappe.parse_json(assign_json) or []
	is_assigned = frappe.session.user in assigned_users

	# Check 24h reply window
	reply_window_open = False
	if phone:
		last_incoming = frappe.get_all(
			"WhatsApp Message",
			filters={
				"reference_doctype": "HD Ticket",
				"reference_name": ticket,
				"type": "Incoming",
			},
			fields=["creation"],
			order_by="creation desc",
			limit=1,
		)
		if last_incoming:
			hours_since = time_diff_in_hours(now_datetime(), last_incoming[0].creation)
			reply_window_open = hours_since < 24

	settings = frappe.get_cached_doc("WhatsApp Helpdesk Settings")
	return {
		"phone": phone,
		"is_assigned": is_assigned,
		"assignees": assigned_users,
		"has_whatsapp": bool(phone),
		"reply_window_open": reply_window_open,
		"allow_template_outside_window": bool(settings.allow_template_outside_window),
	}


@frappe.whitelist()
def get_outgoing_templates() -> list[dict]:
	"""Return WhatsApp Templates available for outgoing agent messages.

	If the admin has configured an allowed list in WhatsApp Helpdesk Settings,
	only those templates are returned. Otherwise all templates are returned.
	"""
	settings = frappe.get_cached_doc("WhatsApp Helpdesk Settings")
	allowed_rows = settings.get("allowed_templates") or []

	filters: dict = {}
	if allowed_rows:
		allowed_names = [row.template for row in allowed_rows if row.template]
		if allowed_names:
			filters["name"] = ["in", allowed_names]

	templates = frappe.get_all(
		"WhatsApp Templates",
		filters=filters,
		fields=["name", "template_name", "template", "actual_name"],
		order_by="template_name asc",
	)
	return templates


@frappe.whitelist()
def send_template_to_ticket(ticket: str, template_name: str) -> dict:
	"""Send an approved template message to reopen/start a conversation outside the 24h window.

	Template variables follow the same convention as the initial message:
	  {{1}} = contact full name, {{2}} = ticket name (HD-XXXX).
	"""
	contact_name_field = frappe.db.get_value("HD Ticket", ticket, "contact")
	if contact_name_field:
		contact_display = frappe.db.get_value(
			"Contact", contact_name_field, "full_name"
		) or contact_name_field
	else:
		contact_display = "Customer"

	_send_auto_reply(
		phone=get_contact_phone(ticket) or frappe.throw(_("No phone number on this ticket.")),
		ticket_name=ticket,
		template_name=template_name,
		contact_name=contact_display,
	)
	return {"status": "sent"}


@frappe.whitelist()
def send_whatsapp_reaction(ticket: str, target_message_id: str, emoji: str) -> dict:
	"""Send an emoji reaction to a specific WhatsApp message on this ticket."""
	phone = get_contact_phone(ticket)
	if not phone:
		frappe.throw(_("No phone number found for the contact linked to this ticket."))

	msg_doc = frappe.get_doc({
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": phone,
		"content_type": "reaction",
		"message": emoji,
		"reply_to_message_id": target_message_id,
		"reference_doctype": "HD Ticket",
		"reference_name": ticket,
	})
	msg_doc.insert(ignore_permissions=True)
	return {"status": "sent"}


@frappe.whitelist()
def get_product_options() -> list[str]:
	"""Return the product list from WhatsApp Helpdesk Settings as an array."""
	raw = frappe.db.get_single_value("WhatsApp Helpdesk Settings", "product_list") or ""
	return [line.strip() for line in raw.splitlines() if line.strip()]


def on_whatsapp_message_update(doc, method=None):
	"""Hook fired on WhatsApp Message on_update — publishes status changes to the helpdesk tab."""
	if doc.reference_doctype != "HD Ticket" or not doc.reference_name:
		return

	# Only publish if status actually changed
	before = doc.get_doc_before_save()
	if before and before.status == doc.status:
		return

	frappe.db.commit()
	frappe.publish_realtime(
		"helpdesk:whatsapp-status-update",
		message={
			"ticket": str(doc.reference_name),
			"message_name": doc.name,
			"status": doc.status or "",
		},
	)


def on_whatsapp_message_insert(doc, method=None):
	"""Hook fired on WhatsApp Message after_insert for helpdesk integration.

	Guarded: silently does nothing if frappe_whatsapp is not installed or
	WhatsApp Helpdesk Settings does not exist.
	"""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return

	settings = frappe.get_cached_doc("WhatsApp Helpdesk Settings")
	if not settings.enabled:
		return

	# Outgoing messages: if already linked to a ticket, publish realtime + update status
	if doc.type != "Incoming":
		if doc.reference_doctype == "HD Ticket" and doc.reference_name:
			# Emoji reactions don't count as agent replies for status purposes
			if doc.content_type != "reaction" and settings.agent_reply_status:
				_set_ticket_status(doc.reference_name, settings.agent_reply_status)
			_publish_whatsapp_message(doc.reference_name, is_incoming=False)
		return

	phone = normalize_phone(doc.get("from") or "")
	if not phone:
		return

	# Blocklist check — silent drop
	for row in (settings.get("blocked_numbers") or []):
		if normalize_phone(row.phone or "") == phone:
			return

	# Try to find a contact
	contact_name = match_phone_to_contact(phone)
	placeholder_domain = settings.placeholder_email_domain or "whatsapp.placeholder.local"
	profile_name = doc.profile_name or f"WhatsApp User {phone}"

	if not contact_name:
		action = settings.unknown_contact_action
		if action == "Skip Ticket Creation":
			return
		elif action == "Create Contact and Ticket":
			original_user = frappe.session.user
			frappe.set_user("Administrator")
			try:
				contact_doc = frappe.get_doc({
					"doctype": "Contact",
					"first_name": profile_name,
					"phone_nos": [
						{
							"doctype": "Contact Phone",
							"phone": phone,
							"is_primary_mobile_no": 1,
						}
					],
				})
				contact_doc.insert(ignore_permissions=True)
				contact_name = contact_doc.name
			finally:
				frappe.set_user(original_user)
		# For "Create Ticket Only", contact_name stays None

	# Resolve email for ticket
	if contact_name:
		email = frappe.db.get_value("Contact", contact_name, "email_id")
		if not email:
			email = f"whatsapp+{phone}@{placeholder_domain}"
	else:
		email = f"whatsapp+{phone}@{placeholder_domain}"

	# Check for existing open ticket linked to this phone
	existing_ticket = None
	from frappe.query_builder import DocType

	WM = DocType("WhatsApp Message")
	linked_messages = (
		frappe.qb.from_(WM)
		.select(WM.reference_name, WM.creation)
		.where(WM.reference_doctype == "HD Ticket")
		.where(WM.type == "Incoming")
		.where(WM["from"] == doc.get("from"))
		.orderby(WM.creation, order=frappe.qb.desc)
		.limit(1)
		.run(as_dict=True)
	)

	if linked_messages:
		candidate = linked_messages[0].reference_name
		if candidate:
			hours_since = time_diff_in_hours(now_datetime(), linked_messages[0].creation)
			timeout = settings.new_conversation_timeout_hours or 24
			if hours_since < timeout:
				status_category = frappe.db.get_value("HD Ticket", candidate, "status_category")
				if status_category == "Closed":
					# Agent deliberately closed — start a fresh ticket
					pass
				else:
					# Open, Pending, or Resolved → reuse and reopen if needed
					existing_ticket = candidate
					if status_category == "Resolved":
						_reopen_ticket(candidate, settings)

	if existing_ticket:
		doc.db_set("reference_doctype", "HD Ticket", update_modified=False)
		doc.db_set("reference_name", existing_ticket, update_modified=False)
		if settings.customer_reply_status:
			_set_ticket_status(existing_ticket, settings.customer_reply_status)
		_notify_assigned_agents(existing_ticket, doc.message, profile_name)
		_publish_whatsapp_message(existing_ticket, is_incoming=True)
	else:
		subject = (doc.message or "")[:100] if doc.message else f"WhatsApp from {profile_name}"
		ticket_data = {
			"doctype": "HD Ticket",
			"subject": subject or f"WhatsApp from {profile_name}",
			"raised_by": email,
			"description": doc.message or "",
			"via_customer_portal": 0,
			"ticket_channel": "WhatsApp",
		}
		if contact_name:
			ticket_data["contact"] = contact_name
		if settings.default_ticket_type:
			ticket_data["ticket_type"] = settings.default_ticket_type
		if settings.default_team:
			ticket_data["agent_group"] = settings.default_team

		original_user = frappe.session.user
		frappe.set_user("Administrator")
		try:
			ticket_doc = frappe.get_doc(ticket_data)
			ticket_doc.insert(ignore_permissions=True)
		finally:
			frappe.set_user(original_user)

		doc.db_set("reference_doctype", "HD Ticket", update_modified=False)
		doc.db_set("reference_name", ticket_doc.name, update_modified=False)

		_notify_assigned_agents(ticket_doc.name, doc.message, profile_name)
		_publish_whatsapp_message(ticket_doc.name, is_incoming=True)

		if settings.send_initial_message and settings.initial_message_template:
			_send_auto_reply(phone, ticket_doc.name, settings.initial_message_template, profile_name)
