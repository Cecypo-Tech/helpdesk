import re

import frappe
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
	"""
	assign_json = frappe.db.get_value("HD Ticket", ticket_name, "_assign") or "[]"
	assignees = frappe.parse_json(assign_json) or []
	if not assignees:
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


def _send_auto_reply(phone: str, ticket_name: str, template_name: str, contact_name: str) -> None:
	"""Send a template-based auto-reply when a new ticket is created. Swallows errors."""
	try:
		import json as _json
		frappe.get_doc({
			"doctype": "WhatsApp Message",
			"type": "Outgoing",
			"message_type": "Template",
			"to": phone,
			"content_type": "text",
			"template": template_name,
			"body_param": _json.dumps({"1": contact_name, "2": ticket_name}),
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
	messages = (
		frappe.qb.from_(WM)
		.select(
			WM.name, WM.creation, WM.type, WM.message, WM.content_type,
			WM.attach, WM.status, WM.profile_name, WM["from"], WM["to"],
		)
		.where(WM.reference_doctype == "HD Ticket")
		.where(WM.reference_name == ticket)
		.orderby(WM.creation)
		.run(as_dict=True)
	)
	# Ensure datetime fields are serialized as strings
	for m in messages:
		if m.get("creation") and not isinstance(m["creation"], str):
			m["creation"] = str(m["creation"])
	return messages


@frappe.whitelist()
def send_whatsapp_reply(
	ticket: str,
	message: str,
	content_type: str = "text",
	attachment: str | None = None,
) -> dict:
	"""Send a WhatsApp reply from the agent on this ticket."""
	phone = get_contact_phone(ticket)
	if not phone:
		frappe.throw(_("No phone number found for the contact linked to this ticket."))

	msg_doc = frappe.get_doc({
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": phone,
		"message": message,
		"content_type": content_type,
		"attach": attachment,
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

	return {
		"phone": phone,
		"is_assigned": is_assigned,
		"assignees": assigned_users,
		"has_whatsapp": bool(phone),
		"reply_window_open": reply_window_open,
	}


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
			if settings.agent_reply_status:
				_set_ticket_status(doc.reference_name, settings.agent_reply_status)
			_publish_whatsapp_message(doc.reference_name, is_incoming=False)
		return

	phone = normalize_phone(doc.get("from") or "")
	if not phone:
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
			status_category = frappe.db.get_value("HD Ticket", candidate, "status_category")
			if status_category and status_category != "Resolved":
				hours_since = time_diff_in_hours(now_datetime(), linked_messages[0].creation)
				timeout = settings.new_conversation_timeout_hours or 24
				if hours_since < timeout:
					existing_ticket = candidate

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
