# helpdesk/integrations/evolution.py
import re

import frappe
import requests as _requests
from frappe import _
from frappe.utils import now_datetime, time_diff_in_hours


# ── Helpers ───────────────────────────────────────────────────────────────────

def _settings():
    return frappe.get_cached_doc("Evolution API Settings")


def _line(instance_name: str):
    """Return the Evolution Line doc for the given instance_name, or throw."""
    names = frappe.get_all(
        "Evolution Line", filters={"instance_name": instance_name}, pluck="name", limit=1
    )
    if not names:
        frappe.throw(
            _("Unknown Evolution instance: {0}").format(instance_name),
            frappe.AuthenticationError,
        )
    return frappe.get_doc("Evolution Line", names[0])


def _headers() -> dict:
    return {"apikey": _settings().global_api_key or "", "Content-Type": "application/json"}


def _url(path: str, instance: str) -> str:
    base = (_settings().server_url or "").rstrip("/")
    return f"{base}/{path}/{instance}"


def _normalize_phone(number: str) -> str:
    return re.sub(r"[^\d]", "", number or "")


def _phone_from_jid(jid: str) -> str:
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
    return parts[0][0].upper() if parts else frappe.session.user[:2].upper()


def _is_blocked(jid: str, sender: str, line) -> bool:
    blocked = line.get("blocked_jids") or []
    phone = _phone_from_jid(sender or jid)
    for row in blocked:
        entry = (row.jid or "").strip()
        if not entry:
            continue
        if entry == jid or entry == sender:
            return True
        if entry.lstrip("+") == phone or _normalize_phone(entry) == phone:
            return True
    return False


def _group_label(jid: str, line) -> str:
    for row in (line.group_jids or []):
        if row.jid == jid:
            return row.group_name or jid
    return jid


def _extract_text(msg: dict) -> tuple[str, str]:
    """Return (text, content_type) from a raw Evolution API message object."""
    # Unwrap container messages
    inner = (
        msg.get("viewOnceMessage", {}).get("message")
        or msg.get("ephemeralMessage", {}).get("message")
        or msg.get("documentWithCaptionMessage", {}).get("message")
        or msg
    )
    if not inner:
        return "", "text"

    if "conversation" in inner:
        return inner["conversation"], "text"
    if "extendedTextMessage" in inner:
        return inner["extendedTextMessage"].get("text", ""), "text"
    if "imageMessage" in inner:
        return inner["imageMessage"].get("caption", ""), "image"
    if "videoMessage" in inner:
        return inner["videoMessage"].get("caption", ""), "video"
    if "documentMessage" in inner:
        caption = inner["documentMessage"].get("caption", "") or inner["documentMessage"].get("fileName", "")
        return caption, "document"
    if "audioMessage" in inner:
        return "", "audio"
    if "stickerMessage" in inner:
        return "", "sticker"
    if "reactionMessage" in inner:
        return inner["reactionMessage"].get("text", ""), "reaction"
    return "", "text"


def _publish_evolution_event(jid: str, is_incoming: bool, line: str, ticket: str = "") -> None:
    frappe.db.commit()
    event_data = {"jid": jid, "is_incoming": is_incoming, "line": line}
    if ticket:
        event_data["ticket"] = ticket
    frappe.publish_realtime(
        "helpdesk:baileys-message",
        message=event_data,
        after_commit=True,
    )


def _notify_agents(jid: str, message_text: str, sender_name: str, line, settings) -> None:
    quiet_minutes = int(settings.notification_quiet_minutes or 0)
    if quiet_minutes:
        recent_outgoing = frappe.db.count(
            "Baileys Message",
            filters={
                "jid": jid,
                "direction": "Outgoing",
                "creation": [">", frappe.utils.add_to_date(now_datetime(), minutes=-quiet_minutes)],
                "line": line.name,
            },
        )
        if recent_outgoing:
            return
    frappe.publish_realtime(
        "helpdesk:new-baileys-message",
        message={"jid": jid, "message": (message_text or "")[:80],
                 "sender": sender_name, "line": line.name},
        after_commit=True,
    )


def _upsert_contact(jid: str, phone: str, name: str) -> None:
    """Create-or-blank-fill a Baileys Contact row keyed by JID."""
    if not jid:
        return
    try:
        if frappe.db.exists("Baileys Contact", {"jid": jid}):
            existing = frappe.db.get_value(
                "Baileys Contact", {"jid": jid}, ["custom_name", "phone"], as_dict=True
            ) or {}
            updates = {}
            if not existing.get("custom_name") and name:
                updates["custom_name"] = name
            if not existing.get("phone") and phone:
                updates["phone"] = phone
            if updates:
                frappe.db.set_value("Baileys Contact", {"jid": jid}, updates, update_modified=False)
        else:
            frappe.get_doc({
                "doctype": "Baileys Contact",
                "jid": jid,
                "phone": phone,
                "custom_name": name,
                "company": "",
                "assigned_team": "",
            }).insert(ignore_permissions=True)
    except Exception:
        pass


def _upsert_contact_name(jid: str, sender_name: str) -> None:
    if not jid or _is_group(jid):
        return
    phone = _phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else ""
    _upsert_contact(jid, phone, sender_name)
    if phone:
        _upsert_contact(f"{phone}@s.whatsapp.net", phone, sender_name)


# ── Webhook ───────────────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def webhook():
    """Single webhook endpoint for all Evolution API events across all instances."""
    if not frappe.db.exists("DocType", "Evolution API Settings"):
        frappe.response["http_status_code"] = 503
        return {"error": "Evolution API Settings not configured"}

    settings = _settings()
    if not settings.enabled:
        return {"status": "disabled"}

    # Auth: Evolution API sends the global apikey in the request header
    incoming_key = (
        frappe.get_request_header("apikey")
        or frappe.get_request_header("x-api-key")
        or ""
    )
    stored_key = settings.global_api_key or ""
    if stored_key and incoming_key != stored_key:
        frappe.response["http_status_code"] = 401
        return {"error": "Unauthorized"}

    try:
        payload = frappe.parse_json(frappe.request.data.decode("utf-8"))
    except Exception:
        frappe.response["http_status_code"] = 400
        return {"error": "Invalid JSON"}

    event = payload.get("event") or ""
    instance_name = payload.get("instance") or ""

    if not instance_name:
        return {"status": "skipped", "reason": "no instance"}

    try:
        line = _line(instance_name)
    except frappe.AuthenticationError:
        return {"status": "skipped", "reason": "unknown instance"}

    if event == "messages.upsert":
        return _handle_upsert(payload.get("data") or {}, line, settings)
    if event == "messages.update":
        return _handle_update(payload.get("data") or [], line)
    if event == "contacts.upsert":
        _handle_contacts_upsert(payload.get("data") or [])
        return {"status": "ok"}

    return {"status": "ignored", "event": event}


def _handle_upsert(data: dict, line, settings) -> dict:
    key = data.get("key") or {}
    jid = key.get("remoteJid") or ""
    from_me = bool(key.get("fromMe"))
    message_id = key.get("id") or ""
    # For groups, participant is the actual sender; for DMs it's the jid itself.
    sender = key.get("participant") or jid
    sender_name = data.get("pushName") or ""
    is_group = _is_group(jid)

    if not jid or jid == "status@broadcast" or jid.endswith("@broadcast"):
        return {"status": "skipped", "reason": "broadcast or no jid"}

    if _is_blocked(jid, sender, line):
        return {"status": "skipped", "reason": "blocked"}

    raw_msg = data.get("message") or {}
    text, content_type = _extract_text(raw_msg)

    # Deduplicate
    if message_id and frappe.db.exists("Baileys Message", {"message_id": message_id}):
        return {"status": "duplicate"}

    frappe.set_user("Administrator")

    if from_me:
        owner = line.connected_user or "Administrator"
        if not frappe.db.exists("User", owner):
            owner = "Administrator"
        doc = frappe.get_doc({
            "doctype": "Baileys Message",
            "direction": "Outgoing",
            "jid": jid,
            "sender_jid": "",
            "sender_name": "(via phone)",
            "profile_name": "(via phone)",
            "message": text,
            "content_type": content_type or "text",
            "media_url": "",
            "message_id": message_id,
            "status": "Delivered",
            "reference_doctype": "",
            "reference_name": "",
            "line": line.name,
            "is_read": 1,
        }).insert(ignore_permissions=True)
        frappe.db.set_value("Baileys Message", doc.name, "owner", owner, update_modified=False)
        _publish_evolution_event(jid, is_incoming=False, line=line.name)
        return {"status": "ok", "mirrored": True}

    # Incoming message
    frappe.get_doc({
        "doctype": "Baileys Message",
        "direction": "Incoming",
        "jid": jid,
        "sender_jid": sender,
        "sender_name": sender_name,
        "profile_name": sender_name,
        "message": text,
        "content_type": content_type or "text",
        "media_url": "",
        "message_id": message_id,
        "status": "Received",
        "reference_doctype": "",
        "reference_name": "",
        "line": line.name,
        "is_read": 0,
    }).insert(ignore_permissions=True)

    _upsert_contact_name(jid, sender_name)
    _publish_evolution_event(jid, is_incoming=True, line=line.name)
    if content_type != "reaction":
        _notify_agents(jid, text, sender_name, line, settings)

    return {"status": "ok"}


def _handle_contacts_upsert(contacts: list) -> None:
    for c in contacts:
        jid = c.get("id") or ""
        name = c.get("pushName") or c.get("name") or c.get("notify") or ""
        if jid and name:
            phone = _phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else ""
            _upsert_contact(jid, phone, name)
