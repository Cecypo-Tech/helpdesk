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


def _headers(line_doc=None) -> dict:
    key = (line_doc.instance_token if line_doc and getattr(line_doc, "instance_token", None) else None) \
          or _settings().global_api_key or ""
    return {"apikey": key, "Content-Type": "application/json"}


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
        "status": "Pending",
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


# Evolution API v2 message status integer codes
_STATUS_MAP = {
    0: None,         # ERROR — ignore
    1: "Sent",       # PENDING
    2: "Sent",       # SERVER_ACK
    3: "Delivered",  # DELIVERY_ACK
    4: "Read",       # READ
    5: "Read",       # PLAYED (audio/video)
}


def _handle_update(updates: list, line) -> dict:
    for item in updates:
        key = item.get("key") or {}
        message_id = key.get("id") or ""
        raw_status = (item.get("update") or {}).get("status")
        status = _STATUS_MAP.get(raw_status) if raw_status is not None else None
        if not message_id or not status:
            continue
        msg_name = frappe.db.get_value("Baileys Message", {"message_id": message_id}, "name")
        if not msg_name:
            continue
        frappe.db.set_value("Baileys Message", msg_name, "status", status, update_modified=False)
        frappe.db.commit()
        frappe.publish_realtime(
            "helpdesk:baileys-status-update",
            message={"message_id": message_id, "status": status,
                     "jid": key.get("remoteJid", ""), "line": line.name},
            after_commit=True,
        )
    return {"status": "ok"}


# ── Agent send ────────────────────────────────────────────────────────────────

@frappe.whitelist()
def send_evolution_reply(
    ticket: str = None,
    jid: str = None,
    message: str = "",
    content_type: str = "text",
    media_url: str | None = None,
    reply_to_message_id: str | None = None,
    reply_to_text: str | None = None,
    reply_to_from_me: bool = False,
    mentioned_jids: str | None = None,
) -> dict:
    """Send a text reply via Evolution API."""
    settings = _settings()
    if not settings.enabled:
        frappe.throw(_("Evolution API is not enabled."))

    if not jid and ticket:
        jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
    if not jid:
        frappe.throw(_("No WhatsApp JID provided."))

    # Resolve which line owns this ticket/JID
    line_name = None
    if ticket:
        line_name = frappe.db.get_value("HD Ticket", ticket, "baileys_line")
    if not line_name:
        # Fallback: find most recent message for this JID
        line_name = frappe.db.get_value(
            "Baileys Message",
            {"jid": jid, "line": ["is", "set"]},
            "line",
            order_by="creation desc",
        )
    if not line_name:
        frappe.throw(_("Cannot determine WhatsApp line for this conversation."))

    line = frappe.get_doc("Evolution Line", line_name)

    if settings.append_agent_initials:
        suffix = f"\n^{_agent_initials()}"
        full_message = f"{message}{suffix}" if message else suffix.strip()
    else:
        full_message = message or ""

    payload: dict = {"number": jid, "text": full_message}
    if mentioned_jids:
        jids_list = frappe.parse_json(mentioned_jids) if isinstance(mentioned_jids, str) else mentioned_jids
        if jids_list:
            payload["mentionsEveryOne"] = False
            payload["mentioned"] = jids_list

    try:
        resp = _requests.post(
            _url("message/sendText", line.instance_name),
            json=payload,
            headers=_headers(line),
            timeout=15,
        )
        resp.raise_for_status()
        sent_id = resp.json().get("key", {}).get("id") or resp.json().get("messageId", "")
    except Exception as e:
        frappe.throw(_("Evolution API send failed: {0}").format(str(e)))

    sender_name = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
    msg_doc = frappe.get_doc({
        "doctype": "Baileys Message",
        "direction": "Outgoing",
        "jid": jid,
        "sender_jid": "",
        "sender_name": sender_name,
        "profile_name": "",
        "message": full_message,
        "content_type": content_type,
        "media_url": media_url or "",
        "message_id": sent_id,
        "reply_to_message_id": reply_to_message_id or "",
        "status": "Sent",
        "reference_doctype": "HD Ticket" if ticket else "",
        "reference_name": ticket or "",
        "line": line.name,
        "is_read": 1,
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

    _publish_evolution_event(jid, is_incoming=False, line=line.name, ticket=ticket or "")
    return {"name": msg_doc.name, "message_id": sent_id, "status": "Sent"}


@frappe.whitelist()
def send_evolution_reaction(
    ticket: str = None,
    jid: str = None,
    target_message_id: str = "",
    emoji: str = "",
) -> dict:
    """Send a reaction to a message via Evolution API."""
    settings = _settings()
    if not settings.enabled:
        frappe.throw(_("Evolution API is not enabled."))

    if not jid and ticket:
        jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
    if not jid or not target_message_id or not emoji:
        frappe.throw(_("jid, target_message_id and emoji are required."))

    line_name = (
        frappe.db.get_value("HD Ticket", ticket, "baileys_line") if ticket
        else frappe.db.get_value("Baileys Message",
                                  {"jid": jid, "line": ["is", "set"]},
                                  "line", order_by="creation desc")
    )
    if not line_name:
        frappe.throw(_("Cannot determine WhatsApp line for this conversation."))
    line = frappe.get_doc("Evolution Line", line_name)

    target_msg = frappe.db.get_value(
        "Baileys Message",
        {"message_id": target_message_id},
        ["message_id", "jid", "direction"],
        as_dict=True,
    )

    reaction_payload = {
        "key": {
            "remoteJid": jid,
            "fromMe": bool(target_msg and target_msg.direction == "Outgoing"),
            "id": target_message_id,
        },
        "reaction": emoji,
    }

    try:
        resp = _requests.post(
            _url("message/sendReaction", line.instance_name),
            json=reaction_payload,
            headers=_headers(line),
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        frappe.throw(_("Evolution API reaction failed: {0}").format(str(e)))

    return {"status": "ok"}


@frappe.whitelist(allow_guest=False)
def send_evolution_media(
    ticket: str = None,
    jid: str = None,
    message: str = "",
    content_type: str = "document",
) -> dict:
    """Upload file to Frappe storage and send via Evolution API."""
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

    return send_evolution_reply(
        ticket=ticket,
        jid=jid,
        message=message,
        content_type=content_type,
        media_url=public_url,
    )


# ── Utility APIs ──────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_evolution_lines() -> list[dict]:
    """Return all Evolution Lines with unread counts — used by the sidebar."""
    lines = frappe.get_all(
        "Evolution Line",
        fields=["name", "label", "instance_name"],
        order_by="label asc",
    )
    for line in lines:
        line["display_label"] = line["label"] or line["instance_name"]
        line["unread"] = frappe.db.count(
            "Baileys Message",
            {"line": line["name"], "direction": "Incoming", "is_read": 0},
        )
    return lines


@frappe.whitelist()
def get_evolution_conversations(line: str = "") -> list[dict]:
    """Return one entry per unique JID for the given line, sorted by most-recent first."""
    from frappe.query_builder import DocType
    from frappe.query_builder.functions import Max

    BM = DocType("Baileys Message")

    q = (
        frappe.qb.from_(BM)
        .select(BM.jid, Max(BM.creation).as_("latest_creation"))
        .where(~BM.jid.like("%@broadcast"))
    )
    if line:
        q = q.where(BM.line == line)

    latest = q.groupby(BM.jid)

    BM2 = DocType("Baileys Message")
    rows = (
        frappe.qb.from_(BM2)
        .join(latest).on(
            (BM2.jid == latest.jid) & (BM2.creation == latest.latest_creation)
        )
        .select(BM2.jid, BM2.sender_name, BM2.message,
                BM2.content_type, BM2.direction, BM2.creation)
        .orderby(BM2.creation, order=frappe.qb.desc)
        .run(as_dict=True)
    )

    seen: set[str] = set()
    deduped = []
    for r in rows:
        if r.jid and r.jid not in seen:
            seen.add(r.jid)
            deduped.append(r)

    line_doc = frappe.get_doc("Evolution Line", line) if line else None
    group_names = {}
    if line_doc:
        group_names = {row.jid: (row.group_name or row.jid) for row in (line_doc.group_jids or [])}

    settings = frappe.get_cached_doc("Evolution API Settings")
    restrict = settings.get("restrict_chats_by_team")
    user_teams: set[str] = set()
    user_has_any_team = False
    if restrict:
        user_teams = set(frappe.get_all("HD Team Member",
                                        filters={"user": frappe.session.user}, pluck="parent"))
        user_has_any_team = bool(user_teams)

    jids = [r.jid for r in deduped]
    contacts: dict[str, dict] = {}
    if jids:
        for c in frappe.get_all(
            "Baileys Contact",
            filters={"jid": ["in", jids]},
            fields=["jid", "custom_name", "company", "assigned_team", "phone"],
        ):
            contacts[c.jid] = c

    result = []
    for r in deduped:
        jid = r.jid
        is_grp = jid.endswith("@g.us")
        contact = contacts.get(jid, {})
        assigned_team = contact.get("assigned_team") or ""

        if restrict and user_has_any_team and assigned_team and assigned_team not in user_teams:
            continue

        if is_grp:
            display_name = (
                contact.get("custom_name")
                or group_names.get(jid)
                or f"Group {jid.split('@')[0][-10:]}"
            )
        else:
            display_name = (
                contact.get("custom_name")
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
            "last_sender_name": r.get("sender_name") or "" if is_grp else "",
            "last_message_time": str(r["creation"]),
            "last_direction": r.get("direction", "Incoming"),
            "content_type": r.get("content_type", "text"),
        })

    return result


@frappe.whitelist()
def mark_evolution_messages_read(jid: str = "", ticket: str = "") -> int:
    """Mark all unread incoming Baileys Messages for a JID as read."""
    if not jid and ticket:
        jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
    if not jid:
        return 0

    filters: dict = {"jid": jid, "direction": "Incoming", "is_read": 0}
    unread = frappe.get_all("Baileys Message", filters=filters, fields=["name"])
    for row in unread:
        frappe.db.set_value("Baileys Message", row.name, "is_read", 1, update_modified=False)

    if unread:
        frappe.db.commit()

    return len(unread)


@frappe.whitelist()
def get_evolution_group_participants(jid: str, line: str) -> list[dict]:
    """Fetch group participants from Evolution API and enrich names from Baileys Contacts."""
    settings = _settings()
    if not settings.enabled or not settings.server_url:
        frappe.throw(_("Evolution API not configured or disabled"))
    line_doc = frappe.get_doc("Evolution Line", line)
    try:
        resp = _requests.get(
            _url("group/findParticipants", line_doc.instance_name),
            params={"groupJid": jid},
            headers=_headers(line_doc),
            timeout=10,
        )
        resp.raise_for_status()
        participants = resp.json().get("participants", [])
    except Exception as e:
        frappe.throw(_("Failed to fetch group participants: {0}").format(str(e)))

    normalised = []
    for p in participants:
        p_id = p.get("id") or ""
        phone = _phone_from_jid(p_id) if p_id.endswith("@s.whatsapp.net") else ""
        normalised.append({
            "jid": p_id,
            "phone": phone,
            "name": "",
            "isAdmin": p.get("admin") in ("admin", "superadmin"),
        })

    for p in normalised:
        if p.get("name"):
            continue
        for lj in [p["jid"], f"{p['phone']}@s.whatsapp.net" if p["phone"] else ""]:
            if not lj:
                continue
            name = frappe.db.get_value("Baileys Contact", {"jid": lj}, "custom_name")
            if name:
                p["name"] = name
                break

    return normalised


@frappe.whitelist()
def get_evolution_instance_status(line: str) -> dict:
    """Return connection state for a given Evolution Line."""
    settings = _settings()
    if not settings.enabled or not settings.server_url:
        return {"connected": False, "error": "Evolution API not configured"}
    line_doc = frappe.get_doc("Evolution Line", line)
    try:
        resp = _requests.get(
            _url("instance/connectionState", line_doc.instance_name),
            headers=_headers(line_doc),
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        state = (data.get("instance") or {}).get("state") or ""
        return {"connected": state == "open", "state": state}
    except Exception as e:
        return {"connected": False, "error": str(e)}
