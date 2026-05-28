# helpdesk/integrations/wa.py
import re

import frappe
import requests as _requests
from frappe import _
from frappe.utils import now_datetime, time_diff_in_hours
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _build_evo_session() -> _requests.Session:
	sess = _requests.Session()
	# Retry only idempotent methods (not POST) to avoid duplicate sends.
	retry = Retry(
		total=3,
		backoff_factor=0.5,
		status_forcelist=[429, 500, 502, 503, 504],
		allowed_methods=frozenset(["DELETE", "GET", "HEAD", "OPTIONS", "PUT", "TRACE"]),
		raise_on_status=False,
	)
	adapter = HTTPAdapter(max_retries=retry)
	sess.mount("http://", adapter)
	sess.mount("https://", adapter)
	return sess


_evo_session: _requests.Session = _build_evo_session()

# Redis lock keys for long-running sync operations.
_LOCK_SYNC_GROUPS = "wa:sync_groups:lock"
_LOCK_SYNC_MESSAGES_PREFIX = "wa:sync_old_messages:lock:"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _settings():
    return frappe.get_cached_doc("WA API Settings")


def _shared_settings():
    """Shared WA settings for both integrations (WhatsApp Helpdesk Settings)."""
    return frappe.get_cached_doc("WhatsApp Helpdesk Settings")


def _line(instance_name: str):
    """Return the WA Line doc for the given instance_name, or throw."""
    names = frappe.get_all(
        "WA Line", filters={"instance_name": instance_name}, pluck="name", limit=1
    )
    if not names:
        frappe.throw(
            _("Unknown WA instance: {0}").format(instance_name),
            frappe.AuthenticationError,
        )
    return frappe.get_doc("WA Line", names[0])


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


def _extract_edit(raw_msg: dict) -> tuple[str, bool]:
    """Detect an edited-message payload and return (new_text, True) or ('', False)."""
    # Shape 1: editedMessage wrapper → message → protocolMessage → editedMessage
    proto_via_edit = (
        (raw_msg.get("editedMessage") or {})
        .get("message", {})
        .get("protocolMessage") or {}
    )
    if proto_via_edit:
        inner = proto_via_edit.get("editedMessage") or {}
        text = inner.get("conversation") or (inner.get("extendedTextMessage") or {}).get("text") or ""
        if text:
            return text, True

    # Shape 2: direct protocolMessage with type 14
    proto = raw_msg.get("protocolMessage") or {}
    if proto.get("type") == 14:
        inner = proto.get("editedMessage") or {}
        text = inner.get("conversation") or (inner.get("extendedTextMessage") or {}).get("text") or ""
        if text:
            return text, True

    return "", False


def _apply_edit(msg_name: str, new_text: str, edited_by: str, jid: str, line) -> None:
    """Append old text to history, update message, publish realtime edit event."""
    doc = frappe.get_doc("WA Message", msg_name)
    if doc.message == new_text:
        return
    doc.append("edit_history", {
        "old_message": doc.message or "",
        "edited_at": frappe.utils.now(),
        "edited_by": edited_by,
    })
    doc.message = new_text
    doc.is_edited = 1
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    frappe.publish_realtime(
        "helpdesk:whatsapp-message-edit",
        message={
            "message_id": doc.message_id,
            "new_text": new_text,
            "name": msg_name,
            "jid": jid,
            "line": line.name,
        },
        after_commit=True,
    )


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


def _fw_settings():
    """Return WhatsApp Helpdesk Settings (frappe_whatsapp integration config), or None."""
    if not frappe.db.exists("DocType", "WhatsApp Helpdesk Settings"):
        return None
    try:
        return frappe.get_cached_doc("WhatsApp Helpdesk Settings")
    except Exception:
        return None


def _fw_allow_template_outside_window() -> bool:
    s = _fw_settings()
    return bool(s and s.allow_template_outside_window) if s else False


def match_phone_to_contact(phone: str) -> str | None:
    """Match a raw phone number to a Frappe Contact name."""
    normalized = _normalize_phone(phone)
    if not normalized:
        return None
    contacts = frappe.get_all(
        "Contact",
        fields=["name", "phone", "mobile_no"],
        or_filters={"phone": ("is", "set"), "mobile_no": ("is", "set")},
    )
    for c in contacts:
        if _normalize_phone(c.phone) == normalized or _normalize_phone(c.mobile_no) == normalized:
            return c.name
    for row in frappe.get_all("Contact Phone", fields=["parent", "phone"], filters={"parenttype": "Contact"}):
        if _normalize_phone(row.phone) == normalized:
            return row.parent
    return None


def get_contact_phone(ticket: str) -> str | None:
    """Resolve a phone number for the contact linked to an HD Ticket."""
    contact_name = frappe.db.get_value("HD Ticket", ticket, "contact")
    if contact_name:
        phone = frappe.db.get_value("Contact", contact_name, "mobile_no") or frappe.db.get_value("Contact", contact_name, "phone")
        if phone:
            return phone
    from frappe.query_builder import DocType as _DocType
    WM = _DocType("WhatsApp Message")
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


def _publish_fw_message(ticket_name: str, is_incoming: bool) -> None:
    """Publish realtime event for a frappe_whatsapp message linked to a ticket."""
    frappe.db.commit()
    frappe.publish_realtime(
        "helpdesk:whatsapp-message",
        message={"ticket": str(ticket_name), "is_incoming": is_incoming},
    )


def _notify_fw_agents(ticket_name: str, message: str | None, profile_name: str) -> None:
    """Create HD Notification for agents assigned to a frappe_whatsapp ticket."""
    assign_json = frappe.db.get_value("HD Ticket", ticket_name, "_assign") or "[]"
    assignees = frappe.parse_json(assign_json) or []
    if not assignees:
        return
    preview = (message or "")[:80] or "sent a WhatsApp message"
    existing = frappe.get_all(
        "HD Notification",
        filters={"reference_ticket": ticket_name, "notification_type": "WhatsApp", "read": 0},
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
                "message": f"{profile_name}: {preview}",
            }).insert(ignore_permissions=True)
        except Exception:
            pass


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
    """Return (text, content_type) from a raw WA API message object."""
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


_EXT_MAP = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
    "video/mp4": "mp4", "video/quicktime": "mov",
    "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a",
    "application/pdf": "pdf",
}


def _save_base64_media(b64: str, mime: str) -> str:
    """Decode base64 media, save as Frappe File, return relative file_url."""
    import base64 as _base64
    try:
        if b64.startswith("data:"):
            header, _, encoded = b64.partition(",")
            mime = header.split(";")[0].replace("data:", "") or mime or "application/octet-stream"
            raw_bytes = _base64.b64decode(encoded)
        else:
            raw_bytes = _base64.b64decode(b64)
        ext = _EXT_MAP.get(mime.split(";")[0], "bin")
        fname = f"wa_media_{frappe.generate_hash(length=8)}.{ext}"
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": fname,
            "content": raw_bytes,
            "is_private": 0,
        })
        file_doc.insert(ignore_permissions=True)
        return file_doc.file_url
    except Exception:
        return ""


def _download_media_via_wa(line, full_data: dict) -> str:
    """Call WA API /chat/getBase64FromMediaMessage.
    Only needs the message key — WA decrypts the CDN-encrypted media using its Baileys session."""
    key = full_data.get("key") or {}
    if not key.get("id"):
        return ""
    # Evolution v2.3+ uses /chat/ prefix, only needs the message key
    endpoint = _url("chat/getBase64FromMediaMessage", line.instance_name)
    payload = {"message": {"key": key}}
    try:
        resp = _evo_session.post(endpoint, json=payload, headers=_headers(line), timeout=60)
        if not resp.ok:
            frappe.log_error(f"WA download failed {resp.status_code}: {resp.text[:200]}", "WA Media Download")
            return ""
        result = resp.json()
        b64 = result.get("base64") or result.get("data") or result.get("buffer") or ""
        mime = result.get("mimetype") or result.get("mediaType") or "application/octet-stream"
        if b64:
            return _save_base64_media(b64, mime)
        frappe.log_error(f"WA download: no base64 in response keys={list(result.keys())}", "WA Media Download")
    except Exception as e:
        frappe.log_error(f"WA download exception: {e}", "WA Media Download")
    return ""


@frappe.whitelist()
def refetch_media_for_message(message_name: str) -> str:
    """Re-download media for an existing Baileys Message via WA API. Returns new local URL."""
    doc = frappe.get_doc("WA Message", message_name)

    if not doc.line or not doc.message_id:
        return ""

    try:
        line = frappe.get_doc("WA Line", doc.line)
    except Exception:
        return ""

    full_data = {
        "key": {
            "remoteJid": doc.jid,
            "fromMe": doc.direction == "Outgoing",
            "id": doc.message_id,
        },
    }
    new_url = _download_media_via_wa(line, full_data)
    if new_url:
        frappe.db.set_value("WA Message", message_name, "media_url", new_url)
        frappe.db.commit()
        return new_url
    return ""


def _extract_media_url(msg: dict, line=None, full_webhook_data: dict | None = None) -> str:
    """Download and permanently save media from an incoming WhatsApp message.
    WhatsApp CDN files (.enc) are AES-encrypted — only Evolution (Baileys session) can decrypt."""
    inner = (
        msg.get("viewOnceMessage", {}).get("message")
        or msg.get("ephemeralMessage", {}).get("message")
        or msg.get("documentWithCaptionMessage", {}).get("message")
        or msg
    )
    for media_key in ("imageMessage", "videoMessage", "audioMessage", "documentMessage", "stickerMessage"):
        if media_key not in inner:
            continue
        media_msg = inner[media_key]
        mime = (media_msg.get("mimetype") or "application/octet-stream").split(";")[0]

        # 1. Inline base64 (requires webhook_base64: true on the Evolution instance)
        b64 = media_msg.get("base64") or ""
        if b64:
            saved = _save_base64_media(b64, mime)
            if saved:
                return saved

        # 2. Evolution /chat/getBase64FromMediaMessage — only needs message key
        if line and full_webhook_data:
            saved = _download_media_via_wa(line, full_webhook_data)
            if saved:
                return saved

        # 3. Last resort: store the raw CDN URL (encrypted, will fail to render in browser)
        cdn_url = media_msg.get("url") or ""
        if cdn_url:
            return cdn_url

        break
    return ""


def _publish_wa_event(jid: str, is_incoming: bool, line: str, ticket: str = "") -> None:
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
    quiet_minutes = int(_shared_settings().notification_quiet_minutes or 0)
    if quiet_minutes:
        recent_outgoing = frappe.db.count(
            "WA Message",
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
	"""Create-or-blank-fill a WA Contact row keyed by JID.
	If jid is a @lid and phone is known, triggers _merge_lid_into_pn immediately.
	"""
	if not jid:
		return
	# If this is a LID JID and we know the phone, merge into the PN row
	if jid.endswith("@lid") and phone:
		pn_jid = f"{_normalize_phone(phone)}@s.whatsapp.net"
		_merge_lid_into_pn(jid, pn_jid)
		return
	try:
		if frappe.db.exists("WA Contact", {"jid": jid}):
			existing = frappe.db.get_value(
				"WA Contact", {"jid": jid}, ["custom_name", "phone"], as_dict=True
			) or {}
			updates = {}
			if not existing.get("custom_name") and name:
				updates["custom_name"] = name
			if not existing.get("phone") and phone:
				updates["phone"] = phone
			if updates:
				frappe.db.set_value("WA Contact", {"jid": jid}, updates, update_modified=False)
		else:
			frappe.get_doc({
				"doctype": "WA Contact",
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


def _merge_lid_into_pn(lid_jid: str, pn_jid: str) -> None:
	"""Merge a @lid alias row into the canonical @s.whatsapp.net row.

	- Ensures the PN row exists.
	- Copies non-empty metadata (custom_name, company, assigned_team) to PN if PN has blanks.
	- Re-keys WA Message.jid and HD Ticket.baileys_jid from lid_jid → pn_jid.
	- Sets canonical_jid on the LID row so future messages are re-routed.
	Idempotent: no-op if canonical_jid already set on the LID row.
	"""
	if not lid_jid or not pn_jid:
		return
	existing_canonical = frappe.db.get_value("WA Contact", {"jid": lid_jid}, "canonical_jid")
	if existing_canonical:
		return  # already merged

	# Ensure PN row exists
	phone = _phone_from_jid(pn_jid)
	lid_row = frappe.db.get_value(
		"WA Contact", {"jid": lid_jid},
		["custom_name", "company", "assigned_team"], as_dict=True,
	) or {}
	_upsert_contact(pn_jid, phone, lid_row.get("custom_name") or "")

	# Copy metadata to PN row where PN has blanks
	pn_row = frappe.db.get_value(
		"WA Contact", {"jid": pn_jid},
		["custom_name", "company", "assigned_team"], as_dict=True,
	) or {}
	updates = {}
	for field in ("custom_name", "company", "assigned_team"):
		if not pn_row.get(field) and lid_row.get(field):
			updates[field] = lid_row[field]
	if updates:
		frappe.db.set_value("WA Contact", {"jid": pn_jid}, updates, update_modified=False)

	# Physically re-key WA Messages
	frappe.db.sql(
		"UPDATE `tabWA Message` SET jid = %s WHERE jid = %s",
		(pn_jid, lid_jid),
	)

	# Also re-key sender_jid for group messages
	frappe.db.sql(
		"UPDATE `tabWA Message` SET sender_jid = %s WHERE sender_jid = %s",
		(pn_jid, lid_jid),
	)

	# Physically re-key HD Ticket.baileys_jid (field may not exist on all sites)
	try:
		frappe.db.sql(
			"UPDATE `tabHD Ticket` SET baileys_jid = %s WHERE baileys_jid = %s",
			(pn_jid, lid_jid),
		)
	except Exception:
		pass

	# Mark LID row as dead alias
	frappe.db.set_value("WA Contact", {"jid": lid_jid}, "canonical_jid", pn_jid, update_modified=False)
	frappe.db.commit()


@frappe.whitelist(allow_guest=True)
def upsert_contact_mapping(lid: str = "", phone: str = "", name: str = "") -> dict:
	"""Gateway calls this with a confirmed LID↔PN pair on every message.
	Validates the API key, then triggers the merge."""
	settings = _settings()
	if not settings.enabled:
		return {"status": "disabled"}

	stored_key = settings.global_api_key or ""
	try:
		incoming_key = (frappe.request.headers.get("apikey") or
				frappe.request.headers.get("Authorization") or "")
	except Exception:
		incoming_key = stored_key  # in tests, skip auth

	if stored_key and incoming_key != stored_key:
		frappe.response["http_status_code"] = 401
		return {"error": "Unauthorized"}

	lid = (lid or "").strip()
	phone = _normalize_phone(phone or "")
	name = (name or "").strip()

	if not lid or not lid.endswith("@lid") or not phone:
		return {"status": "skipped", "reason": "missing lid or phone"}

	pn_jid = f"{phone}@s.whatsapp.net"
	_upsert_contact(pn_jid, phone, name)
	_merge_lid_into_pn(lid, pn_jid)
	return {"status": "ok", "lid": lid, "pn": pn_jid}


# ── Webhook ───────────────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def webhook():
    """Single webhook endpoint for all WA API events across all instances."""
    if not frappe.db.exists("DocType", "WA API Settings"):
        frappe.response["http_status_code"] = 503
        return {"error": "WA API Settings not configured"}

    settings = _settings()
    if not settings.enabled:
        return {"status": "disabled"}

    # Auth: WA API sends the global apikey in the request header
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
    if event == "messages.delete":
        return _handle_delete(payload.get("data") or {}, line)
    if event == "contacts.upsert":
        data = payload.get("data") or []
        if isinstance(data, dict):
            data = [data]
        _handle_contacts_upsert(data)
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

	# Re-route LID JIDs to their canonical PN JID if already merged
	try:
		_canonical = frappe.db.get_value("WA Contact", {"jid": jid}, "canonical_jid")
		if _canonical:
			jid = _canonical
	except Exception:
		pass

	# Re-route sender LID to PN for group messages
	if sender and sender != jid:
		try:
			_sender_canonical = frappe.db.get_value("WA Contact", {"jid": sender}, "canonical_jid")
			if _sender_canonical:
				sender = _sender_canonical
		except Exception:
			pass

	if _is_blocked(jid, sender, line):
		return {"status": "skipped", "reason": "blocked"}

	raw_msg = data.get("message") or {}
	text, content_type = _extract_text(raw_msg)

	# For reactions, capture the ID of the message being reacted to
	reply_to_message_id = ""
	if content_type == "reaction":
		reply_to_message_id = ((raw_msg.get("reactionMessage") or {}).get("key") or {}).get("id") or ""

	# Extract reply-to ID from quoted context message
	if not reply_to_message_id:
		ctx_info = raw_msg.get("extendedTextMessage", {}).get("contextInfo") or {}
		if not ctx_info:
			for media_key in ("imageMessage", "videoMessage", "audioMessage", "documentMessage", "stickerMessage"):
				ctx_info = raw_msg.get(media_key, {}).get("contextInfo") or {}
				if ctx_info:
					break
		reply_to_message_id = ctx_info.get("stanzaId") or ctx_info.get("quotedMessage", {}) and ctx_info.get("stanzaId") or ""

	# Extract media URL for media messages — pass full `data` so Evolution can decrypt
	media_url = ""
	if content_type in ("image", "video", "audio", "document", "sticker"):
		try:
			media_url = _extract_media_url(raw_msg, line=line, full_webhook_data=data)
		except Exception as exc:
			frappe.logger().warning(f"_extract_media_url failed for {message_id}: {exc}")
			media_url = ""

	# Detect and handle incoming edit before dedup check
	new_text, is_edit = _extract_edit(raw_msg)
	if is_edit and message_id:
		existing = frappe.db.get_value("WA Message", {"message_id": message_id}, "name")
		if existing:
			frappe.set_user("Administrator")
			_apply_edit(existing, new_text, edited_by="incoming", jid=jid, line=line)
			return {"status": "ok", "edited": True}
		# Fall through — original not yet stored (edge case: creates new record below)

	# Deduplicate (soft check — catches most cases before the DB round-trip)
	if message_id and frappe.db.exists("WA Message", {"message_id": message_id}):
		return {"status": "duplicate"}

	frappe.set_user("Administrator")

	# Use None for empty message_id so the UNIQUE index allows multiple NULLs
	stored_msg_id = message_id or None

	if from_me:
		owner = line.connected_user or "Administrator"
		if not frappe.db.exists("User", owner):
			owner = "Administrator"
		try:
			doc = frappe.get_doc({
				"doctype": "WA Message",
				"direction": "Outgoing",
				"jid": jid,
				"sender_jid": "",
				"sender_name": "(via phone)",
				"profile_name": "(via phone)",
				"message": text,
				"content_type": content_type or "text",
				"media_url": media_url,
				"message_id": stored_msg_id,
				"reply_to_message_id": reply_to_message_id,
				"status": "Delivered",
				"reference_doctype": "",
				"reference_name": "",
				"line": line.name,
				"is_read": 1,
			}).insert(ignore_permissions=True)
		except frappe.exceptions.DuplicateEntryError:
			return {"status": "duplicate"}
		frappe.db.set_value("WA Message", doc.name, "owner", owner, update_modified=False)
		_publish_wa_event(jid, is_incoming=False, line=line.name)
		return {"status": "ok", "mirrored": True}

	# Incoming message
	try:
		frappe.get_doc({
			"doctype": "WA Message",
			"direction": "Incoming",
			"jid": jid,
			"sender_jid": sender,
			"sender_name": sender_name,
			"profile_name": sender_name,
			"message": text,
			"content_type": content_type or "text",
			"media_url": media_url,
			"message_id": stored_msg_id,
			"reply_to_message_id": reply_to_message_id,
			"status": "Pending",
			"reference_doctype": "",
			"reference_name": "",
			"line": line.name,
			"is_read": 0,
		}).insert(ignore_permissions=True)
	except frappe.exceptions.DuplicateEntryError:
		return {"status": "duplicate"}

	_upsert_contact_name(jid, sender_name)
	_publish_wa_event(jid, is_incoming=True, line=line.name)
	if content_type != "reaction":
		_notify_agents(jid, text, sender_name, line, settings)

	return {"status": "ok"}


def _handle_contacts_upsert(contacts: list) -> None:
	for c in contacts:
		jid = c.get("id") or ""
		name = c.get("notify") or c.get("verifiedName") or c.get("name") or ""
		if not jid or not name:
			continue
		# For @lid JIDs, attempt to resolve phone from the payload fields
		if jid.endswith("@lid"):
			phone = _normalize_phone(c.get("phone") or "")
			if phone:
				_merge_lid_into_pn(jid, f"{phone}@s.whatsapp.net")
				continue
			# No phone in payload — create the LID row as-is; merge will happen
			# when upsert_contact_mapping delivers the confirmed LID↔PN pair
		phone = _phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else ""
		_upsert_contact(jid, phone, name)


# WA API v2 message status integer codes
_STATUS_MAP = {
    0: "Failed",     # ERROR — message could not be delivered (surface it, don't drop it)
    1: "Sent",       # PENDING
    2: "Sent",       # SERVER_ACK
    3: "Delivered",  # DELIVERY_ACK
    4: "Read",       # READ
    5: "Read",       # PLAYED (audio/video)
}


def _handle_update(updates: list, line) -> dict:
    for item in updates:
        if not isinstance(item, dict):
            continue
        key = item.get("key") or {}
        message_id = key.get("id") or ""
        raw_status = (item.get("update") or {}).get("status")
        status = _STATUS_MAP.get(raw_status) if raw_status is not None else None
        if not message_id or not status:
            continue
        msg_name = frappe.db.get_value("WA Message", {"message_id": message_id}, "name")
        if not msg_name:
            continue
        frappe.db.set_value("WA Message", msg_name, "status", status, update_modified=False)
        frappe.db.commit()
        frappe.publish_realtime(
            "helpdesk:baileys-status-update",
            message={"message_id": message_id, "status": status,
                     "jid": key.get("remoteJid", ""), "line": line.name},
            after_commit=True,
        )
    return {"status": "ok"}


def _handle_delete(data: dict, line) -> dict:
    """Mark locally-stored messages as deleted when Evolution API fires messages.delete."""
    ids = data.get("ids") or []
    if isinstance(ids, str):
        ids = [ids]
    deleted = 0
    for message_id in ids:
        if not message_id:
            continue
        msg_name = frappe.db.get_value("WA Message", {"message_id": message_id}, "name")
        if not msg_name:
            continue
        try:
            frappe.db.set_value("WA Message", msg_name, "status", "Failed", update_modified=False)
            frappe.db.commit()
            deleted += 1
        except Exception as e:
            frappe.log_error(f"Failed to mark WA Message {msg_name} as deleted: {e}", "WA Message Delete")
    return {"status": "ok", "deleted": deleted}


# ── Agent send ────────────────────────────────────────────────────────────────

_MIME_MAP = {"image": "image/jpeg", "video": "video/mp4", "audio": "audio/ogg", "document": "application/octet-stream"}


def _evo_send_message(
    line,
    jid: str,
    full_message: str,
    content_type: str = "text",
    media_url: str | None = None,
    reply_to_message_id: str | None = None,
    mentioned_jids=None,
) -> str:
    """POST a single message to the Evolution API and return the sent message id.

    Raises on any failure (timeout, non-2xx, connection error). Callers are responsible
    for deciding what a failure means (persist as Failed, throw to the user, etc.).
    `full_message` is sent verbatim — agent-initials suffixing happens in the caller.
    """
    # Build quoted context for WhatsApp reply threading
    quoted_key: dict | None = None
    if reply_to_message_id:
        target = frappe.db.get_value(
            "WA Message",
            {"message_id": reply_to_message_id},
            ["message_id", "direction", "sender_jid"],
            as_dict=True,
        )
        if target:
            is_from_me = target.direction == "Outgoing"
            participant = "" if not _is_group(jid) else (
                target.sender_jid if not is_from_me else ""
            )
            quoted_key = {"remoteJid": jid, "fromMe": is_from_me, "id": reply_to_message_id}
            if participant:
                quoted_key["participant"] = participant

    if isinstance(mentioned_jids, str):
        mentioned_jids = frappe.parse_json(mentioned_jids) if mentioned_jids.strip() else None

    if media_url and content_type in ("image", "video", "audio", "document"):
        abs_url = media_url if media_url.startswith("http") else frappe.utils.get_url(media_url)
        payload: dict = {
            "number": jid,
            "mediatype": content_type,
            "mimetype": _MIME_MAP.get(content_type, "application/octet-stream"),
            "media": abs_url,
            "caption": full_message,
        }
        endpoint, timeout = "message/sendMedia", 30
    else:
        payload = {"number": jid, "text": full_message}
        endpoint, timeout = "message/sendText", 15

    if quoted_key:
        payload["quoted"] = {"key": quoted_key}
    if mentioned_jids:
        payload["mentionsEveryOne"] = False
        payload["mentioned"] = mentioned_jids

    resp = _evo_session.post(
        _url(endpoint, line.instance_name),
        json=payload,
        headers=_headers(line),
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("key", {}).get("id") or resp.json().get("messageId", "")


@frappe.whitelist()
def send_wa_reply(
    ticket: str = None,
    jid: str = None,
    line: str = None,
    message: str = "",
    content_type: str = "text",
    media_url: str | None = None,
    reply_to_message_id: str | None = None,
    reply_to_text: str | None = None,
    reply_to_from_me: bool = False,
    mentioned_jids: str | None = None,
) -> dict:
    """Send a text reply via WA API (Baileys) or frappe_whatsapp depending on ticket type."""
    # ── frappe_whatsapp path ─────────────────────────────────────────────────
    if ticket and not jid:
        try:
            jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
        except Exception:
            pass
        if not jid:
            return _send_fw_reply(ticket=ticket, message=message, content_type=content_type, media_url=media_url)

    # ── Baileys/WA path ──────────────────────────────────────────────────────
    settings = _settings()
    if not settings.enabled:
        frappe.throw(_("WA API is not enabled."))

    if not jid and ticket:
        jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
    if not jid:
        frappe.throw(_("No WhatsApp JID provided."))

    # Resolve which line owns this ticket/JID — explicit param wins, then ticket field, then message history
    line_name = line or None
    if not line_name and ticket:
        line_name = frappe.db.get_value("HD Ticket", ticket, "baileys_line")
    if not line_name:
        line_name = frappe.db.get_value(
            "WA Message",
            {"jid": jid, "line": ["is", "set"]},
            "line",
            order_by="creation desc",
        )
    if not line_name:
        # Last resort: use the only configured line if there is exactly one
        all_lines = frappe.get_all("WA Line", pluck="name", limit=2)
        if len(all_lines) == 1:
            line_name = all_lines[0]
    if not line_name:
        frappe.throw(_("Cannot determine WhatsApp line for this conversation."))

    line = frappe.get_doc("WA Line", line_name)

    if _shared_settings().append_agent_initials:
        suffix = f"\n^{_agent_initials()}"
        full_message = f"{message}{suffix}" if message else suffix.strip()
    else:
        full_message = message or ""

    # Attempt the send. Pre-send failures (bad jid / line / disabled settings) have
    # already raised above; a failure HERE means Evolution rejected or timed out, so we
    # persist a Failed message the agent can see (and retry) instead of dropping it silently.
    try:
        sent_id = _evo_send_message(
            line,
            jid,
            full_message,
            content_type=content_type,
            media_url=media_url,
            reply_to_message_id=reply_to_message_id,
            mentioned_jids=mentioned_jids,
        )
        status = "Sent"
        send_error = None
    except Exception as e:
        sent_id = ""
        status = "Failed"
        send_error = str(e)
        frappe.log_error(f"WA send failed for {jid}: {e}", "WA Send")

    sender_name = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
    msg_doc = frappe.get_doc({
        "doctype": "WA Message",
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
        "status": status,
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
        # Only advance the ticket to the "agent replied" status when the reply actually went out.
        if status == "Sent":
            shared = _shared_settings()
            if shared.agent_reply_status:
                _set_ticket_status(ticket, shared.agent_reply_status)

    _publish_wa_event(jid, is_incoming=False, line=line.name, ticket=ticket or "")
    result = {"name": msg_doc.name, "message_id": sent_id, "status": status}
    if send_error:
        result["error"] = send_error
    return result


@frappe.whitelist()
def send_wa_reaction(
    ticket: str = None,
    jid: str = None,
    target_message_id: str = "",
    emoji: str = "",
) -> dict:
    """Send a reaction to a message via WA API (Baileys) or frappe_whatsapp depending on ticket type."""
    # ── frappe_whatsapp path ─────────────────────────────────────────────────
    if ticket and not jid:
        try:
            jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
        except Exception:
            pass
        if not jid:
            return _send_fw_reaction(ticket=ticket, target_message_id=target_message_id, emoji=emoji)

    # ── Baileys/WA path ──────────────────────────────────────────────────────
    settings = _settings()
    if not settings.enabled:
        frappe.throw(_("WA API is not enabled."))
    if not jid or not target_message_id or not emoji:
        frappe.throw(_("jid, target_message_id and emoji are required."))

    line_name = (
        frappe.db.get_value("HD Ticket", ticket, "baileys_line") if ticket
        else frappe.db.get_value("WA Message",
                                  {"jid": jid, "line": ["is", "set"]},
                                  "line", order_by="creation desc")
    )
    if not line_name:
        frappe.throw(_("Cannot determine WhatsApp line for this conversation."))
    line = frappe.get_doc("WA Line", line_name)

    target_msg = frappe.db.get_value(
        "WA Message",
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
        resp = _evo_session.post(
            _url("message/sendReaction", line.instance_name),
            json=reaction_payload,
            headers=_headers(line),
            timeout=10,
        )
        resp.raise_for_status()
        sent_id = resp.json().get("key", {}).get("id") or frappe.generate_hash(length=16)
    except Exception as e:
        frappe.log_error(f"WA reaction failed for {jid}: {e}", "WA Send Reaction")
        frappe.throw(_("WA API reaction failed: {0}").format(str(e)))

    sender_name = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
    frappe.get_doc({
        "doctype": "WA Message",
        "direction": "Outgoing",
        "jid": jid,
        "sender_name": sender_name,
        "message": emoji,
        "content_type": "reaction",
        "media_url": "",
        "message_id": sent_id,
        "reply_to_message_id": target_message_id,
        "line": line.name,
        "is_read": 1,
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    _publish_wa_event(jid, is_incoming=False, line=line.name)
    return {"status": "ok"}


@frappe.whitelist()
def edit_wa_message(message_name: str, new_text: str) -> dict:
    """Edit an outgoing text message via WA API and update local record."""
    settings = _settings()
    if not settings.enabled:
        frappe.throw(_("WA API is not enabled."))

    doc = frappe.get_doc("WA Message", message_name)

    if doc.direction != "Outgoing":
        frappe.throw(_("Only outgoing messages can be edited."))
    if doc.content_type != "text":
        frappe.throw(_("Only text messages can be edited."))
    if not (new_text or "").strip():
        frappe.throw(_("Edit text cannot be empty."))
    if doc.owner != frappe.session.user and "System Manager" not in frappe.get_roles():
        frappe.throw(_("You can only edit your own messages."))

    line = frappe.get_doc("WA Line", doc.line)

    try:
        resp = _evo_session.put(
            _url("message/updateMessage", line.instance_name),
            json={
                "number": doc.jid,
                "key": {
                    "id": doc.message_id,
                    "fromMe": True,
                    "remoteJid": doc.jid,
                },
                "text": new_text,
            },
            headers=_headers(line),
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        frappe.log_error(f"WA edit failed for message {message_name}: {e}", "WA Edit Message")
        frappe.throw(_("WA API edit failed: {0}").format(str(e)))

    agent_name = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
    _apply_edit(doc.name, new_text, edited_by=agent_name, jid=doc.jid, line=line)

    return {"status": "ok", "name": doc.name}


@frappe.whitelist(allow_guest=False)
def send_wa_media(
    ticket: str = None,
    jid: str = None,
    message: str = "",
    content_type: str = "document",
) -> dict:
    """Upload file to Frappe storage and send via WA API."""
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
    # Store relative path — send_wa_reply converts to absolute for the API call
    relative_url = file_doc.file_url

    return send_wa_reply(
        ticket=ticket,
        jid=jid,
        message=message,
        content_type=content_type,
        media_url=relative_url,
    )


@frappe.whitelist()
def retry_wa_message(message_name: str) -> dict:
    """Re-send a previously Failed outgoing WA Message in place (Evolution / WA Line path).

    Reuses the stored content/jid/line so the agent can recover from a transient send
    failure without re-typing or re-attaching. Throws on failure so the explicit retry
    action gives immediate feedback; the bubble stays Failed until a retry succeeds.
    """
    if not _settings().enabled:
        frappe.throw(_("WA API is not enabled."))

    doc = frappe.get_doc("WA Message", message_name)
    if doc.direction != "Outgoing":
        frappe.throw(_("Only outgoing messages can be retried."))
    if doc.content_type == "reaction":
        frappe.throw(_("Reactions cannot be retried."))
    if doc.owner != frappe.session.user and "System Manager" not in frappe.get_roles():
        frappe.throw(_("You can only retry your own messages."))
    if not doc.line:
        frappe.throw(_("Cannot determine the WhatsApp line for this message."))

    line = frappe.get_doc("WA Line", doc.line)
    try:
        # doc.message already includes any agent-initials suffix — send verbatim.
        sent_id = _evo_send_message(
            line,
            doc.jid,
            doc.message or "",
            content_type=doc.content_type,
            media_url=doc.media_url or None,
            reply_to_message_id=doc.reply_to_message_id or None,
        )
    except Exception as e:
        frappe.log_error(f"WA retry failed for {message_name}: {e}", "WA Retry")
        frappe.throw(_("Retry failed: {0}").format(str(e)))

    doc.db_set("message_id", sent_id, update_modified=False)
    doc.db_set("status", "Sent", update_modified=False)
    frappe.db.commit()
    _publish_wa_event(doc.jid, is_incoming=False, line=line.name, ticket=doc.reference_name or "")
    return {"name": doc.name, "message_id": sent_id, "status": "Sent"}


# ── Utility APIs ──────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_wa_lines() -> list[dict]:
    """Return all WA Lines with unread counts — used by the sidebar."""
    if not frappe.db.exists("DocType", "WA Line"):
        return []
    lines = frappe.get_all(
        "WA Line",
        fields=["name", "label", "instance_name"],
        order_by="label asc",
    )
    wa_msg_exists = frappe.db.exists("DocType", "WA Message")
    for line in lines:
        line["display_label"] = line["label"] or line["instance_name"]
        line["unread"] = (
            frappe.db.count(
                "WA Message",
                {"line": line["name"], "direction": "Incoming", "is_read": 0},
            )
            if wa_msg_exists
            else 0
        )
    return lines


@frappe.whitelist()
def get_wa_conversations(line: str = "") -> list[dict]:
    """Return one entry per unique JID for the given line, sorted by most-recent first."""
    # Raw SQL — frappe.qb subquery join generates a derived table without an alias,
    # which MySQL rejects with OperationalError 1248.
    line_filter = "AND bm.line = %(line)s" if line else ""
    sub_filter = "AND line = %(line)s" if line else ""
    rows = frappe.db.sql(
        f"""
        SELECT bm.jid, bm.sender_name, bm.message, bm.content_type, bm.direction, bm.creation
        FROM `tabWA Message` bm
        INNER JOIN (
            SELECT jid, MAX(creation) AS latest_creation
            FROM `tabWA Message`
            WHERE jid NOT LIKE '%%@broadcast'
            {sub_filter}
            GROUP BY jid
        ) latest ON bm.jid = latest.jid AND bm.creation = latest.latest_creation
        WHERE bm.jid NOT LIKE '%%@broadcast'
        {line_filter}
        ORDER BY bm.creation DESC
        """,
        {"line": line},
        as_dict=True,
    )

    seen: set[str] = set()
    deduped = []
    for r in rows:
        if r.jid and r.jid not in seen:
            seen.add(r.jid)
            deduped.append(r)

    line_doc = frappe.get_doc("WA Line", line) if line else None
    group_names = {}
    if line_doc:
        group_names = {row.jid: (row.group_name or row.jid) for row in (line_doc.group_jids or [])}

    restrict = _shared_settings().restrict_chats_by_team
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
            "WA Contact",
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
def mark_wa_messages_read(jid: str = "", ticket: str = "") -> int:
    """Mark all unread incoming messages as read (Baileys or frappe_whatsapp)."""
    if not jid and ticket:
        try:
            jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
        except Exception:
            pass

    if jid:
        # Baileys path
        filters: dict = {"jid": jid, "direction": "Incoming", "is_read": 0}
        unread = frappe.get_all("WA Message", filters=filters, fields=["name"])
        for row in unread:
            frappe.db.set_value("WA Message", row.name, "is_read", 1, update_modified=False)
        if unread:
            frappe.db.commit()
        return len(unread)

    if not ticket or not frappe.db.exists("DocType", "WhatsApp Message"):
        return 0

    # frappe_whatsapp path: send read receipts
    count = 0
    unread_fw = frappe.get_all(
        "WhatsApp Message",
        filters={
            "reference_doctype": "HD Ticket",
            "reference_name": ticket,
            "type": "Incoming",
            "status": ["!=", "marked as read"],
        },
        fields=["name", "message_id"],
    )
    for row in unread_fw:
        if not row.message_id:
            continue
        try:
            msg_doc = frappe.get_doc("WhatsApp Message", row.name)
            msg_doc.send_read_receipt()
            count += 1
        except Exception:
            pass
    return count


@frappe.whitelist()
def mark_all_wa_messages_read(line: str) -> int:
    """Mark all unread incoming Baileys Messages for an entire line as read."""
    if not line:
        return 0
    filters: dict = {"line": line, "direction": "Incoming", "is_read": 0}
    unread = frappe.get_all("WA Message", filters=filters, fields=["name"])
    for row in unread:
        frappe.db.set_value("WA Message", row.name, "is_read", 1, update_modified=False)
    if unread:
        frappe.db.commit()
    return len(unread)


@frappe.whitelist()
def get_wa_group_participants(jid: str, line: str) -> list[dict]:
    """Fetch group participants from WA API and enrich names from WA Contacts."""
    if not line:
        return []
    settings = _settings()
    if not settings.enabled or not settings.server_url:
        return []
    line_doc = frappe.get_doc("WA Line", line)
    api_participants = None
    try:
        resp = _evo_session.get(
            _url("group/findParticipants", line_doc.instance_name),
            params={"groupJid": jid},
            headers=_headers(line_doc),
            timeout=10,
        )
        resp.raise_for_status()
        api_participants = resp.json().get("participants", [])
    except _requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            api_participants = None  # group deleted — fall back to local history
        else:
            frappe.log_error(f"get_wa_group_participants failed for {jid}: {e}")
            api_participants = None
    except Exception as e:
        frappe.log_error(f"get_wa_group_participants failed for {jid}: {e}")
        api_participants = None

    if api_participants is not None:
        normalised = []
        for p in api_participants:
            p_id = p.get("id") or ""
            phone = _phone_from_jid(p_id) if p_id.endswith("@s.whatsapp.net") else ""
            normalised.append({
                "jid": p_id,
                "phone": phone,
                "name": "",
                "isAdmin": p.get("admin") in ("admin", "superadmin"),
            })
    else:
        # Fall back to senders seen in WA Message history for this group JID
        rows = frappe.db.sql(
            """
            SELECT sender_jid,
                   MAX(sender_name)  AS sender_name,
                   MAX(profile_name) AS profile_name
            FROM `tabWA Message`
            WHERE jid = %s
              AND direction = 'Incoming'
              AND sender_jid IS NOT NULL AND sender_jid != ''
            GROUP BY sender_jid
            """,
            jid,
            as_dict=True,
        )
        normalised = []
        for row in rows:
            p_id = row.sender_jid
            phone = _phone_from_jid(p_id) if p_id.endswith("@s.whatsapp.net") else ""
            normalised.append({
                "jid": p_id,
                "phone": phone,
                "name": row.sender_name or row.profile_name or "",
                "isAdmin": False,
            })

    # Enrich names from WA Contact (custom_name), then fall back to WA Message profile_name
    for p in normalised:
        if p.get("name"):
            continue
        lookup_jids = [p["jid"]]
        if p["phone"]:
            lookup_jids.append(f"{p['phone']}@s.whatsapp.net")
        for lj in lookup_jids:
            if not lj:
                continue
            row = frappe.db.get_value(
                "WA Contact", {"jid": lj}, ["custom_name", "canonical_jid"], as_dict=True
            )
            if row:
                if row.get("custom_name"):
                    p["name"] = row["custom_name"]
                    break
                # LID row with a resolved canonical — try the PN row
                if row.get("canonical_jid"):
                    canon_name = frappe.db.get_value("WA Contact", {"jid": row["canonical_jid"]}, "custom_name")
                    if canon_name:
                        p["name"] = canon_name
                        break
        if not p.get("name") and p.get("phone"):
            # Last resort: most recent profile_name from WA Message history
            p["name"] = frappe.db.get_value(
                "WA Message",
                {"sender_jid": p["jid"], "profile_name": ["!=", ""]},
                "profile_name",
                order_by="creation desc",
            ) or ""

    return normalised


# ── WA Chat → Task / Ticket helpers ───────────────────────────────────────────

@frappe.whitelist()
def get_tasks_for_jid(jid: str) -> list[dict]:
    """Return HD Tasks linked (via ticket) to the WhatsApp JID."""
    if not jid:
        return []
    try:
        tickets = frappe.get_all("HD Ticket", filters={"baileys_jid": jid}, pluck="name")
    except Exception:
        return []
    if not tickets:
        return []
    return frappe.get_all(
        "HD Task",
        filters={"ticket": ["in", tickets]},
        fields=["name", "title", "status", "priority", "assigned_to", "due_date"],
        order_by="creation desc",
    )


@frappe.whitelist()
def get_contact_info_for_jid(jid: str) -> dict:
    """Return display_name, company, assigned_team, phone for a single JID."""
    if not jid:
        return {}
    contact = frappe.db.get_value(
        "WA Contact", {"jid": jid},
        ["custom_name", "company", "assigned_team", "phone"],
        as_dict=True,
    ) or {}
    phone = contact.get("phone") or (_phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else "")
    # Fall back to last message sender_name if no custom_name
    display_name = contact.get("custom_name") or ""
    if not display_name:
        display_name = frappe.db.get_value(
            "WA Message",
            {"jid": jid, "direction": "Incoming"},
            "profile_name",
            order_by="creation desc",
        ) or jid.split("@")[0]
    return {
        "display_name": display_name,
        "company": contact.get("company") or "",
        "assigned_team": contact.get("assigned_team") or "",
        "phone": phone,
    }


@frappe.whitelist()
def get_tickets_for_jid(jid: str) -> list[dict]:
    """Return HD Tickets linked to the WhatsApp JID."""
    if not jid:
        return []
    try:
        return frappe.get_all(
            "HD Ticket",
            filters={"baileys_jid": jid},
            fields=["name", "subject", "status", "priority", "creation"],
            order_by="creation desc",
        )
    except Exception:
        return []


@frappe.whitelist()
def create_task_from_chat(jid: str, line: str, title: str) -> str:
    """Create an HD Task linked to the WA conversation, creating a ticket if none exists."""
    ticket_name = frappe.db.get_value("HD Ticket", {"baileys_jid": jid}, "name")
    if not ticket_name:
        line_doc = frappe.get_doc("WA Line", line) if line else None
        ticket_data = {
            "doctype": "HD Ticket",
            "subject": title,
            "description": title,
            "ticket_channel": "WhatsApp",
            "baileys_jid": jid,
            "baileys_line": line,
        }
        if line_doc and line_doc.default_ticket_type:
            ticket_data["ticket_type"] = line_doc.default_ticket_type
        if line_doc and line_doc.default_team:
            ticket_data["agent_group"] = line_doc.default_team
        ticket = frappe.get_doc(ticket_data)
        ticket.insert(ignore_permissions=True)
        ticket_name = ticket.name
    task = frappe.get_doc({
        "doctype": "HD Task",
        "title": title,
        "ticket": ticket_name,
        "status": "Todo",
    })
    task.insert(ignore_permissions=True)
    frappe.db.commit()
    return task.name


@frappe.whitelist()
def create_ticket_from_chat(jid: str, line: str, subject: str, description: str = "") -> str:
    """Create an HD Ticket linked to the WA conversation."""
    line_doc = frappe.get_doc("WA Line", line) if line else None
    ticket_data = {
        "doctype": "HD Ticket",
        "subject": subject,
        "description": description or subject,
        "ticket_channel": "WhatsApp",
        "baileys_jid": jid,
        "baileys_line": line,
    }
    if line_doc and line_doc.default_ticket_type:
        ticket_data["ticket_type"] = line_doc.default_ticket_type
    if line_doc and line_doc.default_team:
        ticket_data["agent_group"] = line_doc.default_team
    ticket = frappe.get_doc(ticket_data)
    ticket.insert(ignore_permissions=True)
    frappe.db.commit()
    return ticket.name


@frappe.whitelist()
def configure_wa_webhook(line: str) -> dict:
	"""Register (or update) the Frappe webhook on the WA API instance."""
	settings = _settings()
	if not settings.enabled or not settings.server_url:
		frappe.throw(_("WA API not configured or disabled"))
	line_doc = frappe.get_doc("WA Line", line)
	site_url = frappe.utils.get_url().rstrip("/")
	webhook_url = f"{site_url}/api/method/helpdesk.integrations.wa.webhook"
	payload = {
		"webhook": {
			"enabled": True,
			"url": webhook_url,
			"webhook_by_events": False,
			"webhook_base64": False,
			"headers": {"apikey": _headers(line_doc)["apikey"]},
			"events": ["MESSAGES_UPSERT", "MESSAGES_UPDATE", "MESSAGES_DELETE", "CONTACTS_UPSERT"],
		}
	}
	try:
		resp = _evo_session.post(
			_url("webhook/set", line_doc.instance_name),
			headers=_headers(line_doc),
			json=payload,
			timeout=10,
		)
		resp.raise_for_status()
		return {"status": "ok", "webhook_url": webhook_url, "data": resp.json() if resp.content else {}}
	except Exception as e:
		frappe.log_error(f"Failed to configure webhook for line {line}: {e}", "WA Webhook Config")
		frappe.throw(_("Failed to configure webhook: {0}").format(str(e)))


@frappe.whitelist()
def get_wa_instance_status(line: str) -> dict:
    """Return connection state for a given WA Line."""
    settings = _settings()
    if not settings.enabled or not settings.server_url:
        return {"connected": False, "error": "WA API not configured"}
    line_doc = frappe.get_doc("WA Line", line)
    try:
        resp = _evo_session.get(
            _url("instance/connectionState", line_doc.instance_name),
            headers=_headers(line_doc),
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        state = (data.get("instance") or {}).get("state") or ""
        return {"connected": state == "open", "state": state}
    except Exception as e:
        frappe.log_error(f"WA instance status failed for {line}: {e}", "WA Instance Status")
        return {"connected": False, "error": str(e)}


@frappe.whitelist()
def get_wa_qr(line: str) -> dict:
    """Fetch QR code (or pairing code) for an WA Line instance."""
    settings = _settings()
    if not settings.enabled or not settings.server_url:
        frappe.throw(_("WA API not configured or disabled"))
    line_doc = frappe.get_doc("WA Line", line)
    try:
        resp = _evo_session.get(
            _url("instance/connect", line_doc.instance_name),
            headers=_headers(line_doc),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # WA API returns { base64: "data:image/png;base64,..." } or { code: "..." }
        return {
            "base64": data.get("base64") or data.get("qrcode", {}).get("base64") or "",
            "code": data.get("code") or "",
        }
    except Exception as e:
        frappe.log_error(f"Failed to fetch QR code for line {line}: {e}", "WA QR Code")
        frappe.throw(_("Failed to fetch QR code: {0}").format(str(e)))


# ── Migrated from baileys.py ──────────────────────────────────────────────────

@frappe.whitelist()
def get_whatsapp_messages(jid: str = None, ticket: str = None) -> list[dict]:
	"""Return messages for a conversation.

	Baileys/WA path: looks up baileys_jid from ticket and queries WA Message.
	frappe_whatsapp path: queries WhatsApp Message where reference_name = ticket.
	"""
	from frappe.query_builder import DocType

	# ── Baileys/WA path ──────────────────────────────────────────────────────
	if not jid and ticket:
		try:
			jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
		except Exception:
			pass  # column may not exist on sites without the Baileys custom field

	if jid:
		BM = DocType("WA Message")
		User = DocType("User")
		BC = DocType("WA Contact")

		rows = (
			frappe.qb.from_(BM)
			.left_join(User).on(User.name == BM.owner)
			.left_join(BC).on(BC.jid == BM.sender_jid)
			.select(
				BM.name, BM.creation, BM.direction, BM.jid, BM.message,
				BM.content_type, BM.media_url, BM.sender_jid, BM.sender_name,
				BM.profile_name, BM.message_id, BM.reply_to_message_id, BM.status, BM.owner,
				User.full_name.as_("sender_full_name"),
				BC.phone.as_("sender_phone"),
				BM.is_edited,
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
			m["edit_history"] = []

		edited_names = [m["name"] for m in rows if m.get("is_edited")]
		if edited_names:
			history_rows = frappe.db.get_all(
				"WA Message Edit History",
				filters={"parent": ["in", edited_names]},
				fields=["parent", "old_message", "edited_at", "edited_by"],
				order_by="edited_at asc",
			)
			history_map: dict = {}
			for h in history_rows:
				if h.get("edited_at") and not isinstance(h["edited_at"], str):
					h["edited_at"] = str(h["edited_at"])
				history_map.setdefault(h["parent"], []).append(h)
			for m in rows:
				if m.get("is_edited"):
					m["edit_history"] = history_map.get(m["name"], [])

		return rows

	# ── frappe_whatsapp path ─────────────────────────────────────────────────
	if not ticket or not frappe.db.exists("DocType", "WhatsApp Message"):
		return []

	WM = DocType("WhatsApp Message")
	User = DocType("User")

	rows = (
		frappe.qb.from_(WM)
		.left_join(User).on(User.name == WM.owner)
		.select(
			WM.name, WM.creation, WM.type, WM.message, WM.content_type,
			WM.attach, WM.status, WM.profile_name, WM.message_id,
			WM.reply_to_message_id, WM.is_reply, WM.owner,
			WM["from"], WM["to"],
			User.full_name.as_("sender_full_name"),
		)
		.where(WM.reference_doctype == "HD Ticket")
		.where(WM.reference_name == ticket)
		.orderby(WM.creation)
		.run(as_dict=True)
	)

	for m in rows:
		if m.get("creation") and not isinstance(m["creation"], str):
			m["creation"] = str(m["creation"])
		# Normalise field names to match the Baileys shape the UI expects
		m["direction"] = m["type"]
		m["media_url"] = m.get("attach") or ""
		m["edit_history"] = []
		m["is_edited"] = 0

	return rows


# ── frappe_whatsapp integration handlers ──────────────────────────────────────

def _send_fw_reply(ticket: str, message: str, content_type: str = "text", media_url: str | None = None) -> dict:
	"""Create an Outgoing WhatsApp Message via frappe_whatsapp for this ticket."""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		frappe.throw(_("frappe_whatsapp is not installed."))
	phone = get_contact_phone(ticket)
	if not phone:
		frappe.throw(_("No phone number found for the contact linked to this ticket."))
	shared = _shared_settings()
	if shared.append_agent_initials and content_type == "text":
		suffix = f"\n^{_agent_initials()}"
		message = f"{message}{suffix}"
	msg_doc = frappe.get_doc({
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": phone,
		"message": message,
		"content_type": content_type,
		"attach": media_url or "",
		"reference_doctype": "HD Ticket",
		"reference_name": ticket,
	})
	msg_doc.insert(ignore_permissions=True)
	assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
	if not frappe.parse_json(assign_json):
		try:
			frappe.get_doc("HD Ticket", ticket).assign_agent(frappe.session.user)
		except Exception:
			pass
	s = _fw_settings()
	if s and s.enabled and s.agent_reply_status:
		_set_ticket_status(ticket, s.agent_reply_status)
	return {"name": msg_doc.name, "status": msg_doc.status}


def _send_fw_reaction(ticket: str, target_message_id: str, emoji: str) -> dict:
	"""Send a reaction via frappe_whatsapp for this ticket."""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		frappe.throw(_("frappe_whatsapp is not installed."))
	phone = get_contact_phone(ticket)
	if not phone:
		frappe.throw(_("No phone number found for the contact linked to this ticket."))
	msg_doc = frappe.get_doc({
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": phone,
		"message": emoji,
		"content_type": "reaction",
		"reply_to_message_id": target_message_id,
		"reference_doctype": "HD Ticket",
		"reference_name": ticket,
	})
	msg_doc.insert(ignore_permissions=True)
	return {"name": msg_doc.name, "status": msg_doc.status}


def on_whatsapp_message_update(doc, method=None):
	"""Publish status changes for frappe_whatsapp messages linked to HD Tickets."""
	if doc.reference_doctype != "HD Ticket" or not doc.reference_name:
		return
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
	"""Create or link an HD Ticket when a frappe_whatsapp message arrives."""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return

	s = _fw_settings()
	if not s or not s.enabled:
		return

	# Outgoing: link to ticket and update status
	if doc.type != "Incoming":
		if doc.reference_doctype == "HD Ticket" and doc.reference_name:
			if s.agent_reply_status:
				_set_ticket_status(doc.reference_name, s.agent_reply_status)
			_publish_fw_message(doc.reference_name, is_incoming=False)
		return

	phone = _normalize_phone(doc.get("from") or "")
	if not phone:
		return

	contact_name = match_phone_to_contact(phone)
	placeholder_domain = s.placeholder_email_domain or "whatsapp.placeholder.local"
	profile_name = doc.profile_name or f"WhatsApp User {phone}"

	if not contact_name:
		action = s.unknown_contact_action or "Skip Ticket Creation"
		if action == "Skip Ticket Creation":
			return
		if action == "Create Contact and Ticket":
			original_user = frappe.session.user
			frappe.set_user("Administrator")
			try:
				c = frappe.get_doc({
					"doctype": "Contact",
					"first_name": profile_name,
					"phone_nos": [{"doctype": "Contact Phone", "phone": phone, "is_primary_mobile_no": 1}],
				})
				c.insert(ignore_permissions=True)
				contact_name = c.name
			finally:
				frappe.set_user(original_user)

	if contact_name:
		email = frappe.db.get_value("Contact", contact_name, "email_id") or f"whatsapp+{phone}@{placeholder_domain}"
	else:
		email = f"whatsapp+{phone}@{placeholder_domain}"

	# Find an existing open ticket for this phone number
	from frappe.query_builder import DocType as _DocType
	WM = _DocType("WhatsApp Message")
	linked = (
		frappe.qb.from_(WM)
		.select(WM.reference_name, WM.creation)
		.where(WM.reference_doctype == "HD Ticket")
		.where(WM.type == "Incoming")
		.where(WM["from"] == doc.get("from"))
		.orderby(WM.creation, order=frappe.qb.desc)
		.limit(1)
		.run(as_dict=True)
	)

	existing_ticket = None
	if linked and linked[0].reference_name:
		candidate = linked[0].reference_name
		status_category = frappe.db.get_value("HD Ticket", candidate, "status_category")
		if status_category and status_category != "Resolved":
			timeout = int(s.new_conversation_timeout_hours or 24)
			if time_diff_in_hours(now_datetime(), linked[0].creation) < timeout:
				existing_ticket = candidate

	if existing_ticket:
		doc.db_set("reference_doctype", "HD Ticket", update_modified=False)
		doc.db_set("reference_name", existing_ticket, update_modified=False)
		if s.customer_reply_status:
			_set_ticket_status(existing_ticket, s.customer_reply_status)
		_notify_fw_agents(existing_ticket, doc.message, profile_name)
		_publish_fw_message(existing_ticket, is_incoming=True)
	else:
		subject = (doc.message or "")[:100] or f"WhatsApp from {profile_name}"
		ticket_data = {
			"doctype": "HD Ticket",
			"subject": subject,
			"raised_by": email,
			"description": doc.message or "",
			"via_customer_portal": 0,
		}
		if contact_name:
			ticket_data["contact"] = contact_name
		if s.default_ticket_type:
			ticket_data["ticket_type"] = s.default_ticket_type
		if s.default_team:
			ticket_data["agent_group"] = s.default_team

		original_user = frappe.session.user
		frappe.set_user("Administrator")
		try:
			ticket_doc = frappe.get_doc(ticket_data)
			ticket_doc.insert(ignore_permissions=True)
		finally:
			frappe.set_user(original_user)

		doc.db_set("reference_doctype", "HD Ticket", update_modified=False)
		doc.db_set("reference_name", ticket_doc.name, update_modified=False)
		_notify_fw_agents(ticket_doc.name, doc.message, profile_name)
		_publish_fw_message(ticket_doc.name, is_incoming=True)


@frappe.whitelist()
def save_whatsapp_contact(jid: str, custom_name: str = "", company: str = "", assigned_team: str = "", phone: str = "") -> dict:
	"""Create or update a WA Contact override for a JID."""
	custom_name = (custom_name or "").strip()
	company = (company or "").strip()
	assigned_team = (assigned_team or "").strip()
	phone = _normalize_phone(phone or "")

	# Redirect writes on dead LID alias rows to the canonical PN row
	canonical = frappe.db.get_value("WA Contact", {"jid": jid}, "canonical_jid")
	if canonical:
		jid = canonical

	if frappe.db.exists("WA Contact", {"jid": jid}):
		doc = frappe.get_doc("WA Contact", {"jid": jid})
		doc.custom_name = custom_name
		doc.company = company
		doc.assigned_team = assigned_team
		if phone:
			doc.phone = phone
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc({
			"doctype": "WA Contact",
			"jid": jid,
			"phone": phone or (_phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else ""),
			"custom_name": custom_name,
			"company": company,
			"assigned_team": assigned_team,
		}).insert(ignore_permissions=True)

	return {"status": "ok", "jid": jid, "custom_name": custom_name, "company": company, "phone": phone, "assigned_team": assigned_team}


@frappe.whitelist()
def search_whatsapp_contacts(query: str = "") -> list[dict]:
	"""Search WA Contacts and standard Contacts by name/phone. Returns up to 30 matches."""
	q = (query or "").strip()
	like = f"%{q}%"

	if q:
		wa_rows = frappe.db.sql(
			"""
			SELECT jid, custom_name, phone, company
			FROM `tabWA Contact`
			WHERE jid NOT LIKE '%%@broadcast'
			  AND (canonical_jid IS NULL OR canonical_jid = '')
			  AND (custom_name LIKE %s OR phone LIKE %s OR company LIKE %s)
			ORDER BY custom_name ASC
			LIMIT 30
			""",
			(like, like, like),
			as_dict=True,
		)
	else:
		wa_rows = frappe.get_all(
			"WA Contact",
			filters=[
				["jid", "not like", "%@broadcast"],
				["canonical_jid", "in", ["", None]],
			],
			fields=["jid", "custom_name", "phone", "company"],
			order_by="custom_name asc",
			limit=30,
		)

	seen_phones: set[str] = {_normalize_phone(r.phone) for r in wa_rows if r.phone}
	result = list(wa_rows)

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
def get_hd_teams() -> list[dict]:
	"""Return all HD Teams for the team assignment dropdown."""
	return frappe.get_all("HD Team", fields=["name"], order_by="name asc")


@frappe.whitelist()
def get_hd_customers(query: str = "") -> list[dict]:
	"""Search HD Customers by name for the contact editor autocomplete."""
	q = (query or "").strip()
	filters = [["customer_name", "like", f"%{q}%"]] if q else []
	return frappe.get_all(
		"HD Customer",
		filters=filters,
		fields=["name", "customer_name", "domain"],
		order_by="customer_name asc",
		limit=15,
	)


@frappe.whitelist()
def get_customer_notes(customer: str) -> dict:
	"""Return helpdesk_notes for an HD Customer."""
	if not customer:
		return {"notes": ""}
	notes = frappe.db.get_value("HD Customer", customer, "helpdesk_notes") or ""
	return {"notes": notes}


@frappe.whitelist()
def get_whatsapp_ticket_info(ticket: str) -> dict:
	"""Return WhatsApp metadata for the ticket activity tab.

	Handles two paths:
	  - Baileys/WA tickets: HD Ticket has baileys_jid custom field
	  - frappe_whatsapp tickets: WhatsApp Message docs linked via reference_name
	"""
	# ── Baileys/WA path ──────────────────────────────────────────────────────
	jid = None
	try:
		jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	except Exception:
		pass  # column may not exist on fresh sites without Baileys custom field

	if jid:
		is_grp = _is_group(jid)
		group_name = None
		if is_grp:
			group_name = frappe.db.get_value("WA Contact", {"jid": jid}, "custom_name") or jid.split("@")[0]
		assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
		assigned_users = frappe.parse_json(assign_json) or []
		baileys_line = None
		try:
			baileys_line = frappe.db.get_value("HD Ticket", ticket, "baileys_line")
		except Exception:
			pass
		return {
			"has_whatsapp": True,
			"jid": jid,
			"is_group": is_grp,
			"group_name": group_name,
			"baileys_line": baileys_line,
			"is_assigned": frappe.session.user in assigned_users,
			"assignees": assigned_users,
			"reply_window_open": True,
		}

	# ── frappe_whatsapp path ─────────────────────────────────────────────────
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return {"has_whatsapp": False}

	has_msg = frappe.db.exists("WhatsApp Message", {
		"reference_doctype": "HD Ticket",
		"reference_name": ticket,
	})
	if not has_msg:
		return {"has_whatsapp": False}

	# Determine if the 24-hour reply window is open
	last_incoming = frappe.db.get_value(
		"WhatsApp Message",
		{"reference_doctype": "HD Ticket", "reference_name": ticket, "type": "Incoming"},
		"creation",
		order_by="creation desc",
	)
	window_open = True
	if last_incoming:
		window_open = time_diff_in_hours(now_datetime(), last_incoming) < 24

	assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
	assigned_users = frappe.parse_json(assign_json) or []

	return {
		"has_whatsapp": True,
		"jid": None,
		"is_group": False,
		"is_assigned": frappe.session.user in assigned_users,
		"assignees": assigned_users,
		"reply_window_open": window_open,
		"allow_template_outside_window": _fw_allow_template_outside_window(),
		"via_frappe_whatsapp": True,
	}


@frappe.whitelist()
def pickup_whatsapp_ticket(ticket: str) -> dict:
	"""Assign the current agent to a WhatsApp ticket."""
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


@frappe.whitelist()
def get_whatsapp_analytics(from_date: str = None, to_date: str = None, line: str = None) -> dict:
	"""Return WhatsApp analytics for the given date range, optionally filtered by WA Line."""
	from collections import defaultdict
	from frappe.utils import add_days, today

	if not from_date:
		from_date = add_days(today(), -30)
	if not to_date:
		to_date = today()

	from_dt = f"{from_date} 00:00:00"
	to_dt = f"{to_date} 23:59:59"

	line_filter = "AND line = %(line)s" if line else ""
	params_base = {"from_dt": from_dt, "to_dt": to_dt, "line": line or ""}

	summary = frappe.db.sql(
		f"""
		SELECT
			COUNT(*) as total,
			SUM(direction = 'Incoming') as incoming,
			SUM(direction = 'Outgoing') as outgoing,
			COUNT(DISTINCT jid) as conversations
		FROM `tabWA Message`
		WHERE creation BETWEEN %(from_dt)s AND %(to_dt)s
		  AND content_type != 'reaction'
		  {line_filter}
		""",
		params_base,
		as_dict=True,
	)[0]

	daily = frappe.db.sql(
		f"""
		SELECT
			DATE(creation) as date,
			COUNT(*) as total,
			SUM(direction = 'Incoming') as incoming,
			SUM(direction = 'Outgoing') as outgoing
		FROM `tabWA Message`
		WHERE creation BETWEEN %(from_dt)s AND %(to_dt)s
		  AND content_type != 'reaction'
		  {line_filter}
		GROUP BY DATE(creation)
		ORDER BY date ASC
		""",
		params_base,
		as_dict=True,
	)

	hourly = frappe.db.sql(
		f"""
		SELECT HOUR(creation) as hour, COUNT(*) as total
		FROM `tabWA Message`
		WHERE creation BETWEEN %(from_dt)s AND %(to_dt)s
		  AND content_type != 'reaction'
		  {line_filter}
		GROUP BY HOUR(creation)
		ORDER BY hour ASC
		""",
		params_base,
		as_dict=True,
	)
	hourly_map = {r.hour: r.total for r in hourly}
	hourly_full = [{"hour": h, "total": hourly_map.get(h, 0)} for h in range(24)]

	top_raw = frappe.db.sql(
		f"""
		SELECT
			jid,
			COUNT(*) as total,
			SUM(direction = 'Incoming') as incoming,
			SUM(direction = 'Outgoing') as outgoing,
			MAX(sender_name) as sender_name
		FROM `tabWA Message`
		WHERE creation BETWEEN %(from_dt)s AND %(to_dt)s
		  AND jid NOT LIKE '%%@broadcast'
		  AND content_type != 'reaction'
		  {line_filter}
		GROUP BY jid
		ORDER BY total DESC
		LIMIT 15
		""",
		params_base,
		as_dict=True,
	)

	jids = [r.jid for r in top_raw]
	contacts: dict = {}
	if jids:
		for c in frappe.get_all(
			"WA Contact",
			filters={"jid": ["in", jids]},
			fields=["jid", "custom_name", "company"],
		):
			contacts[c.jid] = c

	if line and frappe.db.exists("WA Line", line):
		line_doc = frappe.get_doc("WA Line", line)
		group_names = {row.jid: (row.group_name or row.jid) for row in (line_doc.group_jids or [])}
	else:
		group_names = {}

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

	raw_replies = frappe.db.sql(
		f"""
		SELECT
			bm_out.owner AS agent_user,
			bm_out.sender_name AS agent_name,
			TIMESTAMPDIFF(MINUTE, bm_in.creation, bm_out.creation) AS response_minutes
		FROM `tabWA Message` bm_out
		INNER JOIN `tabWA Message` bm_in ON (
			bm_in.jid = bm_out.jid
			AND bm_in.direction = 'Incoming'
			AND bm_in.content_type != 'reaction'
			AND bm_in.creation = (
				SELECT MAX(b2.creation)
				FROM `tabWA Message` b2
				WHERE b2.jid = bm_out.jid
				  AND b2.direction = 'Incoming'
				  AND b2.content_type != 'reaction'
				  AND b2.creation < bm_out.creation
			)
		)
		WHERE bm_out.direction = 'Outgoing'
		  AND bm_out.content_type NOT IN ('reaction')
		  AND bm_out.creation BETWEEN %(from_dt)s AND %(to_dt)s
		  AND TIMESTAMPDIFF(MINUTE, bm_in.creation, bm_out.creation) BETWEEN 0 AND 1440
		  {line_filter.replace('line =', 'bm_out.line =')}
		""",
		params_base,
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
def enqueue_wa_sync() -> dict:
	"""Queue a background job that runs sync_wa_contacts then sync_wa_groups."""
	frappe.enqueue(
		"helpdesk.integrations.wa._run_wa_sync_job",
		queue="long",
		timeout=300,
		now=False,
	)
	return {"status": "queued"}


@frappe.whitelist()
def wipe_wa_contacts() -> dict:
	"""Delete all WA Contact rows. Used before a clean resync to remove LID/PN duplicates."""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	count = frappe.db.count("WA Contact")
	frappe.db.sql("DELETE FROM `tabWA Contact`")
	frappe.db.commit()
	return {"deleted": count}


def _run_wa_sync_job() -> None:
	"""Background job: sync contacts then groups, emit realtime event when done."""
	try:
		contact_result = sync_wa_contacts()
		group_result = sync_wa_groups()
		frappe.publish_realtime(
			"helpdesk:wa-sync-complete",
			message={
				"contacts": contact_result.get("total", 0),
				"groups": group_result.get("total", 0),
			},
		)
	except Exception as exc:
		frappe.log_error(str(exc), "WA Sync Job Failed")
		frappe.publish_realtime(
			"helpdesk:wa-sync-complete",
			message={"error": str(exc)},
		)


@frappe.whitelist()
def sync_wa_contacts() -> dict:
	"""Upsert WA Contacts from local WA Message sender history using a single bulk SQL."""
	rows = frappe.db.sql(
		"""
		SELECT sender_jid,
		       MAX(sender_name)   AS sender_name,
		       MAX(profile_name)  AS profile_name
		FROM `tabWA Message`
		WHERE direction = 'Incoming'
		  AND sender_jid IS NOT NULL AND sender_jid != ''
		  AND sender_jid NOT LIKE '%%@broadcast'
		  AND sender_jid NOT LIKE '%%@g.us'
		GROUP BY sender_jid
		""",
		as_dict=True,
	)
	if not rows:
		return {"created": 0, "updated": 0, "total": 0}

	values = []
	for row in rows:
		jid = row.sender_jid
		name = row.sender_name or row.profile_name or ""
		phone = _phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else ""
		if not phone:
			continue  # Skip unresolved @lid contacts — the gateway will populate them on first message
		doc_name = frappe.generate_hash(length=10)
		values.append((doc_name, jid, phone, name))

	placeholders = ", ".join(["(%s, %s, %s, %s, '', '')" for _ in values])
	flat_values = tuple(item for v in values for item in v)
	frappe.db.sql(
		"""
		INSERT INTO `tabWA Contact` (name, jid, phone, custom_name, company, assigned_team)
		VALUES {placeholders}
		ON DUPLICATE KEY UPDATE
		    custom_name = IF(custom_name IS NULL OR custom_name = '', VALUES(custom_name), custom_name),
		    phone       = IF(phone IS NULL OR phone = '', VALUES(phone), phone)
		""".format(placeholders=placeholders),
		flat_values,
	)
	frappe.db.commit()
	return {"created": len(values), "updated": 0, "total": len(values)}


@frappe.whitelist()
def sync_wa_old_messages(line: str, limit_per_chat: int = 50) -> dict:
	"""Enqueue a background job to import historical messages for a WA Line.

	Returns immediately with {"status": "queued"}.  The job fires
	helpdesk:wa-old-sync-complete when done.  A per-line Redis lock prevents
	duplicate jobs from running within 5 minutes.
	"""
	settings = _settings()
	if not settings.enabled or not settings.server_url:
		frappe.throw(_("WA API not configured or disabled"))

	lock_key = f"{_LOCK_SYNC_MESSAGES_PREFIX}{line}"
	if frappe.cache().get_value(lock_key):
		return {"status": "locked"}

	frappe.cache().set_value(lock_key, 1, expires_in_sec=300)
	frappe.enqueue(
		"helpdesk.integrations.wa._run_sync_old_messages_job",
		line=line,
		limit_per_chat=int(limit_per_chat or 50),
		queue="long",
		timeout=600,
	)
	return {"status": "queued"}


def _run_sync_old_messages_job(line: str, limit_per_chat: int = 50) -> None:
	"""Background job: import historical messages then emit wa-old-sync-complete."""
	import datetime as _dt

	lock_key = f"{_LOCK_SYNC_MESSAGES_PREFIX}{line}"
	try:
		frappe.set_user("Administrator")
		settings = _settings()
		total_limit = max(1, min(limit_per_chat * 20, 5000))
		line_doc = frappe.get_doc("WA Line", line)

		imported = skipped = 0
		current_page = 1
		total_pages = 1

		while current_page <= total_pages:
			try:
				resp = _evo_session.post(
					_url("chat/findMessages", line_doc.instance_name),
					json={"where": {}, "page": current_page},
					headers=_headers(line_doc),
					timeout=30,
				)
				resp.raise_for_status()
				raw = resp.json()
			except Exception as e:
				frappe.log_error(f"sync_wa_old_messages page {current_page} failed for {line}: {e}", "WA Old Message Sync")
				break

			msgs_envelope = raw.get("messages") or {}
			total_pages = int(msgs_envelope.get("pages") or 1)
			records = msgs_envelope.get("records") or []

			for msg in records:
				key = msg.get("key") or {}
				message_id = key.get("id") or ""
				remote_jid = key.get("remoteJid") or ""
				if not message_id or not remote_jid or "@broadcast" in remote_jid:
					continue
				if frappe.db.exists("WA Message", {"message_id": message_id}):
					skipped += 1
					continue

				from_me = bool(key.get("fromMe"))
				sender = key.get("participant") or (remote_jid if not from_me else "")
				sender_name = msg.get("pushName") or ""
				raw_msg_body = msg.get("message") or {}
				text, content_type = _extract_text(raw_msg_body)

				try:
					doc = frappe.get_doc({
						"doctype": "WA Message",
						"direction": "Outgoing" if from_me else "Incoming",
						"jid": remote_jid,
						"sender_jid": "" if from_me else sender,
						"sender_name": "(via phone)" if from_me else sender_name,
						"profile_name": "(via phone)" if from_me else sender_name,
						"message": text,
						"content_type": content_type or "text",
						"media_url": "",
						"message_id": message_id,
						"status": "Read" if from_me else "Delivered",
						"line": line_doc.name,
						"is_read": 1,
					})
					doc.insert(ignore_permissions=True)
					ts = msg.get("messageTimestamp") or 0
					if ts:
						orig_creation = _dt.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
						frappe.db.set_value("WA Message", doc.name, "creation", orig_creation, update_modified=False)
					imported += 1
				except Exception as e:
					frappe.log_error(f"Failed to insert WA Message (line={line}): {e}", "WA Old Message Sync")

			frappe.db.commit()
			current_page += 1
			if imported + skipped >= total_limit:
				break

		frappe.publish_realtime(
			"helpdesk:wa-old-sync-complete",
			message={"line": line, "imported": imported, "skipped": skipped},
		)
	except Exception as exc:
		frappe.log_error(str(exc), "WA Old Message Sync Job Failed")
		frappe.publish_realtime(
			"helpdesk:wa-old-sync-complete",
			message={"line": line, "error": str(exc)},
		)
	finally:
		frappe.cache().delete_key(lock_key)


@frappe.whitelist()
def get_outgoing_templates() -> list[dict]:
	"""Placeholder — template-outside-window feature not yet implemented for WA API."""
	return []


@frappe.whitelist()
def send_template_to_ticket(ticket: str, template_name: str) -> dict:
	"""Placeholder — template-outside-window feature not yet implemented for WA API."""
	frappe.throw(_("Template sending outside the 24-hour window is not yet supported."))


@frappe.whitelist()
def get_product_options() -> list[str]:
	"""Placeholder — product options feature not yet implemented for WA API."""
	return []


@frappe.whitelist()
def sync_wa_groups() -> dict:
	"""Fetch groups from all WA Lines via the API and upsert into each line's group_jids child table.

	Deduplication: a Redis lock prevents concurrent or back-to-back runs within 60 seconds.
	"""
	if frappe.cache().get_value(_LOCK_SYNC_GROUPS):
		return {"status": "locked", "created": 0, "updated": 0, "total": 0}

	frappe.cache().set_value(_LOCK_SYNC_GROUPS, 1, expires_in_sec=60)

	settings = _settings()
	if not settings.enabled or not settings.server_url:
		frappe.throw(_("WA API not configured or disabled"))

	lines = frappe.get_all("WA Line", pluck="name")
	created = updated = 0

	for line_name in lines:
		line_doc = frappe.get_doc("WA Line", line_name)
		try:
			resp = _evo_session.get(
				_url("group/fetchAllGroups", line_doc.instance_name),
				params={"getParticipants": "false"},
				headers=_headers(line_doc),
				timeout=90,
			)
			resp.raise_for_status()
			raw = resp.json()
			groups = raw if isinstance(raw, list) else raw.get("groups", [])
		except _requests.exceptions.HTTPError as e:
			if e.response is not None and e.response.status_code == 404:
				continue  # instance not registered on Evolution API — skip silently
			frappe.log_error(f"sync_wa_groups failed for line {line_name}: {e}", "WA Group Sync")
			continue
		except Exception as e:
			frappe.log_error(f"sync_wa_groups failed for line {line_name}: {e}", "WA Group Sync")
			continue

		# Existing JIDs for this line in the child table
		existing_rows = frappe.db.get_all(
			"WhatsApp Group JID",
			filters={"parent": line_name, "parenttype": "WA Line"},
			fields=["name", "jid", "group_name"],
		)
		existing = {r.jid: r for r in existing_rows}

		insert_rows = []
		for g in groups:
			jid = g.get("id") or ""
			if not jid or not jid.endswith("@g.us"):
				continue
			subject = g.get("subject") or g.get("name") or ""

			if jid in existing:
				if subject and existing[jid].group_name != subject:
					frappe.db.set_value(
						"WhatsApp Group JID", existing[jid].name, "group_name", subject, update_modified=False
					)
					updated += 1
			else:
				insert_rows.append((
					frappe.generate_hash(length=10),
					line_name, "WA Line", "group_jids",
					len(existing) + len(insert_rows) + 1,
					jid, subject or "",
				))
				created += 1

		if insert_rows:
			frappe.db.sql(
				"""
				INSERT INTO `tabWhatsApp Group JID`
				    (name, parent, parenttype, parentfield, idx, jid, group_name)
				VALUES {placeholders}
				""".format(placeholders=", ".join(["(%s,%s,%s,%s,%s,%s,%s)"] * len(insert_rows))),
				[v for row in insert_rows for v in row],
			)
			frappe.db.commit()
		elif updated:
			frappe.db.commit()

	return {"created": created, "updated": updated, "total": created + updated}
