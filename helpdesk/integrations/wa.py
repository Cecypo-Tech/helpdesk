# helpdesk/integrations/wa.py
import json
import re
import time as _time
from urllib.parse import quote as _urlquote

import frappe
import requests as _requests
from frappe import _
from frappe.database import savepoint
from frappe.utils import add_to_date, cint, get_datetime, now_datetime, time_diff_in_hours
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
_LOCK_SYNC_EVO_CONTACTS = "wa:sync_evo_contacts:lock"

# Ceiling on chat/fetchProfile calls per WA Line per sync run. Each call is a
# live WhatsApp profile lookup, and bulk lookups are what anti-scraping
# heuristics watch for — so the backlog drains over successive runs instead of
# in one burst. Only numbers already in our WA Message history are ever queried.
_PROFILE_LOOKUP_LIMIT = 200
# Spacing between consecutive fetchProfile calls, for the same reason.
_PROFILE_LOOKUP_DELAY = 0.3
# How long after a run live profile lookups stay on cooldown. Surfaced to the
# operator rather than silently returning "no names found".
_PROFILE_COOLDOWN_SEC = 300


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


def _is_placeholder_name(name: str) -> bool:
    """True when a contact name carries no information beyond the phone number.

    WhatsApp sends the raw number as pushName when the sender has never set a
    profile name, so a WA Contact can look "named" while telling an agent
    nothing. Rows matching this are treated as blank by every enrichment pass —
    which is also what protects an agent's manually typed name from being
    overwritten, since a real name never matches.
    """
    stripped = (name or "").strip()
    if not stripped:
        return True
    return bool(re.fullmatch(r"[\d\s\+\-\(\)]+", stripped))


def _is_group(jid: str) -> bool:
    return jid.endswith("@g.us")


def _extract_edit(raw_msg: dict) -> tuple[str, bool, str]:
    """Detect an edited-message payload.

    Returns (new_text, True, original_message_id) or ('', False, '').
    The original_message_id comes from the inner protocolMessage.key.id — this is the ID
    of the message being edited, NOT the outer data.key.id which may be a new wrapper ID.
    """
    # Shape 1: editedMessage wrapper → message → protocolMessage → editedMessage
    proto_via_edit = (
        (raw_msg.get("editedMessage") or {})
        .get("message", {})
        .get("protocolMessage") or {}
    )
    if proto_via_edit:
        inner = proto_via_edit.get("editedMessage") or {}
        text = inner.get("conversation") or (inner.get("extendedTextMessage") or {}).get("text") or ""
        original_id = (proto_via_edit.get("key") or {}).get("id") or ""
        if text:
            return text, True, original_id

    # Shape 2: direct protocolMessage with type 14
    proto = raw_msg.get("protocolMessage") or {}
    if int(proto.get("type") or 0) == 14:
        inner = proto.get("editedMessage") or {}
        text = inner.get("conversation") or (inner.get("extendedTextMessage") or {}).get("text") or ""
        original_id = (proto.get("key") or {}).get("id") or ""
        if text:
            return text, True, original_id

    return "", False, ""


def _extract_reply_target(data: dict, raw_msg: dict, content_type: str) -> str:
    """Return the message_id this message quotes/replies to, or ''.

    The quote context (`contextInfo.stanzaId`) lives in different places depending
    on the message shape:
      - reactions: `reactionMessage.key.id`
      - media replies: nested in the media sub-message (`imageMessage.contextInfo` …)
      - plain-text replies: Evolution v2 delivers these as a bare `conversation`
        and puts the quote at the TOP-LEVEL `data.contextInfo`, not inside `message`.
        Missing this last case is why text-reply quote boxes never rendered.
    """
    if content_type == "reaction":
        return ((raw_msg.get("reactionMessage") or {}).get("key") or {}).get("id") or ""

    ctx_info = raw_msg.get("extendedTextMessage", {}).get("contextInfo") or {}
    if not ctx_info:
        for media_key in ("imageMessage", "videoMessage", "audioMessage", "documentMessage", "stickerMessage"):
            ctx_info = raw_msg.get(media_key, {}).get("contextInfo") or {}
            if ctx_info:
                break
    if not ctx_info:
        ctx_info = data.get("contextInfo") or {}
    return ctx_info.get("stanzaId") or ""


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


_EDIT_UNRECOVERABLE_NOTE = " [edited on WhatsApp — new text unavailable]"


def _flag_unrecoverable_edit(msg_name: str, jid: str, line) -> None:
    """Mark a message as edited when WhatsApp's newer edit protocol (secretEncryptedMessage)
    arrives — Evolution API/Baileys cannot decrypt this, so the new text is genuinely
    unrecoverable here. Keep the original text (best info we have) and flag it instead
    of silently losing the edit or inserting a stray empty message."""
    doc = frappe.get_doc("WA Message", msg_name)
    if doc.edit_unrecoverable:
        return
    doc.append("edit_history", {
        "old_message": doc.message or "",
        "edited_at": frappe.utils.now(),
        "edited_by": "incoming (undecryptable)",
    })
    doc.message = (doc.message or "") + _EDIT_UNRECOVERABLE_NOTE
    doc.is_edited = 1
    doc.edit_unrecoverable = 1
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    frappe.publish_realtime(
        "helpdesk:whatsapp-message-edit",
        message={
            "message_id": doc.message_id,
            "new_text": doc.message,
            "name": msg_name,
            "jid": jid,
            "line": line.name,
            "edit_unrecoverable": True,
        },
        after_commit=True,
    )


def _publish_wa_delete(jid: str, message_id: str, line_name: str) -> None:
    """Tell open chats a message was removed, so the bubble flips without a reload."""
    frappe.publish_realtime(
        "helpdesk:whatsapp-message-delete",
        message={"message_id": message_id, "jid": jid, "line": line_name},
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
    if frappe.session.user in ("Guest", ""):
        return "BOT"
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


def _fw_conversation_phone(ticket: str | int) -> str | None:
    """The customer's digits for this ticket's WhatsApp conversation.

    normalized_phone holds the other party either way — sender on an incoming
    row, recipient on an outgoing one — so the newest message linked to the
    ticket identifies the person regardless of who wrote last.

    None when the custom field has not been migrated onto this site yet, which
    is a real window on every deploy.
    """
    if not _wa_has_normalized_phone():
        return None
    return frappe.db.get_value(
        "WhatsApp Message",
        {"reference_doctype": "HD Ticket", "reference_name": ticket},
        "normalized_phone",
        order_by="creation desc",
    )


def _fw_reply_window_open(ticket: str | int) -> bool:
    """True when Meta still accepts free-form messages to this ticket's customer.

    The window runs 24 hours from the customer's last *incoming* message. With
    no incoming message at all the conversation is business-initiated, which
    Meta permits only via a template — so the window is closed, not open.

    Meta scopes that window to the **phone number**, not to a ticket: one
    person, one window, wherever their messages landed. Scoping it per ticket
    told agents "the 24-hour reply window has closed" whenever the customer's
    latest message had opened a *new* ticket — the previous one having been
    resolved, closed, or aged past the conversation timeout — which is the
    normal course of events rather than an edge case. The reply would have been
    accepted; we refused to send it.

    Keyed on normalized_phone, which is indexed for exactly this kind of lookup.
    """
    phone = _fw_conversation_phone(ticket)
    if phone:
        last_incoming = frappe.db.get_value(
            "WhatsApp Message",
            {"normalized_phone": phone, "type": "Incoming"},
            "creation",
            order_by="creation desc",
        )
    else:
        # Pre-migration fallback: per-ticket, as before.
        last_incoming = frappe.db.get_value(
            "WhatsApp Message",
            {"reference_doctype": "HD Ticket", "reference_name": ticket, "type": "Incoming"},
            "creation",
            order_by="creation desc",
        )
    if not last_incoming:
        return False
    return time_diff_in_hours(now_datetime(), last_incoming) < 24


# Digits of the subscriber number compared when two numbers are stored in
# different formats. Long enough that a match is not coincidence, short enough
# to survive a missing country code.
_PHONE_SUFFIX_DIGITS = 9


def _phones_match(a: str, b: str) -> bool:
    """True when two numbers denote the same subscriber.

    WhatsApp always delivers full international digits (254799123456) while
    contacts are commonly stored nationally (0799123456), so exact comparison
    silently misses. Compare the trailing subscriber digits when the two differ
    in format.
    """
    x, y = _normalize_phone(a), _normalize_phone(b)
    if not x or not y:
        return False
    if x == y:
        return True
    if len(x) < _PHONE_SUFFIX_DIGITS or len(y) < _PHONE_SUFFIX_DIGITS:
        return False
    return x[-_PHONE_SUFFIX_DIGITS:] == y[-_PHONE_SUFFIX_DIGITS:]


def _phone_suffix(number: str) -> str:
    """The trailing subscriber digits used to bucket a number for lookup.

    Shorter numbers keep their whole value, which matches _phones_match(): it
    refuses to loosely match anything under _PHONE_SUFFIX_DIGITS, so a short
    number can only ever match itself.
    """
    normalized = _normalize_phone(number)
    return normalized[-_PHONE_SUFFIX_DIGITS:] if normalized else ""


def set_contact_phone_suffix(doc, method=None):
    """Store each number's lookup key when a Contact is saved.

    match_phone_to_contact() used to load every Contact and Contact Phone row
    into Python on each inbound message. Storing the key makes that an indexed
    lookup. Assigning a field the doctype lacks is a no-op in frappe, so this is
    safe before the fixture has been migrated.
    """
    # Assign only when the value actually differs. Writing unconditionally marks
    # the parent and every phone_nos row dirty on every Contact save bench-wide,
    # which is needless write amplification on a doctype every app touches.
    suffix = _phone_suffix(doc.get("mobile_no") or doc.get("phone") or "")
    if doc.get("phone_suffix") != suffix:
        doc.phone_suffix = suffix
    for row in doc.get("phone_nos") or []:
        row_suffix = _phone_suffix(row.get("phone") or "")
        if row.get("phone_suffix") != row_suffix:
            row.phone_suffix = row_suffix


def _contact_has_phone_suffix() -> bool:
    """Whether the phone_suffix custom fields have been migrated onto this site.

    Code deploys before `bench migrate` runs; querying the column in that window
    would throw on every inbound message.
    """
    cached = frappe.flags.get("contact_phone_suffix_col")
    if cached is None:
        cached = bool(
            frappe.db.has_column("Contact", "phone_suffix")
            and frappe.db.has_column("Contact Phone", "phone_suffix")
        )
        frappe.flags.contact_phone_suffix_col = cached
    return cached


def _resolve_contact_match(normalized: str, contacts: list, phone_rows: list) -> str | None:
    """Pick a contact from candidate rows.

    Exact matches win outright. Only when nothing matches exactly do we fall
    back to comparing trailing digits, and then only if it identifies exactly
    one contact — attaching a conversation to the wrong customer is worse than
    not attaching it at all.
    """
    for c in contacts:
        if _normalize_phone(c.phone) == normalized or _normalize_phone(c.mobile_no) == normalized:
            return c.name
    for row in phone_rows:
        if _normalize_phone(row.phone) == normalized:
            return row.parent

    loose = set()
    for c in contacts:
        if _phones_match(c.phone, normalized) or _phones_match(c.mobile_no, normalized):
            loose.add(c.name)
    for row in phone_rows:
        if _phones_match(row.phone, normalized):
            loose.add(row.parent)
    return loose.pop() if len(loose) == 1 else None


def match_phone_to_contact(phone: str) -> str | None:
    """Match a raw phone number to a Frappe Contact name.

    Narrows to candidates with an indexed lookup on the stored phone_suffix,
    then applies the exact-then-unique-loose rules over those few rows. Falls
    back to scanning when the columns are not migrated yet — same answer, just
    linear in contact count.
    """
    normalized = _normalize_phone(phone)
    if not normalized:
        return None
    if not _contact_has_phone_suffix():
        return _match_phone_to_contact_scan(normalized)

    suffix = _phone_suffix(normalized)
    contacts = frappe.get_all(
        "Contact",
        filters={"phone_suffix": suffix},
        fields=["name", "phone", "mobile_no"],
    )
    phone_rows = frappe.get_all(
        "Contact Phone",
        filters={"parenttype": "Contact", "phone_suffix": suffix},
        fields=["parent", "phone"],
    )
    return _resolve_contact_match(normalized, contacts, phone_rows)


def _match_phone_to_contact_scan(normalized: str) -> str | None:
    """Pre-index implementation, kept for sites without the phone_suffix fields.

    Loads every candidate rather than narrowing by suffix, then hands the same
    rows to the same resolver — so the two paths cannot disagree.
    """
    contacts = frappe.get_all(
        "Contact",
        fields=["name", "phone", "mobile_no"],
        or_filters={"phone": ("is", "set"), "mobile_no": ("is", "set")},
    )
    phone_rows = frappe.get_all(
        "Contact Phone", fields=["parent", "phone"], filters={"parenttype": "Contact"}
    )
    return _resolve_contact_match(normalized, contacts, phone_rows)


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


def _publish_fw_message(ticket_name: str, is_incoming: bool, immediate: bool = False) -> None:
    """Publish realtime event for a frappe_whatsapp message linked to a ticket.

    The client reloads the thread on this event, so the row must be committed
    before it fires.

    From a doc event (`immediate=False`) that means after_commit: forcing a
    commit there would put a durability barrier inside Meta's webhook request,
    and frappe v16 disallows commits in doc events anyway.

    From the ingestion job (`immediate=True`) commit and emit directly. Relying
    on after_commit there left the agent staring at a thread that never
    refreshed until they clicked away and back — the emit is registered on
    frappe.db.after_commit, and in a job that fires only once the worker
    finishes, which is not a guarantee worth depending on for something the
    agent watches in real time. A job is not a doc event, so committing here is
    both legal and cheap.
    """
    if immediate:
        # nosemgrep: frappe-manual-commit -- job context, not a doc event; the
        # client refetches on this event and must not race the write.
        frappe.db.commit()
    frappe.publish_realtime(
        "helpdesk:whatsapp-message",
        message={"ticket": str(ticket_name), "is_incoming": is_incoming},
        after_commit=not immediate,
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


# 2× the 240px (max-h-60) the bubble renders at, so it stays sharp on retina.
_THUMB_MAX_PX = 480
_THUMB_QUALITY = 75


def _buffer_to_bytes(value) -> bytes | None:
	"""Decode a JS Buffer as it survives JSON.

	The provider serialises binary as an object of numeric keys
	({"0": 255, "1": 216, ...}); other shapes show up depending on the
	serialiser, so handle the common ones and give up quietly otherwise.
	"""
	try:
		if isinstance(value, str):
			import base64 as _base64

			return _base64.b64decode(value)
		if isinstance(value, list):
			return bytes(value)
		if isinstance(value, dict):
			if isinstance(value.get("data"), list):  # {"type": "Buffer", "data": [...]}
				return bytes(value["data"])
			indexed = {int(k): v for k, v in value.items() if str(k).isdigit()}
			if not indexed:
				return None
			return bytes(indexed[i] for i in sorted(indexed))
	except Exception:
		return None
	return None


def _downscale_image(content: bytes, ext: str) -> bytes | None:
	"""Shrink image bytes to a chat-sized preview. None when not worth storing.

	Pillow is used directly rather than frappe.utils.image.optimize_image because
	that helper msgprints on failure (wrong for a webhook) and clamps the target
	to 80% of the source, which barely shrinks large photos.
	"""
	if ext == "gif":
		return None  # a static JPEG of frame 1 would silently kill the animation
	try:
		import io

		from PIL import Image

		img = Image.open(io.BytesIO(content))
		img.thumbnail((_THUMB_MAX_PX, _THUMB_MAX_PX), Image.Resampling.LANCZOS)
		if img.mode not in ("RGB", "L"):
			img = img.convert("RGB")
		out = io.BytesIO()
		img.save(out, format="JPEG", quality=_THUMB_QUALITY, optimize=True)
		small = out.getvalue()
		# A second file only pays for itself if it's meaningfully smaller.
		return small if len(small) < len(content) * 0.9 else None
	except Exception:
		frappe.logger().warning("WA thumbnail generation failed", exc_info=True)
		return None


def _save_thumbnail_file(content: bytes) -> str:
	"""Store thumbnail bytes as a public File, returning its file_url ("" on failure)."""
	try:
		file_doc = frappe.get_doc({
			"doctype": "File",
			"file_name": f"wa_media_{frappe.generate_hash(length=8)}_thumb.jpg",
			"content": content,
			"is_private": 0,
		})
		file_doc.insert(ignore_permissions=True)
		return file_doc.file_url
	except Exception:
		frappe.logger().warning("WA thumbnail save failed", exc_info=True)
		return ""


def _read_media_content(media_url: str) -> bytes | None:
	"""Read back a stored media File by its file_url."""
	try:
		name = frappe.db.get_value("File", {"file_url": media_url}, "name")
		if not name:
			return None
		return frappe.get_doc("File", name).get_content()
	except Exception:
		return None


def thumbnail_for_media_url(media_url: str) -> str:
	"""Build and store an image preview for an already-saved media File.

	Shared by webhook ingest and the backfill job so both produce identical
	thumbnails. Returns "" when the media isn't an image, can't be read, or
	wouldn't meaningfully shrink — callers fall back to the original.
	"""
	if not media_url:
		return ""
	ext = media_url.rsplit(".", 1)[-1].lower() if "." in media_url else ""
	if ext not in ("jpg", "jpeg", "png", "webp"):
		return ""
	content = _read_media_content(media_url)
	if not content:
		return ""
	small = _downscale_image(content, ext)
	if not small:
		return ""
	return _save_thumbnail_file(small)


def _video_poster_from_raw(raw_msg: dict) -> str:
	"""Save the provider's bundled preview frame as a video poster.

	Only works while the webhook payload is still in memory: the preview is
	stripped before raw_message is stored, so it can't be recovered later.
	"""
	node = raw_msg.get("videoMessage") or {}
	content = _buffer_to_bytes(node.get("jpegThumbnail"))
	if not content:
		return ""
	return _save_thumbnail_file(content)


def _build_wa_thumbnail(raw_msg: dict, media_url: str, content_type: str) -> str:
	"""Preview for a message: downscaled image, or the poster frame for a video."""
	try:
		if content_type == "image":
			return thumbnail_for_media_url(media_url)
		if content_type == "video":
			return _video_poster_from_raw(raw_msg)
	except Exception:
		frappe.logger().warning("WA thumbnail build failed", exc_info=True)
	return ""


def _strip_media_thumbnails(msg: dict) -> dict:
    """Strip large preview-only binary fields (jpegThumbnail, scansSidecar) from a raw message
    dict before storing it, keeping all fields needed for media re-decryption."""
    _THUMB_FIELDS = {"jpegThumbnail", "scansSidecar", "waveform", "streamingSidecar"}
    stripped = {}
    for k, v in msg.items():
        if isinstance(v, dict):
            stripped[k] = {ik: iv for ik, iv in v.items() if ik not in _THUMB_FIELDS}
        else:
            stripped[k] = v
    return stripped


def _download_media_via_wa(line, full_data: dict) -> str:
    """Call WA API /chat/getBase64FromMediaMessage.
    Passes the full message object (key + message content) so Evolution can decrypt the
    CDN-encrypted media using the embedded mediaKey / fileEncSha256 from the message itself."""
    key = full_data.get("key") or {}
    if not key.get("id"):
        return ""
    endpoint = _url("chat/getBase64FromMediaMessage", line.instance_name)
    # Include message content so Evolution has the mediaKey needed for decryption
    payload = {"message": {"key": key, "message": full_data.get("message") or {}}}
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
    # Include stored raw message so Evolution has the mediaKey needed for decryption
    if doc.raw_message:
        try:
            full_data["message"] = frappe.parse_json(doc.raw_message)
        except Exception:
            pass
    new_url = _download_media_via_wa(line, full_data)
    if new_url:
        frappe.db.set_value("WA Message", message_name, "media_url", new_url)
        # Media that only lands now still needs its preview. Video posters can't
        # be recovered at this point (the frame was stripped from raw_message),
        # so those fall back to no poster.
        if doc.content_type == "image":
            thumb = thumbnail_for_media_url(new_url)
            if thumb:
                frappe.db.set_value("WA Message", message_name, "thumbnail_url", thumb)
        frappe.db.commit()
        return new_url
    return ""


def _retry_media_download(message_name: str) -> None:
    """Background job: re-download media for a WA Message whose download failed during webhook.
    Enqueued automatically when Evolution is unreachable at webhook time."""
    try:
        doc = frappe.get_doc("WA Message", message_name)
    except Exception:
        return

    if doc.media_url or not doc.raw_message:
        return  # already downloaded or nothing to work with

    try:
        line = frappe.get_doc("WA Line", doc.line)
    except Exception:
        return

    full_data = {
        "key": {
            "remoteJid": doc.jid,
            "fromMe": doc.direction == "Outgoing",
            "id": doc.message_id,
        },
    }
    try:
        full_data["message"] = frappe.parse_json(doc.raw_message)
    except Exception:
        return

    new_url = _download_media_via_wa(line, full_data)
    if new_url:
        frappe.db.set_value("WA Message", message_name, "media_url", new_url)
        if doc.content_type == "image":
            thumb = thumbnail_for_media_url(new_url)
            if thumb:
                frappe.db.set_value("WA Message", message_name, "thumbnail_url", thumb)
        frappe.db.commit()
        _publish_wa_event(doc.jid, is_incoming=doc.direction == "Incoming", line=doc.line, doc=doc)


def _extract_media_url(
    msg: dict, line=None, full_webhook_data: dict | None = None, allow_remote_fetch: bool = True
) -> str:
    """Download and permanently save media from an incoming WhatsApp message.
    WhatsApp CDN files (.enc) are AES-encrypted — only Evolution (Baileys session) can decrypt.

    `allow_remote_fetch=False` skips the blocking call out to Evolution, keeping
    only the paths that cost nothing (inline base64). The webhook uses this so a
    media message doesn't hold the request open for a network round-trip;
    `_retry_media_download` picks the file up a moment later and republishes.
    """
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
        if allow_remote_fetch and line and full_webhook_data:
            saved = _download_media_via_wa(line, full_webhook_data)
            if saved:
                return saved

        # 3. Last resort: store the raw CDN URL (encrypted, will fail to render
        # in browser). Only worth doing once the decrypt attempt above has
        # actually been made and failed — when it has merely been deferred,
        # returning this would make the message look downloaded and stop
        # _retry_media_download from ever running.
        if allow_remote_fetch:
            cdn_url = media_msg.get("url") or ""
            if cdn_url:
                return cdn_url

        break
    return ""


def _publish_wa_event(jid: str, is_incoming: bool, line: str, ticket: str = "", doc=None) -> None:
    """Broadcast a new/changed WA message to every connected client.

    `doc` (the WA Message that triggered this) is optional but should be passed
    wherever it is at hand: it lets the sidebar badge and the conversation list
    update themselves from the payload alone. Without it every client refetched
    `get_wa_lines` and `get_wa_conversations` on every message in both
    directions — an N-agents x M-messages fan-out of two full-table-scan
    queries, which dominated site compute. Clients fall back to a debounced
    refetch when the preview is absent (or when the JID is new to them and the
    list has no row to patch).
    """
    frappe.db.commit()
    event_data = {"jid": jid, "is_incoming": is_incoming, "line": line}
    if ticket:
        event_data["ticket"] = ticket
    if doc is not None:
        event_data["preview"] = {
            "message": doc.get("message") or "",
            "content_type": doc.get("content_type") or "text",
            "sender_name": doc.get("sender_name") or "",
            "direction": doc.get("direction") or ("Incoming" if is_incoming else "Outgoing"),
            "creation": str(doc.get("creation") or now_datetime()),
        }
    frappe.publish_realtime(
        "helpdesk:baileys-message",
        message=event_data,
        after_commit=True,
    )


def _create_wa_notifications(jid: str, preview: str, sender_name: str, line) -> None:
    """One unread HD Notification per agent per conversation.

    A WA Line conversation is a chat, not a ticket — on a representative site
    only 1 of 340 conversations had a linked ticket and none of 219 contacts had
    a team — so there is no assignee or team to route to the way the WABA path
    does. Every active agent is notified instead, which matches what they can
    already see: the sidebar badge and conversation list are not scoped either.

    Inserting a row per message would be indefensible at this volume. The dedupe
    below means an agent gets one notification per conversation and nothing
    further until they read it, so steady-state cost is two SELECTs per incoming
    message and no writes. Creating the row is also what sends the web push —
    see HD Notification._send_push_notification.
    """
    agents = frappe.get_all(
        "HD Agent", filters={"is_active": 1}, pluck="user", ignore_permissions=True
    )
    if not agents:
        return

    already_notified = set(
        frappe.get_all(
            "HD Notification",
            filters={
                "reference_wa_jid": jid,
                "notification_type": "WhatsApp",
                "read": 0,
            },
            pluck="user_to",
            ignore_permissions=True,
        )
    )

    body = f"{sender_name}: {preview}" if sender_name else preview
    for agent in agents:
        if agent in already_notified:
            continue
        try:
            frappe.get_doc({
                "doctype": "HD Notification",
                "user_from": "Administrator",
                "user_to": agent,
                "notification_type": "WhatsApp",
                "reference_wa_jid": jid,
                "reference_wa_line": line.name,
                "message": body,
            }).insert(ignore_permissions=True)
        except Exception:
            # One agent's notification failing must not cost the others theirs,
            # nor fail the webhook that is only incidentally creating them.
            frappe.logger().warning(
                f"WA notification insert failed for {agent} on {jid}", exc_info=True
            )


def _clear_wa_notifications(jid: str, user: str) -> None:
    """Mark this agent's unread notifications for a conversation as read.

    Not cosmetic: _create_wa_notifications skips an agent who already has an
    unread notification for the JID, so without this the agent would be
    notified once about a conversation and then never again. Opening the chat
    is what releases the dedupe for the next message.
    """
    names = frappe.get_all(
        "HD Notification",
        filters={
            "reference_wa_jid": jid,
            "user_to": user,
            "notification_type": "WhatsApp",
            "read": 0,
        },
        pluck="name",
        ignore_permissions=True,
    )
    for name in names:
        frappe.db.set_value("HD Notification", name, "read", 1, update_modified=False)


def _notify_agents(jid: str, message_text: str, sender_name: str, line, settings) -> None:
    # Suppress everything — bell *and* push — while a conversation is visibly
    # being attended, i.e. some agent replied to it within the quiet period.
    # This guard runs before the notification is created rather than only before
    # the realtime publish, so an active chat doesn't buzz anyone's phone on
    # every customer reply. An unattended conversation still gets through.
    #
    # As with unread_window_days: a Single that predates the field keeps no
    # value for it, and Frappe only applies a field default to new documents.
    # Treating unset as 0 would silently mean "always notify" — the exact
    # notification flood this is here to prevent — so unset means the default.
    quiet = _shared_settings().get("notification_quiet_minutes")
    quiet_minutes = DEFAULT_NOTIFICATION_QUIET_MINUTES if quiet is None else cint(quiet)
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

    preview = (message_text or "")[:80] or "sent a WhatsApp message"
    _create_wa_notifications(jid, preview, sender_name, line)

    # Event name must match the listener in desk/src/stores/notification.ts.
    # It used to be "helpdesk:new-baileys-message", which nothing listened for,
    # so an incoming WA Line message never reloaded the bell or played the alert
    # sound. Groups are deliberately treated the same as 1:1 chats here — the
    # dedupe above caps a busy group at one unread notification per agent.
    frappe.publish_realtime(
        "helpdesk:baileys-notification",
        message={"jid": jid, "message": preview,
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
    if event == "connection.update":
        return _handle_connection_update(payload.get("data") or {}, line)

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

	# Cross-line echo detection: when multiple WA Lines are all members of the same
	# group, a message sent by one line arrives at sibling lines with fromMe=False
	# because Evolution marks fromMe relative to each instance. Without this check,
	# sibling lines treat the outgoing message as incoming, the bot fires, and an
	# unwanted automated reply is sent. Detect by comparing sender against the
	# connected_user JID of every configured line.
	if not from_me and is_group and sender and sender != jid:
		try:
			own_jids = set(frappe.get_all(
				"WA Line", filters={"connected_user": ["!=", ""]}, pluck="connected_user"
			))
			if sender in own_jids:
				from_me = True
		except Exception:
			pass

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
	reply_to_message_id = _extract_reply_target(data, raw_msg, content_type)

	# Extract media URL for media messages. The decrypt-and-download round trip
	# to Evolution is deliberately NOT made here: it is the single slowest thing
	# the webhook did, and Evolution blocks on our response. Only the free inline
	# base64 path runs now; anything else leaves media_url empty, which is
	# exactly the condition that enqueues _retry_media_download below. That job
	# fetches the file and republishes, so the bubble fills in a moment later.
	media_url = ""
	thumbnail_url = ""
	raw_message_json = ""
	if content_type in ("image", "video", "audio", "document", "sticker"):
		# Store stripped payload so Evolution can re-decrypt later if the download fails now
		try:
			raw_message_json = frappe.as_json(_strip_media_thumbnails(raw_msg))
		except Exception:
			pass
		try:
			media_url = _extract_media_url(
				raw_msg, line=line, full_webhook_data=data, allow_remote_fetch=False
			)
		except Exception as exc:
			frappe.logger().warning(f"_extract_media_url failed for {message_id}: {exc}")
			media_url = ""
		# Must happen here, not in a background job: the video poster comes from
		# raw_msg, which is about to be stripped of its preview frames. (An image
		# thumbnail needs the downloaded file, so when the fetch is deferred
		# _retry_media_download builds that one instead.)
		thumbnail_url = _build_wa_thumbnail(raw_msg, media_url, content_type)

	# Detect and handle incoming edit before dedup check
	new_text, is_edit, original_id = _extract_edit(raw_msg)
	if is_edit:
		# Use the inner protocolMessage key id (the original message) rather than the
		# outer data key id which may be a new wrapper id assigned to the edit event.
		target_id = original_id or message_id
		existing = frappe.db.get_value("WA Message", {"message_id": target_id}, "name") if target_id else None
		if existing:
			frappe.set_user("Administrator")
			_apply_edit(existing, new_text, edited_by="incoming", jid=jid, line=line)
			return {"status": "ok", "edited": True}
		frappe.logger().warning(f"Incoming WA edit: original not found target_id={target_id} wrapper_id={message_id}")
		return {"status": "ok", "reason": "edit_original_not_found"}

	# WhatsApp's newer edit protocol sends a Signal-encrypted node (secretEncryptedMessage)
	# instead of a plain protocolMessage. Neither Evolution API nor the Baileys library it
	# wraps decrypts this — it has no handling for it at all (confirmed: the key only
	# appears in Baileys' protobuf schema, never in its message-processing code; Evolution's
	# own message store also keeps the pre-edit text). The real new text is unrecoverable
	# here, so just flag the original instead of falling through to a stray empty insert.
	secret_edit = raw_msg.get("secretEncryptedMessage") or {}
	if secret_edit:
		target_id = (secret_edit.get("targetMessageKey") or {}).get("id") or ""
		existing = frappe.db.get_value("WA Message", {"message_id": target_id}, "name") if target_id else None
		if existing:
			frappe.set_user("Administrator")
			_flag_unrecoverable_edit(existing, jid=jid, line=line)
			return {"status": "ok", "edited": "unrecoverable"}
		frappe.logger().warning(f"Incoming WA edit (undecryptable): original not found target_id={target_id}")
		return {"status": "ok", "reason": "edit_original_not_found"}

	# Deduplicate via Redis atomic SET NX — prevents the race where two concurrent
	# webhook deliveries for the SAME line both pass a DB-level exists() check before either inserts.
	# Key is scoped per-line so that a group message legitimately received by multiple WA Lines
	# (all members of the same group) is stored once per line, not deduplicated globally.
	if message_id:
		dedup_key = f"wa_dedup:{line.name}:{message_id}"
		acquired = frappe.cache().set(dedup_key, 1, ex=300, nx=True)
		if not acquired:
			return {"status": "duplicate"}
		# Belt-and-suspenders: also check DB in case Redis lost the key (restart/flush)
		if frappe.db.exists("WA Message", {"message_id": message_id, "line": line.name}):
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
				"thumbnail_url": thumbnail_url,
				"message_id": stored_msg_id,
				"reply_to_message_id": reply_to_message_id,
				"raw_message": raw_message_json,
				"status": "Delivered",
				"reference_doctype": "",
				"reference_name": "",
				"line": line.name,
			}).insert(ignore_permissions=True)
		except frappe.exceptions.DuplicateEntryError:
			return {"status": "duplicate"}
		frappe.db.set_value("WA Message", doc.name, "owner", owner, update_modified=False)
		_publish_wa_event(jid, is_incoming=False, line=line.name, doc=doc)
		if not media_url and raw_message_json:
			frappe.enqueue(
				"helpdesk.integrations.wa._retry_media_download",
				message_name=doc.name,
				queue="short",
			)
		return {"status": "ok", "mirrored": True}

	# Incoming message — link to existing HD Ticket if one owns this JID
	try:
		_ticket_ref = frappe.db.get_value("HD Ticket", {"baileys_jid": jid}, "name") or ""
	except Exception:
		_ticket_ref = ""

	try:
		incoming_doc = frappe.get_doc({
			"doctype": "WA Message",
			"direction": "Incoming",
			"jid": jid,
			"sender_jid": sender,
			"sender_name": sender_name,
			"profile_name": sender_name,
			"message": text,
			"content_type": content_type or "text",
			"media_url": media_url,
			"thumbnail_url": thumbnail_url,
			"message_id": stored_msg_id,
			"reply_to_message_id": reply_to_message_id,
			"raw_message": raw_message_json,
			"status": "Pending",
			"reference_doctype": "HD Ticket" if _ticket_ref else "",
			"reference_name": _ticket_ref,
			"line": line.name,
		}).insert(ignore_permissions=True)
	except frappe.exceptions.DuplicateEntryError:
		return {"status": "duplicate"}

	_upsert_contact_name(jid, sender_name)
	_publish_wa_event(jid, is_incoming=True, line=line.name, doc=incoming_doc)
	if not media_url and raw_message_json:
		frappe.enqueue(
			"helpdesk.integrations.wa._retry_media_download",
			message_name=incoming_doc.name,
			queue="short",
		)
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
    # Status updates are the highest-frequency webhook event by far — every
    # outgoing message produces sent/delivered/read in turn. The per-item
    # get_value + commit this used to do meant a full table scan (message_id was
    # unindexed) and a transaction round-trip per status. Resolve the whole
    # batch in one query and commit once.
    pending = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        key = item.get("key") or {}
        message_id = key.get("id") or ""
        update_body = item.get("update") or {}
        raw_status = update_body.get("status")
        status = _STATUS_MAP.get(raw_status) if raw_status is not None else None
        if not message_id or not status:
            continue
        pending.append((message_id, status, key.get("remoteJid", "")))

    if not pending:
        return {"status": "ok"}

    # A message_id is not unique (forwarded messages legitimately repeat one,
    # which is why the unique index was dropped) — keep the first match, as the
    # per-item get_value did.
    name_by_id: dict[str, str] = {}
    for row in frappe.db.sql(
        "SELECT message_id, name FROM `tabWA Message` WHERE message_id IN %(ids)s",
        {"ids": tuple({p[0] for p in pending})},
        as_dict=True,
    ):
        name_by_id.setdefault(row.message_id, row.name)

    events = []
    for message_id, status, remote_jid in pending:
        msg_name = name_by_id.get(message_id)
        if not msg_name:
            continue
        frappe.db.set_value("WA Message", msg_name, "status", status, update_modified=False)
        events.append({"message_id": message_id, "status": status,
                       "jid": remote_jid, "line": line.name})

    if not events:
        return {"status": "ok"}

    frappe.db.commit()
    # Already durable after the commit above, so these publish immediately
    # rather than waiting to be flushed by the next one.
    for event in events:
        frappe.publish_realtime("helpdesk:baileys-status-update", message=event)
    return {"status": "ok"}


def _handle_connection_update(data: dict, line) -> dict:
    """Persist the connected WhatsApp JID when Evolution reports a live connection."""
    # Evolution API v2 sends either data.instance.me.id or data.me.id for the
    # instance's own WhatsApp JID when state becomes "open".
    state = (data.get("instance") or {}).get("state") or data.get("state") or ""
    me = (data.get("instance") or {}).get("me") or data.get("me") or {}
    owner_jid = me.get("id") or ""
    if state == "open" and owner_jid:
        try:
            frappe.db.set_value("WA Line", line.name, "connected_user", owner_jid)
        except Exception as e:
            frappe.log_error(f"Failed to save connected_user for {line.name}: {e}", "WA Connection Update")
    return {"status": "ok", "state": state}


def _handle_delete(data: dict, line) -> dict:
    """Mark locally-stored messages as deleted when Evolution API fires messages.delete."""
    ids = data.get("ids") or []
    if isinstance(ids, str):
        ids = [ids]
    deleted = 0
    for message_id in ids:
        if not message_id:
            continue
        msg = frappe.db.get_value(
            "WA Message", {"message_id": message_id}, ["name", "jid"], as_dict=True
        )
        if not msg:
            continue
        try:
            # `status` tracks delivery, not existence — writing "Failed" here would
            # both corrupt delivery analytics and offer the agent a Retry button
            # for a message the sender deliberately removed.
            frappe.db.set_value("WA Message", msg.name, "is_deleted", 1, update_modified=False)
            frappe.db.commit()
            deleted += 1
            _publish_wa_delete(msg.jid, message_id, line.name)
        except Exception as e:
            frappe.log_error(f"Failed to mark WA Message {msg.name} as deleted: {e}", "WA Message Delete")
    return {"status": "ok", "deleted": deleted}


# ── Agent send ────────────────────────────────────────────────────────────────

_MIME_MAP = {"image": "image/jpeg", "video": "video/mp4", "audio": "audio/ogg", "document": "application/octet-stream"}


def _evo_send_message(
    line,
    jid: str,
    full_message: str,
    content_type: str = "text",
    media_url: str | None = None,
    mime_type: str | None = None,
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
            # Evolution API requires participant for non-from-me group replies.
            # If we don't have the sender_jid, skip quoted to avoid a 400 crash.
            if _is_group(jid) and not is_from_me and not participant:
                quoted_key = None
            else:
                quoted_key = {"remoteJid": jid, "fromMe": is_from_me, "id": reply_to_message_id}
                if participant:
                    quoted_key["participant"] = participant

    if isinstance(mentioned_jids, str):
        mentioned_jids = frappe.parse_json(mentioned_jids) if mentioned_jids.strip() else None

    if media_url and content_type in ("image", "video", "audio", "document"):
        if media_url.startswith("http"):
            abs_url = media_url
        else:
            abs_url = frappe.utils.get_url(_urlquote(media_url, safe="/:"))
        effective_mime = mime_type or _MIME_MAP.get(content_type, "application/octet-stream")
        file_name = abs_url.rsplit("/", 1)[-1].split("?")[0] or f"file.{effective_mime.split('/')[-1]}"
        payload: dict = {
            "number": jid,
            "mediatype": content_type,
            "mimetype": effective_mime,
            "fileName": file_name,
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
    # Evolution can crash with a JS TypeError when the quoted key is malformed
    # (e.g. wrong fromMe direction). Retry without quoted so the message still sends.
    if not resp.ok and resp.status_code == 400 and "quoted" in payload:
        try:
            err_body = resp.json()
            messages = err_body.get("response", {}).get("message", [])
            if any("TypeError" in str(m) for m in messages):
                payload.pop("quoted", None)
                resp = _evo_session.post(
                    _url(endpoint, line.instance_name),
                    json=payload,
                    headers=_headers(line),
                    timeout=timeout,
                )
        except Exception:
            pass
    if not resp.ok:
        frappe.log_error(
            f"WA send {resp.status_code} for {jid}: {resp.text[:500]}", "WA Send"
        )
    resp.raise_for_status()
    data = resp.json()
    return data.get("key", {}).get("id") or data.get("messageId", "")


@frappe.whitelist()
def send_wa_reply(
    ticket: str = None,
    jid: str = None,
    line: str = None,
    message: str = "",
    content_type: str = "text",
    media_url: str | None = None,
    mime_type: str | None = None,
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
            return _send_fw_reply(ticket=ticket, message=message, content_type=content_type, media_url=media_url, reply_to_message_id=reply_to_message_id)

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
            mime_type=mime_type,
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
    })
    msg_doc.insert(ignore_permissions=True)

    if ticket:
        assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
        if frappe.session.user not in (frappe.parse_json(assign_json) or []):
            try:
                frappe.get_doc("HD Ticket", ticket).assign_agent(frappe.session.user)
            except Exception:
                pass
        # Only advance the ticket to the "agent replied" status when the reply actually went out.
        if status == "Sent":
            shared = _shared_settings()
            if shared.agent_reply_status:
                _set_ticket_status(ticket, shared.agent_reply_status)

    _publish_wa_event(jid, is_incoming=False, line=line.name, ticket=ticket or "", doc=msg_doc)
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
    # An empty emoji is a clear, not a missing argument: WhatsApp removes a
    # reaction when it receives one with an empty body.
    if not jid or not target_message_id:
        frappe.throw(_("jid and target_message_id are required."))

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
    reaction_doc = frappe.get_doc({
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
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    _publish_wa_event(jid, is_incoming=False, line=line.name, doc=reaction_doc)
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
        resp = _evo_session.post(
            _url("chat/updateMessage", line.instance_name),
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
        if not resp.ok:
            frappe.log_error(f"WA edit {resp.status_code}: {resp.text[:500]}", "WA Edit Message")
        resp.raise_for_status()
    except Exception as e:
        frappe.log_error(f"WA edit failed for message {message_name}: {e}", "WA Edit Message")
        frappe.throw(_("WA API edit failed: {0}").format(str(e)))

    agent_name = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
    _apply_edit(doc.name, new_text, edited_by=agent_name, jid=doc.jid, line=line)

    return {"status": "ok", "name": doc.name}


@frappe.whitelist()
def delete_wa_message(message_name: str) -> dict:
    """Delete an outgoing message for everyone via WA API and mark the local record."""
    settings = _settings()
    if not settings.enabled:
        frappe.throw(_("WA API is not enabled."))

    doc = frappe.get_doc("WA Message", message_name)

    # WhatsApp only lets you delete-for-everyone what you sent.
    if doc.direction != "Outgoing":
        frappe.throw(_("Only outgoing messages can be deleted."))
    if doc.owner != frappe.session.user and "System Manager" not in frappe.get_roles():
        frappe.throw(_("You can only delete your own messages."))
    if doc.is_deleted:
        return {"status": "ok", "name": doc.name, "already_deleted": True}
    if not doc.message_id:
        frappe.throw(_("This message has no WhatsApp ID yet."))

    line = frappe.get_doc("WA Line", doc.line)

    try:
        resp = _evo_session.delete(
            _url("chat/deleteMessageForEveryone", line.instance_name),
            json={
                "id": doc.message_id,
                "fromMe": True,
                "remoteJid": doc.jid,
            },
            headers=_headers(line),
            timeout=15,
        )
        if not resp.ok:
            frappe.log_error(f"WA delete {resp.status_code}: {resp.text[:500]}", "WA Delete Message")
        resp.raise_for_status()
    except Exception as e:
        frappe.log_error(f"WA delete failed for message {message_name}: {e}", "WA Delete Message")
        frappe.throw(_("WA API delete failed: {0}").format(str(e)))

    frappe.db.set_value("WA Message", doc.name, "is_deleted", 1, update_modified=False)
    frappe.db.commit()
    _publish_wa_delete(doc.jid, doc.message_id, line.name)

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
        mime_type=mime_type,
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
    if doc.is_deleted:
        frappe.throw(_("Deleted messages cannot be retried."))
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
    _publish_wa_event(doc.jid, is_incoming=False, line=line.name, ticket=doc.reference_name or "", doc=doc)
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

    # Per-line unread counts for the current agent in a single grouped query,
    # rather than one query per line. Scoped to the same per-agent cursor
    # (WA Conversation Read State) the conversation list uses, and to the same
    # unread window floor (see _unread_floor).
    unread_by_line: dict[str, int] = {}
    if lines and frappe.db.exists("DocType", "WA Message"):
        floor = _unread_floor()
        floor_filter = "AND m.creation > %(floor)s" if floor else ""
        for row in frappe.db.sql(
            f"""
            SELECT m.line AS line, COUNT(*) AS cnt
            FROM `tabWA Message` m
            LEFT JOIN `tabWA Conversation Read State` r
              ON r.jid = m.jid AND r.user = %(user)s
            WHERE m.direction = 'Incoming'
              {floor_filter}
              AND (r.last_read IS NULL OR m.creation > r.last_read)
            GROUP BY m.line
            """,
            {"user": frappe.session.user, "floor": floor},
            as_dict=True,
        ):
            if row.line:
                unread_by_line[row.line] = row.cnt

    for line in lines:
        line["display_label"] = line["label"] or line["instance_name"]
        line["unread"] = unread_by_line.get(line["name"], 0)
    return lines


def _mark_conversation_read_for_user(jid: str, user: str | None = None, upto=None) -> None:
    """Upsert one agent's read cursor for a JID to `upto` (default: now).

    `(user, jid)` is enforced unique at the SQL level (see
    add_wa_conversation_read_state_unique_index patch). The get-then-insert
    below is not atomic, so two concurrent callers for the same agent+JID
    (double-click, multiple tabs) can both miss the existing row and both
    attempt an insert. The loser of that race gets a UniqueValidationError
    from Frappe (wrapping the underlying MySQL 1062 duplicate-key error).

    That insert is wrapped in a savepoint (not a full frappe.db.rollback())
    so a losing race only unwinds its own failed insert, not any other
    uncommitted work earlier in the same request/transaction — e.g. earlier
    iterations of mark_all_wa_messages_read's per-JID loop. On a loss we fall
    back to updating the row the winner created, so the race is invisible to
    the caller.
    """
    user = user or frappe.session.user
    upto = upto or now_datetime()
    existing = frappe.db.get_value("WA Conversation Read State", {"user": user, "jid": jid}, "name")
    if existing:
        frappe.db.set_value("WA Conversation Read State", existing, "last_read", upto, update_modified=False)
        return

    doc = frappe.get_doc({
        "doctype": "WA Conversation Read State",
        "user": user,
        "jid": jid,
        "last_read": upto,
    })
    message_log_len = len(frappe.message_log)
    with savepoint(catch=frappe.exceptions.UniqueValidationError):
        doc.insert(ignore_permissions=True)
        return

    # Lost the race: another request inserted the row first. The failed
    # insert queued a "must be unique" msgprint before raising — drop it so
    # a losing race doesn't surface a confusing toast to the agent, since
    # the fallback below makes the race invisible to the caller otherwise.
    del frappe.message_log[message_log_len:]

    # Fall back to updating the row the winner created.
    existing = frappe.db.get_value("WA Conversation Read State", {"user": user, "jid": jid}, "name")
    if existing:
        frappe.db.set_value("WA Conversation Read State", existing, "last_read", upto, update_modified=False)
    else:
        # Shouldn't happen (row vanished between the failed insert and this
        # re-read) — retry once rather than silently dropping the write.
        frappe.get_doc({
            "doctype": "WA Conversation Read State",
            "user": user,
            "jid": jid,
            "last_read": upto,
        }).insert(ignore_permissions=True)


def _mark_conversation_read_for_all_agents(jid: str, upto) -> None:
    """Advance every agent's cursor for a JID — used for historical/backfilled imports
    that shouldn't appear as unread for anyone."""
    for agent_user in frappe.get_all("HD Agent", pluck="user"):
        _mark_conversation_read_for_user(jid, user=agent_user, upto=upto)


# Keep in sync with the matching defaults in WhatsApp Helpdesk Settings; used
# when the singleton has no stored value for them.
DEFAULT_UNREAD_WINDOW_DAYS = 30
DEFAULT_NOTIFICATION_QUIET_MINUTES = 10


def _unread_floor():
    """Oldest creation time an incoming message can have and still count as unread.

    Without a floor, an agent who has no `WA Conversation Read State` row for a
    JID has `r.last_read IS NULL`, which makes the *entire* message history
    unread. That is both a nonsense badge (agents were seeing counts in the
    thousands on first login) and the reason the unread queries had to scan
    every row ever received — cost per call grew with total history even at
    flat traffic, and these two endpoints were the largest consumers of site
    compute by a wide margin.

    The floor turns that into a bounded range scan: it is a plain
    `creation > :floor` predicate on `m` alone, so unlike the cursor comparison
    (which depends on the joined read-state row) the planner can push it into
    the `wa_message_direction_creation_jid` index.

    Trade-off, by design: incoming messages older than the window are treated
    as read for everyone, whether or not the agent ever opened them. Returns
    None when the window is 0, which restores the old unbounded behaviour.
    """
    # .get() rather than attribute access: on a site that hasn't migrated since
    # this field was added the singleton has no such attribute at all, and a
    # Single that predates the field keeps no value for it either — Frappe only
    # applies a field default to new documents, never to an existing singleton.
    # Unset therefore has to mean the default, not 0; an admin who genuinely
    # wants no window stores an explicit 0.
    days = _shared_settings().get("unread_window_days")
    days = DEFAULT_UNREAD_WINDOW_DAYS if days is None else cint(days)
    if days <= 0:
        return None
    return add_to_date(now_datetime(), days=-days)


def _unread_counts_for_user(jids: list[str], user: str) -> dict[str, int]:
    """{jid: unread incoming message count} for one agent, scoped to `jids`."""
    if not jids:
        return {}
    floor = _unread_floor()
    floor_filter = "AND m.creation > %(floor)s" if floor else ""
    rows = frappe.db.sql(
        f"""
        SELECT m.jid, COUNT(*) AS cnt
        FROM `tabWA Message` m
        LEFT JOIN `tabWA Conversation Read State` r
          ON r.jid = m.jid AND r.user = %(user)s
        WHERE m.direction = 'Incoming' AND m.jid IN %(jids)s
          {floor_filter}
          AND (r.last_read IS NULL OR m.creation > r.last_read)
        GROUP BY m.jid
        """,
        {"jids": tuple(jids), "user": user, "floor": floor},
        as_dict=True,
    )
    return {row.jid: row.cnt for row in rows}


CONVERSATIONS_PAGE_SIZE = 50


def _team_blocked_jids() -> list[str]:
    """JIDs the current agent may not see under restrict_chats_by_team.

    This used to be a post-filter in Python, which a LIMIT makes untenable —
    dropping rows after the page is cut yields short and inconsistently sized
    pages. Expressed as an exclusion list instead: WA Contact holds one row per
    conversation (hundreds, not millions), so resolving it up front is cheap and
    keeps the paged query itself simple. Chats with no team stay visible to
    everyone, and an agent with no team of their own sees everything — both
    match the previous behaviour.
    """
    if not _shared_settings().restrict_chats_by_team:
        return []
    user_teams = set(
        frappe.get_all("HD Team Member", filters={"user": frappe.session.user}, pluck="parent")
    )
    if not user_teams:
        return []
    return frappe.db.sql_list(
        """
        SELECT jid FROM `tabWA Contact`
        WHERE IFNULL(assigned_team, '') != '' AND assigned_team NOT IN %(teams)s
        """,
        {"teams": tuple(user_teams)},
    )


def _search_matched_jids(search: str, line: str) -> set[str]:
    """JIDs whose contact details, or any message, match a search term.

    Matching messages (rather than only the latest one, which is all the
    client-side filter this replaces could see) means searching actually reaches
    back through a conversation. The `message LIKE` scan it costs is
    unindexable, but this only runs for a term an agent typed, debounced — not
    on the automatic refresh path that made these endpoints expensive.
    """
    like = f"%{search}%"
    matched = set(
        frappe.db.sql_list(
            """
            SELECT jid FROM `tabWA Contact`
            WHERE custom_name LIKE %(q)s OR company LIKE %(q)s OR phone LIKE %(q)s
            """,
            {"q": like},
        )
    )
    message_scope = "AND line = %(line)s" if line else ""
    matched.update(
        frappe.db.sql_list(
            f"""
            SELECT DISTINCT jid FROM `tabWA Message`
            WHERE message LIKE %(q)s {message_scope}
            """,
            {"q": like, "line": line},
        )
    )
    if line:
        try:
            line_doc = frappe.get_doc("WA Line", line)
            matched.update(
                row.jid
                for row in (line_doc.group_jids or [])
                if search.lower() in (row.group_name or "").lower()
            )
        except frappe.DoesNotExistError:
            pass
    return {j for j in matched if j}


def _unread_jids_for_user(line: str, user: str) -> list[str]:
    """JIDs with at least one unread incoming message for this agent."""
    floor = _unread_floor()
    clauses = ["m.direction = 'Incoming'"]
    if line:
        clauses.append("m.line = %(line)s")
    if floor:
        clauses.append("m.creation > %(floor)s")
    return frappe.db.sql_list(
        f"""
        SELECT DISTINCT m.jid
        FROM `tabWA Message` m
        LEFT JOIN `tabWA Conversation Read State` r
          ON r.jid = m.jid AND r.user = %(user)s
        WHERE {' AND '.join(clauses)}
          AND (r.last_read IS NULL OR m.creation > r.last_read)
        """,
        {"line": line, "user": user, "floor": floor},
    )


@frappe.whitelist()
def get_wa_conversations(
    line: str = "",
    search: str = "",
    conv_filter: str = "all",
    favourite_jids=None,
    limit: int = CONVERSATIONS_PAGE_SIZE,
    offset: int = 0,
) -> dict:
    """One entry per unique JID for a line, most-recent first, one page at a time.

    Returns `{"conversations": [...], "has_more": bool}`.

    This used to return every conversation on the line with no LIMIT, and the
    client filtered and searched the result in the browser. Searching, filtering
    and the team restriction all had to move here for a page to mean anything:
    a filter applied after the cut would silently shrink pages.
    """
    limit = max(1, min(cint(limit) or CONVERSATIONS_PAGE_SIZE, 200))
    offset = max(0, cint(offset))
    search = (search or "").strip()
    conv_filter = conv_filter or "all"
    if isinstance(favourite_jids, str):
        favourite_jids = frappe.parse_json(favourite_jids or "[]")
    favourite_jids = [j for j in (favourite_jids or []) if j]

    empty: dict = {"conversations": [], "has_more": False}

    # Predicates on the grouped-by-JID subquery, so they apply before the page
    # is cut. Raw SQL throughout — frappe.qb generates a derived table without
    # an alias for this shape, which MySQL rejects with OperationalError 1248.
    sub_clauses = ["jid NOT LIKE '%%@broadcast'"]
    params: dict = {"line": line, "limit": limit + 1, "offset": offset}
    if line:
        sub_clauses.append("line = %(line)s")

    if conv_filter == "groups":
        sub_clauses.append("jid LIKE '%%@g.us'")
    elif conv_filter == "favourites":
        # Favourites live in the agent's own localStorage, so the client has to
        # send them; there is nothing server-side to filter on otherwise.
        if not favourite_jids:
            return empty
        sub_clauses.append("jid IN %(favourites)s")
        params["favourites"] = tuple(favourite_jids)
    elif conv_filter == "unread":
        unread_jids = _unread_jids_for_user(line, frappe.session.user)
        if not unread_jids:
            return empty
        sub_clauses.append("jid IN %(unread_jids)s")
        params["unread_jids"] = tuple(unread_jids)

    if search:
        matched = _search_matched_jids(search, line)
        # The JID itself is worth matching directly so a phone number typed in
        # full finds its chat even with no contact record.
        if matched:
            sub_clauses.append("(jid IN %(matched)s OR jid LIKE %(search_like)s)")
            params["matched"] = tuple(matched)
        else:
            sub_clauses.append("jid LIKE %(search_like)s")
        params["search_like"] = f"%{search}%"

    blocked = _team_blocked_jids()
    if blocked:
        sub_clauses.append("jid NOT IN %(blocked)s")
        params["blocked"] = tuple(blocked)

    line_filter = "AND bm.line = %(line)s" if line else ""
    # limit + 1 rows in the subquery is how has_more is decided without a second
    # COUNT over the same grouping.
    rows = frappe.db.sql(
        f"""
        SELECT bm.jid, bm.sender_name, bm.message, bm.content_type, bm.direction, bm.creation
        FROM `tabWA Message` bm
        INNER JOIN (
            SELECT jid, MAX(creation) AS latest_creation
            FROM `tabWA Message`
            WHERE {' AND '.join(sub_clauses)}
            GROUP BY jid
            ORDER BY latest_creation DESC
            LIMIT %(limit)s OFFSET %(offset)s
        ) latest ON bm.jid = latest.jid AND bm.creation = latest.latest_creation
        WHERE bm.jid NOT LIKE '%%@broadcast'
        {line_filter}
        ORDER BY bm.creation DESC
        """,
        params,
        as_dict=True,
    )

    # Two messages for one JID can share MAX(creation) to the microsecond, so
    # the join can return more than one row per JID.
    seen: set[str] = set()
    deduped = []
    for r in rows:
        if r.jid and r.jid not in seen:
            seen.add(r.jid)
            deduped.append(r)

    has_more = len(deduped) > limit
    deduped = deduped[:limit]

    line_doc = frappe.get_doc("WA Line", line) if line else None
    group_names = {}
    if line_doc:
        group_names = {row.jid: (row.group_name or row.jid) for row in (line_doc.group_jids or [])}

    jids = [r.jid for r in deduped]
    contacts: dict[str, dict] = {}
    if jids:
        for c in frappe.get_all(
            "WA Contact",
            filters={"jid": ["in", jids]},
            fields=["jid", "custom_name", "company", "assigned_team", "phone", "canonical_jid"],
        ):
            contacts[c.jid] = c

    # Non-Done task counts per JID — single query, silently skipped if custom field absent.
    task_counts: dict[str, int] = {}
    if jids:
        try:
            for row in frappe.db.sql(
                """
                SELECT baileys_jid, COUNT(*) AS cnt
                FROM `tabHD Task`
                WHERE status != 'Done' AND baileys_jid IN %(jids)s
                GROUP BY baileys_jid
                """,
                {"jids": tuple(jids)},
                as_dict=True,
            ):
                task_counts[row.baileys_jid] = row.cnt
        except Exception:
            pass

    # Unread incoming message counts per JID, scoped to the requesting agent
    # via WA Conversation Read State (per-agent cursor, not a shared flag).
    unread_counts = _unread_counts_for_user(jids, frappe.session.user)

    result = []
    for r in deduped:
        jid = r.jid
        is_grp = jid.endswith("@g.us")
        contact = contacts.get(jid, {})
        assigned_team = contact.get("assigned_team") or ""
        # The team restriction is applied in SQL now (see _team_blocked_jids) —
        # filtering here would cut rows out of an already-sized page.
        canonical = contact.get("canonical_jid") or ""
        phone_fallback = (
            contact.get("phone")
            or (_phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else "")
            or (_phone_from_jid(canonical) if canonical.endswith("@s.whatsapp.net") else "")
        ) if not is_grp else ""

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
                or (f"+{phone_fallback}" if phone_fallback else "")
                or jid.split("@")[0]
            )
        result.append({
            "jid": jid,
            "display_name": display_name or jid,
            "company": contact.get("company") or "",
            "assigned_team": assigned_team,
            "phone": phone_fallback,
            "is_group": is_grp,
            "last_message": r.get("message") or f"[{r.get('content_type', 'media')}]",
            "last_sender_name": r.get("sender_name") or "" if is_grp else "",
            "last_message_time": str(r["creation"]),
            "last_direction": r.get("direction", "Incoming"),
            "content_type": r.get("content_type", "text"),
            "open_task_count": task_counts.get(jid, 0),
            "unread_count": unread_counts.get(jid, 0),
        })

    return {"conversations": result, "has_more": has_more}


# Meta only accepts a read receipt inside the 24-hour customer-service window.
_WA_RECEIPT_MAX_AGE_HOURS = 24
# A receipt that keeps failing inside the window is almost always permanently
# unsendable. Retrying it forever left the message short of "marked as read",
# which re-logged the failure on every ticket open and stranded it in the tab's
# unread badge (get_ticket_wa_unread_count filters on that same field).
_WA_RECEIPT_MAX_ATTEMPTS = 3
_WA_RECEIPT_BACKOFF_TTL = 900
_WA_RECEIPT_ATTEMPTS_TTL = 86400


def _wa_account_for_message(account_name: str | None):
    """Resolve a message's WhatsApp Account, falling back to the default incoming one.

    Returns None rather than raising: a message pointing at a since-deleted
    account must not 500 the whole mark-as-read call for the other messages.
    """
    if not account_name:
        account_name = frappe.db.get_value("WhatsApp Account", {"is_default_incoming": 1}, "name")
    if not account_name:
        return None
    try:
        return frappe.get_cached_doc("WhatsApp Account", account_name)
    except frappe.DoesNotExistError:
        return None


def _post_wa_read_receipt(message_id: str, account) -> tuple[bool, str]:
    r"""POST a read receipt to Meta. Returns (ok, error) with the real failure detail.

    We own this call rather than delegating to frappe_whatsapp's
    WhatsAppMessage.send_read_receipt(): that method swallows the exception and
    logs an error it reconstructs from stale request flags, which in practice
    reads "None\n{}" and tells us nothing. It also lives in a vendored app whose
    only remote is upstream, so patches to it cannot be deployed from here.
    """
    try:
        resp = _requests.post(
            f"{account.url}/{account.version}/{account.phone_id}/messages",
            headers={
                "authorization": f"Bearer {account.get_password('token')}",
                "content-type": "application/json",
            },
            json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id},
            timeout=10,
        )
        resp.raise_for_status()
        return True, ""
    except _requests.exceptions.HTTPError as e:
        r = e.response
        if r is None:
            return False, f"HTTPError with no response: {e}"
        return False, f"HTTP {r.status_code}: {(r.text or '').strip()[:500]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _settle_wa_read_receipt(name: str) -> None:
    """Mark a message read locally when Meta will never accept its receipt.

    The agent did read the message; only the courtesy receipt failed, so the
    honest local state is "read". Uses db.set_value rather than doc.save() to
    keep a read receipt from re-running WhatsAppMessage.validate/on_update.
    """
    frappe.db.set_value(
        "WhatsApp Message", name, "status", "marked as read", update_modified=False
    )


@frappe.whitelist()
def mark_wa_messages_read(jid: str = "", ticket: str | int = "") -> int:
    """Mark all unread incoming messages as read (Baileys or frappe_whatsapp)."""
    if not jid and ticket:
        try:
            jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
        except Exception:
            pass

    if jid:
        # Baileys path
        count = _unread_counts_for_user([jid], frappe.session.user).get(jid, 0)
        _mark_conversation_read_for_user(jid)
        _clear_wa_notifications(jid, frappe.session.user)
        frappe.db.commit()
        return count

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
        fields=["name", "message_id", "creation", "whatsapp_account"],
    )
    for row in unread_fw:
        if not row.message_id:
            continue

        # Outside the customer-service window Meta rejects the receipt for good,
        # so don't spend an API call to find that out.
        if time_diff_in_hours(now_datetime(), row.creation) > _WA_RECEIPT_MAX_AGE_HOURS:
            _settle_wa_read_receipt(row.name)
            count += 1
            continue

        backoff_key = f"wa_read_receipt_retry:{row.name}"
        attempts_key = f"wa_read_receipt_attempts:{row.name}"
        if frappe.cache().get_value(backoff_key):
            continue

        account = _wa_account_for_message(row.whatsapp_account)
        if not account:
            continue
        # The documented kill switch for receipts. Until now it only gated the
        # button on the desk form, and this path sent them regardless.
        if not account.allow_auto_read_receipt:
            continue

        ok, error = _post_wa_read_receipt(row.message_id, account)
        if ok:
            _settle_wa_read_receipt(row.name)
            frappe.cache().delete_value(attempts_key)
            count += 1
            continue

        attempts = cint(frappe.cache().get_value(attempts_key)) + 1
        if attempts >= _WA_RECEIPT_MAX_ATTEMPTS:
            # Log once, at the point we stop trying, carrying the real response.
            frappe.log_error(
                f"WhatsApp read receipt failed after {attempts} attempts",
                f"message_id={row.message_id}\n{error}",
                reference_doctype="WhatsApp Message",
                reference_name=row.name,
            )
            _settle_wa_read_receipt(row.name)
            frappe.cache().delete_value(attempts_key)
            count += 1
        else:
            frappe.cache().set_value(attempts_key, attempts, expires_in_sec=_WA_RECEIPT_ATTEMPTS_TTL)
            frappe.cache().set_value(backoff_key, 1, expires_in_sec=_WA_RECEIPT_BACKOFF_TTL)
    return count


@frappe.whitelist()
def get_ticket_wa_unread_count(ticket: str) -> int:
    """Return the unread incoming WhatsApp message count for a ticket's WA tab badge.

    Branches the same way mark_wa_messages_read does: WA Line tickets track read
    state per-agent via the WA Conversation Read State cursor (see
    _unread_counts_for_user), WABA tickets via the shared WhatsApp Message.status
    field.
    """
    if not ticket:
        return 0

    try:
        jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
    except Exception:
        jid = None

    if jid:
        return _unread_counts_for_user([jid], frappe.session.user).get(jid, 0)

    if not frappe.db.exists("DocType", "WhatsApp Message"):
        return 0

    return frappe.db.count(
        "WhatsApp Message",
        {
            "reference_doctype": "HD Ticket",
            "reference_name": ticket,
            "type": "Incoming",
            "status": ["!=", "marked as read"],
        },
    )


@frappe.whitelist()
def mark_all_wa_messages_read(line: str) -> int:
    """Mark all unread incoming Baileys Messages for an entire line as read, for the current agent."""
    if not line:
        return 0
    # Only JIDs that can actually hold unread messages need a cursor written.
    # Anything older than the floor already counts as read (see _unread_floor),
    # so bounding this the same way keeps the DISTINCT off the full history and
    # avoids writing hundreds of read-state rows for long-dead conversations.
    filters = {"line": line, "direction": "Incoming"}
    floor = _unread_floor()
    if floor:
        filters["creation"] = [">", floor]
    jids = frappe.get_all("WA Message", filters=filters, pluck="jid", distinct=True)
    if not jids:
        return 0
    counts = _unread_counts_for_user(jids, frappe.session.user)
    total = sum(counts.values())
    upto = now_datetime()
    for jid in jids:
        _mark_conversation_read_for_user(jid, upto=upto)
    frappe.db.commit()
    return total


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
    """Return HD Tasks for the WhatsApp JID.

    Includes tasks tagged with the jid directly (created from the chat) plus any
    linked via a ticket on the same chat (back-compat with older tasks).
    """
    if not jid:
        return []
    fields = ["name", "title", "status", "priority", "assigned_to", "due_date", "creation"]
    by_name: dict[str, dict] = {}
    # Direct association — tasks created from this chat.
    try:
        for t in frappe.get_all("HD Task", filters={"baileys_jid": jid}, fields=fields):
            by_name[t["name"]] = t
    except Exception:
        pass
    # Back-compat — tasks linked via a ticket on this chat.
    try:
        tickets = frappe.get_all("HD Ticket", filters={"baileys_jid": jid}, pluck="name")
        if tickets:
            for t in frappe.get_all("HD Task", filters={"ticket": ["in", tickets]}, fields=fields):
                by_name[t["name"]] = t
    except Exception:
        pass
    return sorted(by_name.values(), key=lambda r: r.get("creation") or "", reverse=True)


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
    """Create an HD Task tied to the WA conversation.

    The task is associated with the chat directly via baileys_jid. It links to an
    existing ticket for the chat if one already exists, but does NOT create a
    ticket — use create_ticket_from_chat for that.
    """
    try:
        existing_ticket = frappe.db.get_value("HD Ticket", {"baileys_jid": jid}, "name")
    except Exception:
        existing_ticket = None
    task = frappe.get_doc({
        "doctype": "HD Task",
        "title": title,
        "status": "Todo",
        "baileys_jid": jid,
        "baileys_line": line or None,
        "ticket": existing_ticket or None,
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
			"headers": {"apikey": settings.global_api_key or ""},
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
def test_wa_connection(line: str) -> dict:
    """Test actual WhatsApp connectivity for a WA Line.

    Returns a richer status dict compared to get_wa_instance_status:
      - evo_state: raw state string from Evolution API
      - connected: bool
      - profile: dict from /chat/whatsappNumbers (proves real WA reachability), or None
      - error: str if something failed
    """
    import time as _time

    settings = _settings()
    if not settings.enabled or not settings.server_url:
        return {"connected": False, "evo_state": "unknown", "error": "WA API not configured"}

    line_doc = frappe.get_doc("WA Line", line)

    # Step 1: Evolution cached state
    evo_state = "unknown"
    try:
        r = _evo_session.get(
            _url("instance/connectionState", line_doc.instance_name),
            headers=_headers(line_doc),
            timeout=6,
        )
        r.raise_for_status()
        evo_state = (r.json().get("instance") or {}).get("state") or "unknown"
    except Exception as e:
        return {"connected": False, "evo_state": "unknown", "error": f"Could not reach Evolution API: {e}"}

    # Step 2: live WA check — ask Evolution for 1 recent chat.
    # findChats requires an active Baileys socket; if the session is actually dead
    # Evolution returns an error even when connectionState still says "open".
    profile = None
    live_error = None
    try:
        # connected_user is stored as a JID (e.g. "2547XXXX@s.whatsapp.net")
        instance_number = _normalize_phone(getattr(line_doc, "connected_user", "") or "")
        if instance_number:
            cr = _evo_session.post(
                _url("chat/whatsappNumbers", line_doc.instance_name),
                headers=_headers(line_doc),
                json={"numbers": [instance_number]},
                timeout=10,
            )
            if cr.ok:
                results = cr.json()
                if isinstance(results, list) and results:
                    profile = results[0]
                elif isinstance(results, dict):
                    profile = results
            else:
                live_error = f"Live check HTTP {cr.status_code}: {cr.text[:200]}"
        else:
            # No connected_user stored — use fetchInstances which exists in all
            # Evolution API v2 versions and returns the owner JID when connected.
            base = (_settings().server_url or "").rstrip("/")
            cr = _evo_session.get(
                f"{base}/instance/fetchInstances",
                headers=_headers(line_doc),
                params={"instanceName": line_doc.instance_name},
                timeout=10,
            )
            if cr.ok:
                items = cr.json()
                if isinstance(items, dict):
                    items = [items]
                instance_info = items[0] if isinstance(items, list) and items else {}
                owner_jid = instance_info.get("owner") or ""
                profile_name = instance_info.get("profileName") or ""
                profile = {"instance_reachable": True}
                if profile_name:
                    profile["name"] = profile_name
                # Auto-populate connected_user so future checks use the faster path
                if owner_jid and not getattr(line_doc, "connected_user", ""):
                    try:
                        frappe.db.set_value("WA Line", line_doc.name, "connected_user", owner_jid)
                    except Exception:
                        pass
            else:
                live_error = f"Instance fetch HTTP {cr.status_code}: {cr.text[:200]}"
    except Exception as e:
        live_error = str(e)

    truly_connected = evo_state == "open" and profile is not None
    return {
        "connected": truly_connected,
        "evo_state": evo_state,
        "profile": profile,
        "live_error": live_error,
        "checked_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@frappe.whitelist()
def reconnect_wa_line(line: str) -> dict:
    """Restart the Evolution API instance to force a fresh WA connection."""
    settings = _settings()
    if not settings.enabled or not settings.server_url:
        frappe.throw(_("WA API not configured or disabled"))

    line_doc = frappe.get_doc("WA Line", line)
    try:
        resp = _evo_session.post(
            _url("instance/restart", line_doc.instance_name),
            headers=_headers(line_doc),
            timeout=15,
        )
        resp.raise_for_status()
        return {"ok": True, "message": "Instance restarted. WhatsApp will reconnect in a few seconds."}
    except Exception as e:
        frappe.log_error(f"WA reconnect failed for {line}: {e}", "WA Reconnect")
        return {"ok": False, "error": str(e)}


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

def _wa_message_phone(doc) -> str:
	"""The other party's digits for a WhatsApp Message: sender in, recipient out."""
	raw = doc.get("from") if doc.get("type") == "Incoming" else doc.get("to")
	return _normalize_phone(raw or "")


def set_wa_message_normalized_phone(doc, method=None):
	"""Store the conversation key at write time so it can be indexed.

	Computing it in the query instead (REGEXP_REPLACE over `from`/`to`) forces a
	full scan of tabWhatsApp Message on every conversation page load, because an
	expression can never use an index.

	Assigning a field the doctype lacks is a no-op in frappe, so this is safe on
	a site that has not yet imported the Custom Field fixture.
	"""
	doc.normalized_phone = _wa_message_phone(doc)


_WA_DUPLICATE_FLAG = "hd_wa_duplicate"


def _wa_message_id_seen(message_id: str) -> bool:
	"""Whether this wamid is already stored, as of the latest committed write.

	A plain exists() reads the transaction's snapshot under REPEATABLE READ, so
	it cannot see a row a concurrent delivery has inserted but not yet
	committed — which is exactly the fan-out case, where two requests carrying
	the same wamid land a fraction of a second apart. SELECT ... FOR UPDATE is a
	current read and takes a lock on the message_id key, so the second request
	waits for the first to commit and then sees its row.

	This is why the index in add_whatsapp_message_id_index is not optional:
	without it the locking read scans the table and holds locks across it.
	"""
	rows = frappe.db.sql(
		"""SELECT `name` FROM `tabWhatsApp Message`
		WHERE `message_id` = %s LIMIT 1 FOR UPDATE""",
		(message_id,),
	)
	return bool(rows)


def flag_duplicate_whatsapp_message(doc, method=None):
	"""Mark an inbound WhatsApp Message whose wamid has already been stored.

	Meta fans each inbound event out to every app subscribed to the WABA, and
	retries any delivery that does not return 200 promptly. frappe_whatsapp
	checks for neither: message_id carries no unique constraint and post()
	inserts unconditionally, so every delivery stored its own row and the
	conversation showed the customer's message twice.

	The insert cannot be cancelled from before_insert without raising, and
	raising would hand Meta a 500 — which buys a retry of the very delivery
	being rejected, and repeated, gets the app's webhook backed off. So this
	only records the verdict, while the keyed lookup is cheap, and
	on_whatsapp_message_insert drops the row.

	Inbound only. Outgoing rows take their message_id from Meta's send
	response, and collapsing one into an inbound row would erase a reply an
	agent actually sent. Blank ids are left alone for the same reason: a row can
	reach the table before Meta has issued one, and treating "" as a value would
	collapse all of them into a single row.
	"""
	if doc.get("type") != "Incoming":
		return
	message_id = (doc.get("message_id") or "").strip()
	if not message_id:
		return
	if _wa_message_id_seen(message_id):
		doc.flags[_WA_DUPLICATE_FLAG] = True


def _wa_has_normalized_phone() -> bool:
	"""Whether the normalized_phone custom field has been migrated onto this site.

	Custom fields are absent until `bench migrate` imports the fixture, which is
	a real window on every deploy — querying the column before then raises
	OperationalError and would take the WhatsApp page down.
	"""
	cached = frappe.flags.get("wa_normalized_phone_col")
	if cached is None:
		cached = bool(frappe.db.has_column("WhatsApp Message", "normalized_phone"))
		frappe.flags.wa_normalized_phone_col = cached
	return cached


def _waba_phone_sql(alias: str = "") -> str:
	"""SQL for a WhatsApp Message's conversation key: the other party's digits.

	Prefers the stored, indexed normalized_phone column. Falls back to computing
	it inline on sites where the fixture has not been migrated yet — same result,
	just unindexed.
	"""
	p = f"{alias}." if alias else ""
	if _wa_has_normalized_phone():
		return f"{p}`normalized_phone`"
	return (
		f"REGEXP_REPLACE(CASE WHEN {p}`type`='Incoming' THEN {p}`from` ELSE {p}`to` END,"
		" '[^0-9]', '')"
	)


def _waba_open_phones() -> set[str]:
	"""Phones whose linked ticket is still open.

	Mirrors get_my_open_counts()'s definition so the Open filter agrees with the
	sidebar badge. Note the badge counts *tickets* while this counts *phones*,
	so one customer with two open tickets is 2 there and 1 here.
	"""
	rows = frappe.db.sql(
		f"""
		SELECT DISTINCT {_waba_phone_sql('wm')} AS phone
		FROM `tabWhatsApp Message` wm
		INNER JOIN `tabHD Ticket` t
			ON wm.reference_doctype = 'HD Ticket' AND wm.reference_name = t.name
		WHERE t.status NOT IN ('Resolved', 'Closed')
		""",
		as_dict=True,
	)
	return {r.phone for r in rows if r.phone}


def _waba_search_phones(search: str) -> set[str]:
	"""Phones of contacts whose name matches the search text.

	The display name comes from Contact, not from the message, so name search
	has to resolve to phone numbers before the page is cut.
	"""
	like = f"%{search}%"
	phones: set[str] = set()
	contacts = frappe.db.sql(
		"""
		SELECT name, phone, mobile_no
		FROM `tabContact`
		WHERE first_name LIKE %(like)s OR last_name LIKE %(like)s OR name LIKE %(like)s
		""",
		{"like": like},
		as_dict=True,
	)
	if not contacts:
		return phones
	for c in contacts:
		for raw in (c.phone, c.mobile_no):
			norm = _normalize_phone(raw)
			if norm:
				phones.add(norm)
	for row in frappe.get_all(
		"Contact Phone",
		filters={"parenttype": "Contact", "parent": ["in", [c.name for c in contacts]]},
		fields=["phone"],
	):
		norm = _normalize_phone(row.phone)
		if norm:
			phones.add(norm)
	return phones


@frappe.whitelist()
def get_whatsapp_conversations(
	search: str = "",
	conv_filter: str = "all",
	limit: int = CONVERSATIONS_PAGE_SIZE,
	offset: int = 0,
) -> dict:
	"""One entry per phone number for the WABA chat page, most-recent first, one page at a time.

	Returns `{"conversations": [...], "has_more": bool}`.

	Unlike get_wa_conversations() (WA Line, keyed by jid), a WABA "conversation"
	spans every ticket ever created for that phone number.

	This used to select every WhatsApp Message with no LIMIT, reduce to
	latest-per-phone in Python and scan the whole Contact table, so its cost
	tracked total message history rather than what was displayed. Search and the
	filters run here rather than in the browser for the usual reason: a filter
	applied after the page cut silently shrinks pages.
	"""
	limit = max(1, min(cint(limit) or CONVERSATIONS_PAGE_SIZE, 200))
	offset = max(0, cint(offset))
	search = (search or "").strip()
	conv_filter = conv_filter or "all"

	empty: dict = {"conversations": [], "has_more": False}
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return empty

	phone_expr = _waba_phone_sql()
	clauses = ["rn = 1", "phone != ''"]
	params: dict = {"limit": limit + 1, "offset": offset}

	if conv_filter == "awaiting":
		# Last word was the customer's, so we owe them a reply.
		clauses.append("`type` = 'Incoming'")
	elif conv_filter == "open":
		open_phones = _waba_open_phones()
		if not open_phones:
			return empty
		clauses.append("phone IN %(open_phones)s")
		params["open_phones"] = tuple(open_phones)

	if search:
		matched = _waba_search_phones(search)
		# Match the number itself too, so a phone typed in full finds its chat
		# even when no contact record exists for it.
		if matched:
			clauses.append(
				"(phone IN %(matched)s OR message LIKE %(search_like)s"
				" OR phone LIKE %(search_like)s)"
			)
			params["matched"] = tuple(matched)
		else:
			clauses.append("(message LIKE %(search_like)s OR phone LIKE %(search_like)s)")
		params["search_like"] = f"%{search}%"

	# ROW_NUMBER picks each phone's newest message, so the filters above apply to
	# the row the agent actually sees. limit + 1 decides has_more without a
	# second COUNT over the same grouping.
	rows = frappe.db.sql(
		f"""
		SELECT phone, `type`, message, content_type, creation, profile_name
		FROM (
			SELECT
				{phone_expr} AS phone,
				`type`, message, content_type, creation, profile_name,
				ROW_NUMBER() OVER (
					PARTITION BY {phone_expr} ORDER BY creation DESC, name DESC
				) AS rn
			FROM `tabWhatsApp Message`
		) t
		WHERE {' AND '.join(clauses)}
		ORDER BY creation DESC
		LIMIT %(limit)s OFFSET %(offset)s
		""",
		params,
		as_dict=True,
	)

	has_more = len(rows) > limit
	rows = rows[:limit]
	if not rows:
		return empty

	phones = [r.phone for r in rows]
	phones_set = set(phones)

	# Most recent *ticket-linked* message per phone, resolved separately: the
	# newest message overall may predate any ticket link. HD Ticket autonames
	# with an integer key while reference_name is stored as a string, so cast to
	# line up with ticket_info's keys.
	phone_to_ticket: dict[str, int] = {}
	for r in frappe.db.sql(
		f"""
		SELECT phone, reference_name FROM (
			SELECT
				{phone_expr} AS phone, reference_name,
				ROW_NUMBER() OVER (
					PARTITION BY {phone_expr} ORDER BY creation DESC, name DESC
				) AS rn
			FROM `tabWhatsApp Message`
			WHERE reference_doctype = 'HD Ticket' AND IFNULL(reference_name, '') != ''
		) t
		WHERE rn = 1 AND phone IN %(phones)s
		""",
		{"phones": tuple(phones)},
		as_dict=True,
	):
		if r.phone and r.reference_name:
			phone_to_ticket[r.phone] = int(r.reference_name)

	ticket_info: dict[int, dict] = {}
	ticket_names = list(set(phone_to_ticket.values()))
	if ticket_names:
		for t in frappe.get_all(
			"HD Ticket",
			filters={"name": ["in", ticket_names]},
			fields=["name", "status", "priority", "customer", "_assign"],
		):
			assignees = frappe.parse_json(t._assign or "[]") or []
			t["assigned_to"] = assignees[0] if assignees else None
			ticket_info[t.name] = t

	# Non-Done task counts per ticket — a task counts toward a conversation's
	# badge if it's linked to that ticket directly, or if it shares the same
	# customer/company (so company-wide tasks show up on every contact from
	# that company, not just the one the task was originally filed under).
	task_count: dict[int, int] = {}
	if ticket_names:
		company_names = list({t.customer for t in ticket_info.values() if t.customer})
		conditions = ["ticket IN %(tickets)s"]
		values: dict = {"tickets": tuple(str(n) for n in ticket_names)}
		if company_names:
			conditions.append("customer IN %(companies)s")
			values["companies"] = tuple(company_names)
		task_rows = frappe.db.sql(
			f"""
			SELECT ticket, customer
			FROM `tabHD Task`
			WHERE status != 'Done' AND ({" OR ".join(conditions)})
			""",
			values,
			as_dict=True,
		)
		for ticket_id, info in ticket_info.items():
			customer = info.customer
			count = 0
			for row in task_rows:
				row_ticket = int(row.ticket) if row.ticket else None
				if row_ticket == ticket_id or (customer and row.customer == customer):
					count += 1
			task_count[ticket_id] = count

	# normalized-phone → Contact, built only for the phones on this page. This
	# used to scan the whole Contact table plus Contact Phone on every call.
	phone_to_contact: dict[str, dict] = {}
	for c in frappe.get_all(
		"Contact",
		fields=["name", "first_name", "phone", "mobile_no"],
		or_filters={"phone": ("is", "set"), "mobile_no": ("is", "set")},
	):
		for raw in (c.phone, c.mobile_no):
			norm = _normalize_phone(raw)
			if norm in phones_set:
				phone_to_contact.setdefault(norm, c)
	missing = phones_set - set(phone_to_contact)
	if missing:
		for row in frappe.get_all(
			"Contact Phone", fields=["parent", "phone"], filters={"parenttype": "Contact"}
		):
			norm = _normalize_phone(row.phone)
			if norm in missing and norm not in phone_to_contact:
				first_name = frappe.db.get_value("Contact", row.parent, "first_name")
				phone_to_contact[norm] = {"name": row.parent, "first_name": first_name}

	conversations = []
	for r in rows:
		phone = r.phone
		contact = phone_to_contact.get(phone)
		display_name = (contact and contact.get("first_name")) or r.get("profile_name") or phone
		ticket = ticket_info.get(phone_to_ticket.get(phone))
		conversations.append({
			"phone": phone,
			"display_name": display_name,
			"last_message": r["message"] or f"[{r['content_type']}]",
			"last_message_time": str(r["creation"]),
			"last_direction": r["type"],
			"ticket_status": ticket.status if ticket else None,
			"ticket_priority": ticket.priority if ticket else None,
			"company": ticket.customer if ticket else None,
			"assigned_to": ticket.assigned_to if ticket else None,
			"open_task_count": task_count.get(phone_to_ticket.get(phone), 0),
		})
	return {"conversations": conversations, "has_more": has_more}


def _dedupe_wa_rows(rows: list) -> list:
	"""Keep one copy per message_id.

	When several WA Lines are members of the same group, the same message is
	stored once per line. Keep the first copy in creation order. Rows without a
	message_id (old records) are always kept.
	"""
	seen: set = set()
	out: list = []
	for m in rows:
		mid = m.get("message_id")
		if mid:
			if mid in seen:
				continue
			seen.add(mid)
		out.append(m)
	return out


def _finalize_wa_rows(rows: list) -> None:
	"""Normalise WA Message rows for the client and attach edit history, in place."""
	for m in rows:
		if m.get("creation") and not isinstance(m["creation"], str):
			m["creation"] = str(m["creation"])
		m["type"] = "Outgoing" if m["direction"] == "Outgoing" else "Incoming"
		m["attach"] = m.get("media_url") or ""
		m["is_reply"] = 1 if (m.get("reply_to_message_id") and m.get("content_type") != "reaction") else 0
		m["edit_history"] = []

	edited_names = [m["name"] for m in rows if m.get("is_edited")]
	if not edited_names:
		return

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


@frappe.whitelist()
def get_whatsapp_messages(
	jid: str = None,
	ticket: str = None,
	phone: str = None,
	limit: int = 0,
	before: str = None,
	before_name: str = None,
) -> list[dict] | dict:
	"""Return messages for a conversation.

	Baileys/WA path: looks up baileys_jid from ticket and queries WA Message.
	frappe_whatsapp path (ticket): queries WhatsApp Message where reference_name = ticket.
	frappe_whatsapp path (phone): queries WhatsApp Message across ALL tickets ever
	linked to that phone number — used by the WhatsApp Business standalone chat page.

	When `phone` is given with `limit`, returns a paginated page instead of a bare
	list: {"messages": [...oldest to newest...], "has_more": bool}. `before` is the
	`creation` timestamp of the oldest message already loaded on the client — pass
	it back to fetch the next older page.
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

		def _select_messages():
			return (
				frappe.qb.from_(BM)
				.left_join(User).on(User.name == BM.owner)
				.left_join(BC).on(BC.jid == BM.sender_jid)
				.select(
					BM.name, BM.creation, BM.direction, BM.jid, BM.message,
					BM.content_type, BM.media_url, BM.thumbnail_url, BM.sender_jid, BM.sender_name,
					BM.profile_name, BM.message_id, BM.reply_to_message_id, BM.status, BM.owner,
					User.full_name.as_("sender_full_name"),
					BC.phone.as_("sender_phone"),
					BM.is_edited,
					BM.is_deleted,
					BM.edit_unrecoverable,
				)
				.where(BM.jid == jid)
			)

		limit = cint(limit)
		paginated = bool(limit)
		has_more = False
		reply_targets: list = []

		if paginated:
			# Page over real messages only. Reaction rows are ~19% of the table, so
			# counting them against `limit` would return far fewer bubbles than the
			# client asked for. This page's reactions are fetched separately below.
			query = _select_messages().where(BM.content_type != "reaction")
			if before:
				# `creation` is not unique (bulk-imported messages can share a
				# timestamp to the microsecond), so a plain `creation < before`
				# cursor silently drops the tied sibling at a page boundary. Break
				# the tie on `name` to make paging lossless.
				if before_name:
					query = query.where(
						(BM.creation < before)
						| ((BM.creation == before) & (BM.name < before_name))
					)
				else:
					query = query.where(BM.creation < before)
			# Newest-first with one spare row, so a full page tells us older ones exist.
			page = (
				query.orderby(BM.creation, order=frappe.qb.desc)
				.orderby(BM.name, order=frappe.qb.desc)
				.limit(limit + 1)
				.run(as_dict=True)
			)
			has_more = len(page) > limit
			rows = list(reversed(page[:limit]))  # hand back oldest → newest
		else:
			rows = _select_messages().orderby(BM.creation).run(as_dict=True)

		rows = _dedupe_wa_rows(rows)

		if paginated and rows:
			page_ids = {m["message_id"] for m in rows if m.get("message_id")}

			if page_ids:
				# Reactions are stored as their own rows and can sit anywhere in the
				# timeline relative to the message they target, so select them by
				# target rather than by position. The client filters them out of the
				# rendered list and folds them into its reactions map.
				reactions = (
					_select_messages()
					.where(BM.content_type == "reaction")
					.where(BM.reply_to_message_id.isin(list(page_ids)))
					.orderby(BM.creation)
					.run(as_dict=True)
				)
				rows.extend(_dedupe_wa_rows(reactions))

			# A reply whose target scrolled out of the page still has to render its
			# quoted preview. Return those targets separately so they resolve for the
			# preview without appearing as bubbles of their own.
			wanted = {
				m["reply_to_message_id"] for m in rows
				if m.get("reply_to_message_id") and m.get("content_type") != "reaction"
			} - page_ids
			if wanted:
				reply_targets = _dedupe_wa_rows(
					_select_messages()
					.where(BM.message_id.isin(list(wanted)))
					.orderby(BM.creation)
					.run(as_dict=True)
				)

		_finalize_wa_rows(rows)
		_finalize_wa_rows(reply_targets)

		if paginated:
			return {"messages": rows, "has_more": has_more, "reply_targets": reply_targets}
		return rows

	# ── frappe_whatsapp path ─────────────────────────────────────────────────
	if not phone and not ticket:
		return []
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return []

	WM = DocType("WhatsApp Message")
	User = DocType("User")

	paginated = False
	has_more = False
	if phone:
		paginated = bool(limit)
		# Stitch messages across every ticket ever linked to this phone number.
		# `to`/`from` aren't guaranteed to be stored in the same format (raw
		# webhook digits vs. Contact.mobile_no with symbols), so pre-filter on
		# a substring match and confirm with an exact normalized comparison.
		tail = phone[-9:] if len(phone) >= 9 else phone
		query = (
			frappe.qb.from_(WM)
			.left_join(User).on(User.name == WM.owner)
			.select(
				WM.name, WM.creation, WM.type, WM.message, WM.content_type,
				WM.attach, WM.status, WM.profile_name, WM.message_id,
				WM.reply_to_message_id, WM.is_reply, WM.owner,
				WM["from"], WM["to"], WM.reference_name,
				User.full_name.as_("sender_full_name"),
			)
			.where(WM["from"].like(f"%{tail}%") | WM["to"].like(f"%{tail}%"))
		)
		if paginated:
			# Fetched newest-first, capped by `limit`, so this is a hard boundary
			# on how much history a single page can pull regardless of how many
			# tail-match false positives get filtered out below.
			if before:
				query = query.where(WM.creation < before)
			candidates = query.orderby(WM.creation, order=frappe.qb.desc).limit(limit + 1).run(as_dict=True)
		else:
			candidates = query.orderby(WM.creation).run(as_dict=True)

		rows = [
			m for m in candidates
			if _normalize_phone(m["from"]) == phone or _normalize_phone(m["to"]) == phone
		]
		if paginated:
			has_more = len(rows) > limit
			rows = rows[:limit]
			rows.reverse()  # newest-first → chronological for display
	else:
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
		m["edit_unrecoverable"] = 0
		# Meta's API has no unsend-for-everyone equivalent on this path.
		m["is_deleted"] = 0

	if paginated:
		return {"messages": rows, "has_more": has_more}
	return rows


@frappe.whitelist()
def get_wa_message_by_message_id(message_id: str = None, jid: str = None) -> dict | None:
	"""Return one finalized WA Message row by its WhatsApp message_id, scoped to a jid.

	Used by the chat UI to resolve a reply's quoted target on demand when it falls
	outside the loaded page and was not returned in `reply_targets` (e.g. after a
	realtime refresh). Returns None when the target is not stored.
	"""
	if not message_id or not jid:
		return None

	from frappe.query_builder import DocType

	try:
		BM = DocType("WA Message")
		User = DocType("User")
		BC = DocType("WA Contact")
		rows = (
			frappe.qb.from_(BM)
			.left_join(User).on(User.name == BM.owner)
			.left_join(BC).on(BC.jid == BM.sender_jid)
			.select(
				BM.name, BM.creation, BM.direction, BM.jid, BM.message,
				BM.content_type, BM.media_url, BM.thumbnail_url, BM.sender_jid, BM.sender_name,
				BM.profile_name, BM.message_id, BM.reply_to_message_id, BM.status, BM.owner,
				User.full_name.as_("sender_full_name"),
				BC.phone.as_("sender_phone"),
				BM.is_edited,
				BM.is_deleted,
				BM.edit_unrecoverable,
			)
			.where(BM.jid == jid)
			.where(BM.message_id == message_id)
			.orderby(BM.creation)
			.limit(1)
			.run(as_dict=True)
		)
	except Exception:
		return None

	rows = _dedupe_wa_rows(rows)
	if not rows:
		return None
	_finalize_wa_rows(rows)
	return rows[0]


# ── frappe_whatsapp integration handlers ──────────────────────────────────────

def _send_fw_reply(ticket: str, message: str, content_type: str = "text", media_url: str | None = None, reply_to_message_id: str | None = None) -> dict:
	"""Create an Outgoing WhatsApp Message via frappe_whatsapp for this ticket."""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		frappe.throw(_("frappe_whatsapp is not installed."))
	phone = get_contact_phone(ticket)
	if not phone:
		frappe.throw(_("No phone number found for the contact linked to this ticket."))
	# Meta rejects free-form sends outside the 24-hour window with error 131047.
	# Refusing here turns an opaque delivery failure into an actionable message.
	if not _fw_reply_window_open(ticket):
		frappe.throw(
			_("The 24-hour reply window has closed. Send an approved template instead.")
		)
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
		"is_reply": 1 if reply_to_message_id else 0,
		"reply_to_message_id": reply_to_message_id or "",
	})
	msg_doc.insert(ignore_permissions=True)
	assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
	if frappe.session.user not in (frappe.parse_json(assign_json) or []):
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
	# See _publish_fw_message: this runs on the status path, which is the
	# highest-volume webhook traffic there is — every sent/delivered/read for
	# every outgoing message. A forced commit here was a durability barrier per
	# status callback.
	frappe.publish_realtime(
		"helpdesk:whatsapp-status-update",
		message={
			"ticket": str(doc.reference_name),
			"message_name": doc.name,
			"status": doc.status or "",
		},
		after_commit=True,
	)


def on_whatsapp_message_insert(doc, method=None):
	"""Create or link an HD Ticket when a frappe_whatsapp message arrives."""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return

	if doc.flags.get(_WA_DUPLICATE_FLAG):
		# Meta delivered this wamid twice (see flag_duplicate_whatsapp_message).
		# Dropped raw rather than through delete_doc: the row has no children and
		# no links, nothing has seen it, and running a document lifecycle over
		# something that logically never existed only invites side effects.
		#
		# Returning here also leaves reference_doctype unset, which is what stops
		# bot.handle_whatsapp_message — the next after_insert hook — from firing a
		# second automated reply at the customer.
		frappe.db.delete("WhatsApp Message", {"name": doc.name})
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

	# Everything an inbound message needs — contact resolution, ticket creation,
	# agent notifications, the bot — used to run here, inside the HTTP request
	# Meta is still waiting on. Meta retries any delivery it does not get a
	# timely 200 for, and sustained retries get an endpoint deprioritised, so
	# holding its request open for that work is the wrong trade. The webhook now
	# returns as soon as the row is stored and the work happens in a job.
	frappe.enqueue(
		"helpdesk.integrations.wa_ingest.process_incoming_message",
		queue="short",
		job_id=f"wa_ingest_{doc.name}",
		# The job re-reads the row by name, so it must not start before the
		# insert commits or it would find nothing.
		enqueue_after_commit=True,
		message_name=doc.name,
	)


def _announce_incoming(doc, ticket_name, profile_name: str, s) -> None:
	"""Status, agent notification and realtime for a message already linked.

	Each step is guarded on its own. The link is what matters and is already
	written by the time this runs; a notification that fails must not cost the
	agent the ticket. That is not hypothetical — on 2026-08-13 one unguarded
	notification (an acknowledgement email with no outgoing account configured)
	threw from a ticket's after_insert, took the insert with it, and stopped new
	WhatsApp conversations becoming tickets at all.

	Realtime is emitted last and outside the guards' silence: if it fails the
	agent's thread will not refresh by itself, which is worth an Error Log.
	"""
	for step, run in (
		("status", lambda: s.customer_reply_status and _set_ticket_status(ticket_name, s.customer_reply_status)),
		("notification", lambda: _notify_fw_agents(ticket_name, doc.message, profile_name)),
		("realtime", lambda: _publish_fw_message(ticket_name, is_incoming=True, immediate=True)),
	):
		try:
			run()
		except Exception:
			frappe.log_error(
				title=f"WhatsApp ingestion: {step} failed",
				message=f"Message {doc.name} is linked to ticket {ticket_name}.",
			)


def link_incoming_message(doc, s=None) -> None:
	"""Resolve the contact and ticket for one inbound message.

	Split out of on_whatsapp_message_insert so it can run in a job rather than
	in Meta's webhook request. Called only by
	wa_ingest.process_incoming_message, which owns the idempotency check —
	this function assumes it is the first and only run for this message.
	"""
	s = s or _fw_settings()
	if not s or not s.enabled:
		return

	phone = _normalize_phone(doc.get("from") or "")
	if not phone:
		return

	placeholder_domain = s.placeholder_email_domain or "whatsapp.placeholder.local"
	profile_name = doc.profile_name or f"WhatsApp User {phone}"

	# The most recent ticket this number already wrote to. Looked up before the
	# contact is resolved because its linkage is authoritative — an agent may
	# have set it by hand — and because a phone lookup can fail where this
	# cannot (number stored nationally, contact carrying no number at all).
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

	prev_ticket = linked[0].reference_name if linked and linked[0].reference_name else None
	prev_contact = prev_customer = None
	if prev_ticket:
		prev = frappe.db.get_value(
			"HD Ticket", prev_ticket, ["contact", "customer"], as_dict=True
		)
		if prev:
			prev_contact, prev_customer = prev.contact, prev.customer

	contact_name = prev_contact or match_phone_to_contact(phone)

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

	# Reuse that ticket only while it is still live. A resolved or closed one
	# starts a fresh ticket, but its contact/customer carry over (see above).
	existing_ticket = None
	if prev_ticket:
		status_category = frappe.db.get_value("HD Ticket", prev_ticket, "status_category")
		if status_category and status_category != "Resolved":
			timeout = int(s.new_conversation_timeout_hours or 24)
			if time_diff_in_hours(now_datetime(), linked[0].creation) < timeout:
				existing_ticket = prev_ticket

	if existing_ticket:
		doc.db_set("reference_doctype", "HD Ticket", update_modified=False)
		doc.db_set("reference_name", existing_ticket, update_modified=False)
		_announce_incoming(doc, existing_ticket, profile_name, s)
	else:
		subject = (doc.message or "")[:100] or f"WhatsApp from {profile_name}"
		ticket_data = {
			"doctype": "HD Ticket",
			"subject": subject,
			"raised_by": email,
			"description": doc.message or "",
			"via_customer_portal": 0,
			"ticket_channel": "WhatsApp",
		}
		if contact_name:
			ticket_data["contact"] = contact_name
		# HD Ticket.set_customer() can only derive a customer from the contact's
		# Dynamic Links. Carrying it forward preserves a customer an agent set
		# by hand on the previous ticket, which that derivation would miss.
		if prev_customer:
			ticket_data["customer"] = prev_customer
		if s.default_ticket_type:
			ticket_data["ticket_type"] = s.default_ticket_type
		if s.default_team:
			ticket_data["agent_group"] = s.default_team

		original_user = frappe.session.user
		frappe.set_user("Administrator")
		try:
			ticket_doc = frappe.get_doc(ticket_data)
			# raised_by here is a placeholder address for a customer who reached
			# us by phone number, so an acknowledgement email would bounce off a
			# domain that does not exist. The customer already has their
			# acknowledgement — the bot replies on WhatsApp.
			ticket_doc.flags.skip_ack_email = True
			ticket_doc.insert(ignore_permissions=True)
		finally:
			frappe.set_user(original_user)

		doc.db_set("reference_doctype", "HD Ticket", update_modified=False)
		doc.db_set("reference_name", ticket_doc.name, update_modified=False)
		_announce_incoming(doc, ticket_doc.name, profile_name, s)


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
def get_active_whatsapp_ticket_for_phone(phone: str) -> dict:
	"""Resolve which HD Ticket a reply from the WhatsApp Business chat page should
	attach to. Mirrors the open-ticket-within-timeout lookup in
	on_whatsapp_message_insert(); falls back to the most recent ticket for this
	phone (regardless of status) if none is currently open, so a reply always
	has somewhere to go."""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return {"ticket": None}

	phone = _normalize_phone(phone)
	if not phone:
		return {"ticket": None}

	WM = frappe.qb.DocType("WhatsApp Message")
	tail = phone[-9:] if len(phone) >= 9 else phone
	candidates = (
		frappe.qb.from_(WM)
		.select(WM.reference_name, WM.creation, WM["from"])
		.where(WM.reference_doctype == "HD Ticket")
		.where(WM.type == "Incoming")
		.where(WM["from"].like(f"%{tail}%"))
		.orderby(WM.creation, order=frappe.qb.desc)
		.limit(5)
		.run(as_dict=True)
	)
	linked = [c for c in candidates if _normalize_phone(c["from"]) == phone and c.reference_name]
	if not linked:
		return {"ticket": None}

	latest = linked[0]
	s = _fw_settings()
	timeout = int((s and s.new_conversation_timeout_hours) or 24)

	existing_ticket = None
	status_category = frappe.db.get_value("HD Ticket", latest.reference_name, "status_category")
	if status_category and status_category != "Resolved":
		if time_diff_in_hours(now_datetime(), latest.creation) < timeout:
			existing_ticket = latest.reference_name

	return {"ticket": existing_ticket or latest.reference_name}


@frappe.whitelist()
def get_whatsapp_ticket_info(ticket: str | int) -> dict:
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

	window_open = _fw_reply_window_open(ticket)

	assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
	assigned_users = frappe.parse_json(assign_json) or []

	return {
		"has_whatsapp": True,
		"jid": None,
		"is_group": False,
		"is_assigned": frappe.session.user in assigned_users,
		"assignees": assigned_users,
		"reply_window_open": window_open,
		"via_frappe_whatsapp": True,
	}


@frappe.whitelist()
def get_ticket_baileys_link(ticket: str | int) -> dict:
	"""Return baileys_jid and baileys_line for a ticket, or nulls if absent.

	Uses frappe.db.get_value (no field-permission gate) so restricted roles
	that can read HD Ticket don't hit the 'Field not permitted in query' error
	that frappe.client.get_value raises for custom fields.
	"""
	jid, line = None, None
	try:
		jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	except Exception:
		pass
	try:
		line = frappe.db.get_value("HD Ticket", ticket, "baileys_line")
	except Exception:
		pass
	return {"baileys_jid": jid or None, "baileys_line": line or None}


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
		timeout=1800,
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
				# Names actually resolved this run. The row count above barely
				# moves once every contact has been seen once, which is what
				# made the sync button look broken.
				"names": (
					contact_result.get("enriched", 0)
					+ contact_result.get("from_evolution", 0)
				),
				"names_cooldown": contact_result.get("names_cooldown", False),
			},
		)
	except Exception as exc:
		frappe.log_error(str(exc), "WA Sync Job Failed")
		frappe.publish_realtime(
			"helpdesk:wa-sync-complete",
			message={"error": str(exc)},
		)


def _enrich_wa_contacts_from_frappe_contacts() -> int:
	"""Backfill WA Contact.custom_name from matching Frappe Contacts (by phone).

	Only overwrites rows where custom_name is blank or still just a raw pushName
	(i.e. looks like a phone number).  Returns the count of rows updated.
	"""
	# Pull all WA Contacts that have a phone but whose custom_name either looks
	# like a phone number or is blank — these are candidates for enrichment.
	wa_rows = frappe.db.sql(
		"""
		SELECT name, phone, custom_name
		FROM `tabWA Contact`
		WHERE phone IS NOT NULL AND phone != ''
		""",
		as_dict=True,
	)
	if not wa_rows:
		return 0

	# Build a normalized-phone → full_name map from Frappe Contacts.
	# Covers mobile_no, phone, and Contact Phone child table.
	contact_map: dict[str, str] = {}
	for c in frappe.db.sql(
		"SELECT full_name, mobile_no, phone FROM `tabContact` WHERE full_name IS NOT NULL AND full_name != ''",
		as_dict=True,
	):
		for raw in (c.mobile_no, c.phone):
			norm = _normalize_phone(raw or "")
			if norm and norm not in contact_map:
				contact_map[norm] = c.full_name
	for row in frappe.db.sql(
		"SELECT p.phone, c.full_name FROM `tabContact Phone` p JOIN `tabContact` c ON c.name = p.parent WHERE c.full_name != ''",
		as_dict=True,
	):
		norm = _normalize_phone(row.phone or "")
		if norm and norm not in contact_map:
			contact_map[norm] = row.full_name

	if not contact_map:
		return 0

	enriched = 0
	for wa in wa_rows:
		norm = _normalize_phone(wa.phone or "")
		frappe_name = contact_map.get(norm)
		if not frappe_name:
			continue
		# Skip if already has a real name (not just digits/plus/spaces)
		if not _is_placeholder_name(wa.custom_name):
			continue
		frappe.db.set_value("WA Contact", wa.name, "custom_name", frappe_name, update_modified=False)
		enriched += 1

	if enriched:
		frappe.db.commit()
	return enriched


def _contact_name_row(jid: str) -> dict | None:
	"""Return the WA Contact row that owns the display name for this JID.

	Follows canonical_jid so a @lid alias resolves to the PN row that actually
	renders in the UI — writing the name onto the alias would leave the chat
	title unchanged.
	"""
	row = frappe.db.get_value(
		"WA Contact", {"jid": jid}, ["name", "custom_name", "canonical_jid"], as_dict=True
	)
	if row and row.get("canonical_jid"):
		canon = frappe.db.get_value(
			"WA Contact", {"jid": row["canonical_jid"]}, ["name", "custom_name"], as_dict=True
		)
		if canon:
			return canon
	return row


def _apply_resolved_name(jid: str, phone: str, name: str) -> bool:
	"""Store a name resolved from Evolution, replacing only placeholder names.

	Returns True when the stored name actually changed. Row creation and
	@lid → PN merging are delegated to _upsert_contact so these passes cannot
	introduce the duplicate rows that merge exists to prevent.
	"""
	if not jid or _is_placeholder_name(name):
		return False
	row = _contact_name_row(jid)
	if row and not _is_placeholder_name(row.get("custom_name")):
		return False  # a real name is already there — never overwrite an agent's
	if not row:
		_upsert_contact(jid, phone, name)
		row = _contact_name_row(jid)
		if not row:
			return False
	if (row.get("custom_name") or "") == name:
		return True
	frappe.db.set_value("WA Contact", row["name"], "custom_name", name, update_modified=False)
	return True


def _evo_contact_entries(line_doc) -> list[dict]:
	"""Fetch Evolution's whole contact store for one line via chat/findContacts.

	Evolution has shipped both an empty body and a {"where": {}} filter depending
	on version, and wraps the list in several different envelopes, so both bodies
	are tried and every known envelope is unwrapped. Returns [] on any failure —
	this is a best-effort bulk pass and the profile pass still runs after it.
	"""
	for body in ({}, {"where": {}}):
		try:
			resp = _evo_session.post(
				_url("chat/findContacts", line_doc.instance_name),
				headers=_headers(line_doc),
				json=body,
				timeout=30,
			)
			if not resp.ok:
				continue
			data = resp.json()
		except Exception as e:
			frappe.logger().warning(f"findContacts {line_doc.instance_name}: {e}")
			continue

		if isinstance(data, dict):
			for key in ("contacts", "records", "data"):
				if isinstance(data.get(key), list):
					data = data[key]
					break
		if isinstance(data, list):
			return [d for d in data if isinstance(d, dict)]
	return []


def _sync_contacts_from_evolution_store(line_doc) -> int:
	"""Blank-fill contact names from Evolution's contact store. Returns names written.

	One request covers the whole store, which makes this the cheap pass. Its real
	value is that every name it fills is one fewer per-number fetchProfile
	lookup the next pass has to make.
	"""
	own_jid = getattr(line_doc, "connected_user", "") or ""
	filled = 0
	for entry in _evo_contact_entries(line_doc):
		jid = entry.get("id") or entry.get("remoteJid") or ""
		if not jid or jid == own_jid or _is_group(jid) or "@broadcast" in jid:
			continue
		# A saved or verified name beats a self-declared pushName.
		name = ""
		for key in ("name", "verifiedName", "pushName", "notify"):
			candidate = (entry.get(key) or "").strip()
			if candidate and not _is_placeholder_name(candidate):
				name = candidate
				break
		if not name:
			continue
		phone = _phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else ""
		if _apply_resolved_name(jid, phone, name):
			filled += 1
	if filled:
		frappe.db.commit()
	return filled


def _resolve_wa_names_via_profile(line_doc, limit: int = _PROFILE_LOOKUP_LIMIT) -> int:
	"""Resolve still-unnamed contacts for one line via chat/fetchProfile.

	This is the pass that actually produces names for WhatsApp Business accounts:
	their verified profile name never appears in pushName, because the customer
	never sends one, so no amount of message history will ever surface it.

	Candidates are restricted to numbers already present in our WA Message
	history for this line — existing conversations, never a cold list. That
	distinction is what keeps a bulk profile lookup from reading as scraping.
	The SQL predicate only narrows the candidate set; _apply_resolved_name
	re-checks with _is_placeholder_name before writing anything.
	"""
	rows = frappe.db.sql(
		"""
		SELECT c.name AS row_name, c.jid, c.phone, MAX(m.creation) AS last_seen
		FROM `tabWA Contact` c
		INNER JOIN `tabWA Message` m ON m.jid = c.jid AND m.line = %(line)s
		WHERE c.phone IS NOT NULL AND c.phone != ''
		  AND (c.custom_name IS NULL OR c.custom_name = ''
		       OR c.custom_name REGEXP '^[0-9 +()-]+$')
		  AND c.jid NOT LIKE '%%@g.us'
		  AND c.jid NOT LIKE '%%@broadcast'
		GROUP BY c.name, c.jid, c.phone
		ORDER BY last_seen DESC
		LIMIT %(limit)s
		""",
		{"line": line_doc.name, "limit": limit + 1},
		as_dict=True,
	)
	truncated = len(rows) > limit
	rows = rows[:limit]
	if truncated:
		frappe.logger().warning(
			f"fetchProfile: {line_doc.instance_name} has more than {limit} unnamed "
			f"contacts — resolving the {limit} most recently active this run, "
			f"remainder on the next run"
		)

	resolved = 0
	for idx, row in enumerate(rows):
		if idx:
			_time.sleep(_PROFILE_LOOKUP_DELAY)
		try:
			resp = _evo_session.post(
				_url("chat/fetchProfile", line_doc.instance_name),
				headers=_headers(line_doc),
				json={"number": row.phone},
				timeout=20,
			)
			if not resp.ok:
				continue
			profile = resp.json()
		except Exception as e:
			frappe.logger().warning(f"fetchProfile {line_doc.instance_name}: {e}")
			continue

		if not isinstance(profile, dict) or profile.get("numberExists") is False:
			continue
		name = (
			profile.get("name")
			or profile.get("verifiedName")
			or profile.get("pushName")
			or ""
		).strip()
		# An existing number with no name is a genuinely nameless contact —
		# not a failure, and no API will fix it. Leave the number showing.
		if not name:
			continue
		if _apply_resolved_name(row.jid, row.phone, name):
			resolved += 1

	if resolved:
		frappe.db.commit()
	return resolved


def sync_wa_contact_names_from_evolution(limit: int = _PROFILE_LOOKUP_LIMIT) -> dict:
	"""Ask Evolution for names our local message history never carried.

	Runs the cheap bulk store pass first, then the per-number profile pass for
	whatever is still unnamed. A per-line try/except keeps one unreachable
	instance from aborting the whole run.

	Returns {"resolved": int, "cooldown": bool}. The cooldown flag matters: the
	lock doubles as a rate-limit cooldown on live profile lookups, so a second
	press inside the window does no work. Reporting that as "0 names found"
	would read as a broken button, so the caller surfaces it distinctly.
	"""
	settings = _settings()
	if not settings.enabled or not settings.server_url:
		return {"resolved": 0, "cooldown": False}
	if frappe.cache().get_value(_LOCK_SYNC_EVO_CONTACTS):
		return {"resolved": 0, "cooldown": True}
	frappe.cache().set_value(_LOCK_SYNC_EVO_CONTACTS, 1, expires_in_sec=_PROFILE_COOLDOWN_SEC)

	resolved = 0
	for line_name in frappe.get_all("WA Line", pluck="name"):
		try:
			line_doc = frappe.get_doc("WA Line", line_name)
			resolved += _sync_contacts_from_evolution_store(line_doc)
			resolved += _resolve_wa_names_via_profile(line_doc, limit=limit)
		except Exception as e:
			frappe.logger().warning(f"contact name sync failed for {line_name}: {e}")
	return {"resolved": resolved, "cooldown": False}


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
	values = []
	for row in rows:
		jid = row.sender_jid
		name = row.sender_name or row.profile_name or ""
		phone = _phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else ""
		if not phone:
			continue  # Skip unresolved @lid contacts — the gateway will populate them on first message
		doc_name = frappe.generate_hash(length=10)
		values.append((doc_name, jid, phone, name))

	# Guard the empty case: every row can be an @lid without a resolvable phone,
	# which would otherwise build "VALUES " and raise a SQL syntax error.
	if values:
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

	# Enrich custom_name from Frappe Contact where we have a phone match and custom_name is still blank/pushName
	enriched = _enrich_wa_contacts_from_frappe_contacts()

	# Last resort: ask Evolution for names our local message history never carried.
	# Runs after the Frappe Contact pass so the curated CRM name always wins.
	evo = sync_wa_contact_names_from_evolution()

	return {
		"created": len(values),
		"updated": 0,
		"total": len(values),
		"enriched": enriched,
		"from_evolution": evo.get("resolved", 0),
		"names_cooldown": evo.get("cooldown", False),
	}


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
		# jid -> the max "effective creation" timestamp seen among the messages
		# imported into that jid during this run, so cursors can be advanced to
		# the newest *imported* message's time rather than wall-clock "now"
		# (see _mark_conversation_read_for_all_agents call below).
		touched_jids: dict = {}
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
					})
					doc.insert(ignore_permissions=True)
					ts = msg.get("messageTimestamp") or 0
					if ts:
						orig_creation = _dt.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
						frappe.db.set_value("WA Message", doc.name, "creation", orig_creation, update_modified=False)
						effective_creation = get_datetime(orig_creation)
					else:
						# No back-dated timestamp from the API: this message's
						# creation is whatever the DB assigned at insert time.
						effective_creation = get_datetime(doc.creation)
					imported += 1
					prev_max = touched_jids.get(remote_jid)
					if prev_max is None or effective_creation > prev_max:
						touched_jids[remote_jid] = effective_creation
				except Exception as e:
					frappe.log_error(f"Failed to insert WA Message (line={line}): {e}", "WA Old Message Sync")

			frappe.db.commit()
			current_page += 1
			if imported + skipped >= total_limit:
				break

		# Advance each touched jid's cursor to the newest *imported* message's
		# creation time, not wall-clock "now" — this job pages through
		# potentially thousands of messages across many HTTP calls, and a
		# genuinely new live message can be inserted (with a real, current
		# creation) while it's still running. Advancing to "now" would advance
		# past that live message's creation and silently mark it read for
		# every agent; advancing to the newest imported message's own
		# timestamp cannot do that, since a live message's creation is always
		# later than any back-dated historical import's.
		for touched_jid, upto in touched_jids.items():
			_mark_conversation_read_for_all_agents(touched_jid, upto=upto)
		if touched_jids:
			frappe.db.commit()

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
	"""Return approved WhatsApp Templates available for sending outside the 24-hour window."""
	if not frappe.db.exists("DocType", "WhatsApp Templates"):
		return []
	return frappe.get_all(
		"WhatsApp Templates",
		filters={"status": "APPROVED"},
		# `template` is the raw body, still carrying its {{1}} placeholders. The
		# picker lists it so a template is recognisable by its wording rather
		# than by an opaque name; the rendered-for-this-ticket version comes
		# from preview_template_for_ticket once one is selected.
		fields=["name", "template_name", "language_code", "template"],
		order_by="template_name asc",
	)


def _template_field_value(ticket_doc, fieldname: str) -> str:
	"""Resolve one template variable from a ticket.

	Supports `link_field.target_field` so a template can greet someone by name.
	Plain `contact` renders the Contact's document key, which frappe builds as
	`first_name-company_name` — "Hello Shivani Somaia-Acme Ltd" is not something
	to send a customer, so the seeded templates use `contact.first_name`.
	"""
	if "." in fieldname:
		link_field, target = fieldname.split(".", 1)
		link_value = ticket_doc.get(link_field)
		if not link_value:
			return ""
		meta_field = ticket_doc.meta.get_field(link_field)
		doctype = meta_field.options if meta_field else None
		if not doctype:
			return ""
		return str(frappe.db.get_value(doctype, link_value, target) or "")

	raw = ticket_doc.get_formatted(fieldname)
	if raw:
		# str() before strip_html, which is a re.sub and rejects anything else
		# with "expected string or bytes-like object, got 'int'". HD Ticket is
		# autoincrement-named, so `name` — the {{2}} of every seeded template —
		# arrives here as an int on any site whose tickets are numbered, and the
		# preview 500s on the agent the moment they pick a template.
		return frappe.utils.strip_html(str(raw))
	value = ticket_doc.get(fieldname)
	return str(value) if value is not None else ""


def _render_template_for_ticket(ticket: str | int, template_name: str) -> tuple[str, str | None]:
	"""Render a WhatsApp Template against a ticket.

	Returns (rendered_message, body_param). Shared by preview_template_for_ticket
	and send_template_to_ticket so the agent can never be shown a different
	string from the one that goes to Meta.

	body_param is the JSON object frappe_whatsapp's send_template() reads to take
	its explicit-parameter branch. Its default branch misreads sample_values as
	field names and sends empty strings, which Meta rejects with #131008.
	"""
	template_doc = frappe.get_doc("WhatsApp Templates", template_name)
	body_param = None
	params = {}
	if template_doc.sample_values:
		if not template_doc.field_names:
			frappe.throw(
				_("Template {0} has variables but no Field Names are configured. "
				  "Open the WhatsApp Template and set Field Names to the HD Ticket "
				  "field names that should fill each variable.").format(template_name)
			)
		ticket_doc = frappe.get_doc("HD Ticket", ticket)
		field_names = [f.strip() for f in template_doc.field_names.split(",")]
		for i, fn in enumerate(field_names, 1):
			params[str(i)] = _template_field_value(ticket_doc, fn)
		body_param = json.dumps(params)

	# frappe_whatsapp never sets `message` on template sends, leaving the chat
	# bubble blank, so we render the body ourselves for display.
	rendered_message = template_doc.template or ""
	for idx, value in params.items():
		rendered_message = rendered_message.replace("{{" + idx + "}}", str(value))
	return rendered_message, body_param


@frappe.whitelist()
def preview_template_for_ticket(ticket: str | int, template_name: str) -> dict:
	"""Return the template body exactly as it will be sent to this ticket's contact."""
	if not frappe.db.exists("WhatsApp Templates", template_name):
		frappe.throw(_("Template {0} not found.").format(template_name))
	message, _body_param = _render_template_for_ticket(ticket, template_name)
	return {"message": message}


@frappe.whitelist()
def send_template_to_ticket(ticket: str, template_name: str) -> dict:
	"""Send an approved WhatsApp Template to the contact on this ticket.

	Creates an Outgoing WhatsApp Message with the template set; frappe_whatsapp's
	before_insert hook detects the template field and routes to send_template().

	When the template has variables (sample_values set), frappe_whatsapp's default
	fallback incorrectly uses sample_values as field names, returning empty strings
	that Meta rejects with #131008.  We resolve field values here instead and pass
	them as body_param so frappe_whatsapp takes the explicit-param branch.
	"""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		frappe.throw(_("frappe_whatsapp is not installed."))
	if not frappe.db.exists("WhatsApp Templates", template_name):
		frappe.throw(_("Template {0} not found.").format(template_name))
	phone = get_contact_phone(ticket)
	if not phone:
		frappe.throw(_("No phone number found for the contact linked to this ticket."))

	rendered_message, body_param = _render_template_for_ticket(ticket, template_name)

	msg_doc = frappe.get_doc({
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": phone,
		"template": template_name,
		"body_param": body_param,
		"message": rendered_message,
		"reference_doctype": "HD Ticket",
		"reference_name": ticket,
	})
	msg_doc.insert(ignore_permissions=True)
	return {"name": msg_doc.name, "status": msg_doc.status}


@frappe.whitelist()
def get_product_options() -> list[str]:
	"""Placeholder — product options feature not yet implemented for WA API."""
	return []


@frappe.whitelist()
def sync_wa_groups() -> dict:
	"""Resolve group names for the 50 most-recently-active group JIDs (per line) that are
	missing or have a blank name in the group_jids child table.

	Uses findGroupInfos (one request per JID) instead of fetchAllGroups, which times out
	on instances with many groups.

	Deduplication: a Redis lock prevents concurrent or back-to-back runs within 60 seconds.
	"""
	if frappe.cache().get_value(_LOCK_SYNC_GROUPS):
		return {"status": "locked", "created": 0, "updated": 0, "total": 0}

	frappe.cache().set_value(_LOCK_SYNC_GROUPS, 1, expires_in_sec=300)

	settings = _settings()
	if not settings.enabled or not settings.server_url:
		frappe.throw(_("WA API not configured or disabled"))

	lines = frappe.get_all("WA Line", pluck="name")
	created = updated = 0

	for line_name in lines:
		line_doc = frappe.get_doc("WA Line", line_name)

		# 50 most-recently-active distinct group JIDs seen in messages for this line
		rows = frappe.db.sql(
			"""
			SELECT jid, MAX(creation) AS last_seen
			FROM `tabWA Message`
			WHERE line = %s AND jid LIKE '%%@g.us'
			GROUP BY jid
			ORDER BY last_seen DESC
			LIMIT 50
			""",
			line_name,
			as_dict=True,
		)
		candidate_jids = [r.jid for r in rows]
		if not candidate_jids:
			continue

		# Existing rows for this line
		existing_rows = frappe.db.get_all(
			"WhatsApp Group JID",
			filters={"parent": line_name, "parenttype": "WA Line"},
			fields=["name", "jid", "group_name"],
		)
		existing = {r.jid: r for r in existing_rows}

		# Always re-resolve every candidate — a group that already has a name may
		# have been renamed on WhatsApp since. Skipping already-named groups here
		# used to mean sync always reported 0 once every group had been seen once,
		# and renames were never picked up. findGroupInfos is fast (~1s), so
		# re-checking the top 50 per line is cheap relative to the 30-minute job budget.
		need_resolve = candidate_jids

		insert_rows = []
		for jid in need_resolve:
			try:
				resp = _evo_session.get(
					_url("group/findGroupInfos", line_doc.instance_name),
					params={"groupJid": jid},
					headers=_headers(line_doc),
					timeout=15,
				)
				resp.raise_for_status()
				g = resp.json()
				subject = g.get("subject") or g.get("name") or ""
			except _requests.exceptions.HTTPError as e:
				if e.response is not None and e.response.status_code in (404, 400):
					subject = ""  # group gone or bad JID — store with empty name, don't retry
				else:
					frappe.logger().warning(f"sync_wa_groups findGroupInfos {jid}: {e}")
					continue
			except Exception as e:
				frappe.logger().warning(f"sync_wa_groups findGroupInfos {jid}: {e}")
				continue

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
					jid, subject,
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
