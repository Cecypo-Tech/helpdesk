# Multi-WhatsApp Lines via Evolution API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-instance Baileys gateway with a multi-line WhatsApp architecture backed by Evolution API v2 — one Evolution server, N instances, each shown as a separate collapsible sidebar entry with unread badge.

**Architecture:** New `Evolution API Settings` singleton holds the server URL and global API key. Each WhatsApp number is an `Evolution Line` record (instance_name matches the Evolution API instance). A new `evolution.py` module handles all webhook events and outgoing calls. The sidebar renders a collapsible "WhatsApp" section dynamically from `get_evolution_lines()`.

**Tech Stack:** Frappe/Python backend, Vue 3 + Pinia frontend, Evolution API v2 REST, frappe-ui `createResource`.

**Reference:** Spec at `docs/superpowers/specs/2026-05-22-multi-whatsapp-evolution-design.md`. Evolution API v2 docs at https://doc.evolution-api.com/

---

## File Map

**Create:**
- `helpdesk/helpdesk/doctype/evolution_api_settings/evolution_api_settings.json`
- `helpdesk/helpdesk/doctype/evolution_api_settings/evolution_api_settings.py`
- `helpdesk/helpdesk/doctype/evolution_api_settings/__init__.py`
- `helpdesk/helpdesk/doctype/evolution_api_settings/test_evolution_api_settings.py`
- `helpdesk/helpdesk/doctype/evolution_line/evolution_line.json`
- `helpdesk/helpdesk/doctype/evolution_line/evolution_line.py`
- `helpdesk/helpdesk/doctype/evolution_line/__init__.py`
- `helpdesk/helpdesk/doctype/evolution_line/test_evolution_line.py`
- `helpdesk/integrations/evolution.py`
- `desk/src/stores/evolutionLines.ts`

**Modify:**
- `helpdesk/helpdesk/doctype/baileys_message/baileys_message.json` — add `line`, `is_read` fields
- `helpdesk/helpdesk/fixtures/custom_fields.json` — add `baileys_line` on HD Ticket
- `helpdesk/hooks.py` — fixtures filter + guest method
- `helpdesk/integrations/baileys.py` — add `line` param to `get_baileys_analytics()`
- `desk/src/components/layouts/Sidebar.vue` — dynamic WhatsApp section
- `desk/src/components/layouts/layoutSettings.ts` — remove hardcoded WA entries
- `desk/src/router/index.ts` — `/whatsapp/:lineName` route
- `desk/src/pages/whatsapp/WhatsAppPage.vue` — read `lineName` from params
- `desk/src/components/whatsapp/BaileysConversationList.vue` — `line` prop
- `desk/src/components/whatsapp/BaileysChat.vue` — pass `line` through
- `desk/src/pages/whatsapp/WhatsAppAnalytics.vue` — line filter dropdown

---

## Task 1: Create `Evolution API Settings` DocType

**Files:**
- Create: `helpdesk/helpdesk/doctype/evolution_api_settings/evolution_api_settings.json`
- Create: `helpdesk/helpdesk/doctype/evolution_api_settings/evolution_api_settings.py`
- Create: `helpdesk/helpdesk/doctype/evolution_api_settings/__init__.py`
- Test: `helpdesk/helpdesk/doctype/evolution_api_settings/test_evolution_api_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# helpdesk/helpdesk/doctype/evolution_api_settings/test_evolution_api_settings.py
import frappe
from frappe.tests.utils import FrappeTestCase

class TestEvolutionApiSettings(FrappeTestCase):
    def test_singleton_exists_after_migrate(self):
        self.assertTrue(frappe.db.exists("DocType", "Evolution API Settings"))
        doc = frappe.get_single("Evolution API Settings")
        self.assertIsNotNone(doc)
        self.assertIsNotNone(doc.get("server_url"))

    def test_required_fields_present(self):
        meta = frappe.get_meta("Evolution API Settings")
        field_names = [f.fieldname for f in meta.fields]
        for required in ["enabled", "server_url", "global_api_key",
                         "default_team", "unknown_contact_action",
                         "notification_quiet_minutes"]:
            self.assertIn(required, field_names, f"Missing field: {required}")
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.helpdesk.doctype.evolution_api_settings.test_evolution_api_settings
```
Expected: `ERROR` — module not found.

- [ ] **Step 3: Create `__init__.py`**

```python
# helpdesk/helpdesk/doctype/evolution_api_settings/__init__.py
```
(empty file)

- [ ] **Step 4: Create the DocType JSON**

```json
{
 "actions": [],
 "creation": "2026-05-22 10:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": [
  "section_connection", "enabled", "server_url",
  "col_break_conn", "global_api_key",
  "section_ticket_defaults", "default_ticket_type",
  "col_break_defaults", "default_team",
  "col_break_email", "placeholder_email_domain",
  "section_dm_behaviour", "unknown_contact_action",
  "col_break_dm", "new_conversation_timeout_hours",
  "section_status_automation", "customer_reply_status",
  "col_break_status", "agent_reply_status", "append_agent_initials",
  "section_access", "restrict_chats_by_team",
  "section_notifications", "notification_quiet_minutes"
 ],
 "fields": [
  {"fieldname": "section_connection", "fieldtype": "Section Break", "label": "Connection"},
  {"default": "0", "fieldname": "enabled", "fieldtype": "Check", "label": "Enabled"},
  {"fieldname": "server_url", "fieldtype": "Data", "label": "Server URL",
   "description": "Evolution API v2 base URL, e.g. https://evolution.example.com"},
  {"fieldname": "col_break_conn", "fieldtype": "Column Break"},
  {"fieldname": "global_api_key", "fieldtype": "Password", "label": "Global API Key",
   "description": "Evolution API global key — sent as apikey header."},
  {"fieldname": "section_ticket_defaults", "fieldtype": "Section Break", "label": "Ticket Defaults"},
  {"fieldname": "default_ticket_type", "fieldtype": "Link", "label": "Default Ticket Type",
   "options": "HD Ticket Type"},
  {"fieldname": "col_break_defaults", "fieldtype": "Column Break"},
  {"fieldname": "default_team", "fieldtype": "Link", "label": "Default Team",
   "options": "HD Team"},
  {"fieldname": "col_break_email", "fieldtype": "Column Break"},
  {"default": "whatsapp.placeholder.local", "fieldname": "placeholder_email_domain",
   "fieldtype": "Data", "label": "Placeholder Email Domain",
   "description": "Domain for synthetic emails (group tickets and unknown contacts)."},
  {"fieldname": "section_dm_behaviour", "fieldtype": "Section Break", "label": "Individual DM Behaviour"},
  {"default": "Create Contact and Ticket", "fieldname": "unknown_contact_action",
   "fieldtype": "Select", "label": "Unknown Contact Action",
   "options": "Skip Ticket Creation\nCreate Ticket Only\nCreate Contact and Ticket"},
  {"fieldname": "col_break_dm", "fieldtype": "Column Break"},
  {"default": "24", "fieldname": "new_conversation_timeout_hours", "fieldtype": "Int",
   "label": "New Conversation Timeout (Hours)"},
  {"fieldname": "section_status_automation", "fieldtype": "Section Break", "label": "Status Automation"},
  {"fieldname": "customer_reply_status", "fieldtype": "Link",
   "label": "Status on Customer Reply", "options": "HD Ticket Status"},
  {"fieldname": "col_break_status", "fieldtype": "Column Break"},
  {"fieldname": "agent_reply_status", "fieldtype": "Link",
   "label": "Status on Agent Reply", "options": "HD Ticket Status"},
  {"default": "1", "fieldname": "append_agent_initials", "fieldtype": "Check",
   "label": "Append Agent Initials to Outgoing Messages"},
  {"fieldname": "section_access", "fieldtype": "Section Break", "label": "Chat Access Control"},
  {"default": "0", "fieldname": "restrict_chats_by_team", "fieldtype": "Check",
   "label": "Restrict Chats by Team Assignment"},
  {"fieldname": "section_notifications", "fieldtype": "Section Break", "label": "Notifications"},
  {"default": "10", "fieldname": "notification_quiet_minutes", "fieldtype": "Int",
   "label": "Notification Quiet Period (Minutes)"}
 ],
 "issingle": 1,
 "links": [],
 "modified": "2026-05-22 10:00:00.000000",
 "modified_by": "Administrator",
 "module": "Helpdesk",
 "name": "Evolution API Settings",
 "owner": "Administrator",
 "permissions": [{"create": 1, "read": 1, "role": "System Manager", "write": 1}],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

- [ ] **Step 5: Create the Python controller**

```python
# helpdesk/helpdesk/doctype/evolution_api_settings/evolution_api_settings.py
from frappe.model.document import Document

class EvolutionApiSettings(Document):
    pass
```

- [ ] **Step 6: Migrate**

```bash
cd /home/frappeuser/bench16
bench --site site16.local migrate
```
Expected: migration completes with no errors.

- [ ] **Step 7: Run tests — expect pass**

```bash
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.helpdesk.doctype.evolution_api_settings.test_evolution_api_settings
```
Expected: 2 tests pass.

- [ ] **Step 8: Commit**

```bash
git add helpdesk/helpdesk/doctype/evolution_api_settings/
git commit -m "feat(evolution): add Evolution API Settings singleton DocType"
```

---

## Task 2: Create `Evolution Line` DocType

**Files:**
- Create: `helpdesk/helpdesk/doctype/evolution_line/evolution_line.json`
- Create: `helpdesk/helpdesk/doctype/evolution_line/evolution_line.py`
- Create: `helpdesk/helpdesk/doctype/evolution_line/__init__.py`
- Test: `helpdesk/helpdesk/doctype/evolution_line/test_evolution_line.py`

- [ ] **Step 1: Write the failing test**

```python
# helpdesk/helpdesk/doctype/evolution_line/test_evolution_line.py
import frappe
from frappe.tests.utils import FrappeTestCase

class TestEvolutionLine(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        if frappe.db.exists("Evolution Line", {"instance_name": "_test-line"}):
            frappe.delete_doc("Evolution Line", frappe.db.get_value(
                "Evolution Line", {"instance_name": "_test-line"}, "name"), force=True)

    def tearDown(self):
        frappe.db.rollback()

    def test_create_line(self):
        doc = frappe.get_doc({
            "doctype": "Evolution Line",
            "label": "Test Sales",
            "instance_name": "_test-line",
        }).insert(ignore_permissions=True)
        self.assertEqual(doc.instance_name, "_test-line")
        self.assertEqual(doc.label, "Test Sales")

    def test_instance_name_unique(self):
        frappe.get_doc({"doctype": "Evolution Line", "label": "A",
                        "instance_name": "_test-line"}).insert(ignore_permissions=True)
        with self.assertRaises(frappe.DuplicateEntryError):
            frappe.get_doc({"doctype": "Evolution Line", "label": "B",
                            "instance_name": "_test-line"}).insert(ignore_permissions=True)

    def test_required_fields(self):
        meta = frappe.get_meta("Evolution Line")
        field_names = [f.fieldname for f in meta.fields]
        for required in ["label", "instance_name", "connected_user", "group_jids", "blocked_jids"]:
            self.assertIn(required, field_names, f"Missing field: {required}")
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.helpdesk.doctype.evolution_line.test_evolution_line
```
Expected: `ERROR` — module not found.

- [ ] **Step 3: Create `__init__.py`**

```python
# helpdesk/helpdesk/doctype/evolution_line/__init__.py
```
(empty file)

- [ ] **Step 4: Create the DocType JSON**

```json
{
 "actions": [],
 "autoname": "field:instance_name",
 "creation": "2026-05-22 10:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": [
  "label", "instance_name", "col_break_1", "connected_user",
  "section_groups", "group_jids",
  "section_blocklist", "blocked_jids"
 ],
 "fields": [
  {"fieldname": "label", "fieldtype": "Data", "label": "Label",
   "description": "Friendly name shown in sidebar. Falls back to instance_name if blank."},
  {"fieldname": "instance_name", "fieldtype": "Data", "label": "Instance Name",
   "description": "Must exactly match the instance name configured in Evolution API.",
   "reqd": 1, "unique": 1, "in_list_view": 1},
  {"fieldname": "col_break_1", "fieldtype": "Column Break"},
  {"fieldname": "connected_user", "fieldtype": "Link", "options": "User",
   "label": "Connected User",
   "description": "Credited as owner of fromMe-mirrored (phone-typed) messages. Falls back to Administrator."},
  {"fieldname": "section_groups", "fieldtype": "Section Break", "label": "Group Mappings"},
  {"fieldname": "group_jids", "fieldtype": "Table",
   "label": "WhatsApp Groups", "options": "Baileys Gateway Group JID",
   "description": "Map each group JID to a friendly name and optional team override."},
  {"fieldname": "section_blocklist", "fieldtype": "Section Break", "label": "Blocklist"},
  {"fieldname": "blocked_jids", "fieldtype": "Table",
   "label": "Blocked Numbers / Groups", "options": "Baileys Blocked JID",
   "description": "Messages from these JIDs are silently dropped."}
 ],
 "issingle": 0,
 "links": [],
 "modified": "2026-05-22 10:00:00.000000",
 "modified_by": "Administrator",
 "module": "Helpdesk",
 "name": "Evolution Line",
 "owner": "Administrator",
 "permissions": [
  {"create": 1, "delete": 1, "read": 1, "role": "System Manager", "write": 1}
 ],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": [],
 "title_field": "label"
}
```

- [ ] **Step 5: Create the Python controller**

```python
# helpdesk/helpdesk/doctype/evolution_line/evolution_line.py
from frappe.model.document import Document

class EvolutionLine(Document):
    pass
```

- [ ] **Step 6: Migrate**

```bash
cd /home/frappeuser/bench16
bench --site site16.local migrate
```

- [ ] **Step 7: Run tests — expect pass**

```bash
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.helpdesk.doctype.evolution_line.test_evolution_line
```
Expected: 3 tests pass.

- [ ] **Step 8: Commit**

```bash
git add helpdesk/helpdesk/doctype/evolution_line/
git commit -m "feat(evolution): add Evolution Line DocType"
```

---

## Task 3: Add `line` + `is_read` to `Baileys Message`

**Files:**
- Modify: `helpdesk/helpdesk/doctype/baileys_message/baileys_message.json`

- [ ] **Step 1: Add fields to the JSON**

In `baileys_message.json`, add to `field_order` after `"status"`:
```
"line", "is_read"
```

Add to `fields` array:
```json
{
 "fieldname": "line",
 "fieldtype": "Link",
 "label": "Evolution Line",
 "options": "Evolution Line",
 "in_list_view": 0,
 "read_only": 1
},
{
 "default": "0",
 "fieldname": "is_read",
 "fieldtype": "Check",
 "label": "Is Read",
 "read_only": 1
}
```

- [ ] **Step 2: Migrate**

```bash
cd /home/frappeuser/bench16
bench --site site16.local migrate
```

- [ ] **Step 3: Verify fields exist**

```bash
bench --site site16.local console
```
In the console:
```python
frappe.get_meta("Baileys Message").get_field("line")
# Should return a FieldDef, not None
frappe.get_meta("Baileys Message").get_field("is_read")
# Same
exit()
```

- [ ] **Step 4: Commit**

```bash
git add helpdesk/helpdesk/doctype/baileys_message/baileys_message.json
git commit -m "feat(evolution): add line + is_read fields to Baileys Message"
```

---

## Task 4: Add `baileys_line` custom field to HD Ticket

**Files:**
- Modify: `helpdesk/helpdesk/fixtures/custom_fields.json`
- Modify: `helpdesk/hooks.py`

- [ ] **Step 1: Add to `custom_fields.json`**

Append to the JSON array:
```json
{
 "doctype": "Custom Field",
 "name": "HD Ticket-baileys_line",
 "dt": "HD Ticket",
 "fieldname": "baileys_line",
 "fieldtype": "Link",
 "options": "Evolution Line",
 "label": "WhatsApp Line",
 "insert_after": "baileys_jid",
 "read_only": 1,
 "in_list_view": 0,
 "in_filter": 0,
 "permlevel": 0
}
```

- [ ] **Step 2: Update `hooks.py` fixtures filter**

In `hooks.py`, find the fixtures block and update the fieldname filter:
```python
fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            ["dt", "in", ["HD Ticket", "Customer"]],
            ["fieldname", "in", ["baileys_jid", "baileys_line", "helpdesk_notes"]],
        ],
    }
]
```

- [ ] **Step 3: Import fixtures + migrate**

```bash
cd /home/frappeuser/bench16
bench --site site16.local migrate
bench --site site16.local import-fixtures --app helpdesk
```

- [ ] **Step 4: Verify field on HD Ticket**

```bash
bench --site site16.local console
```
```python
frappe.get_meta("HD Ticket").get_field("baileys_line")
# Should return the FieldDef, not None
exit()
```

- [ ] **Step 5: Commit**

```bash
git add helpdesk/helpdesk/fixtures/custom_fields.json helpdesk/hooks.py
git commit -m "feat(evolution): add baileys_line Link field to HD Ticket"
```

---

## Task 5: Create `evolution.py` — helpers + webhook (messages.upsert)

**Files:**
- Create: `helpdesk/integrations/evolution.py`
- Test (inline via bench console — unit test added in Task 6)

- [ ] **Step 1: Create `evolution.py` with helpers and webhook**

```python
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
```

- [ ] **Step 2: Restart gunicorn to load the new module**

```bash
pkill -f "frappe serve" 2>/dev/null; sleep 1
cd /home/frappeuser/bench16
nohup /bin/bash -c 'cd /home/frappeuser/bench16 && bench serve --port 8004' \
  >> /tmp/frappe_serve.log 2>&1 &
sleep 6 && curl -s -o /dev/null -w "HTTP %{http_code}" http://localhost:8004/api/method/ping
```
Expected: `HTTP 200`.

- [ ] **Step 3: Quick smoke test via console**

```bash
bench --site site16.local console
```
```python
from helpdesk.integrations import evolution
print(evolution._extract_text({"conversation": "Hello"}))
# Expected: ('Hello', 'text')
print(evolution._extract_text({"imageMessage": {"caption": "see pic"}}))
# Expected: ('see pic', 'image')
exit()
```

- [ ] **Step 4: Commit**

```bash
git add helpdesk/integrations/evolution.py
git commit -m "feat(evolution): add evolution.py with webhook handler for messages.upsert"
```

---

## Task 6: `evolution.py` — messages.update + status handler

**Files:**
- Modify: `helpdesk/integrations/evolution.py`
- Create: `helpdesk/integrations/tests/test_evolution_webhook.py`

- [ ] **Step 1: Write failing test**

```python
# helpdesk/integrations/tests/test_evolution_webhook.py
import frappe
from frappe.tests.utils import FrappeTestCase

class TestEvolutionWebhook(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        # Ensure settings exist
        s = frappe.get_single("Evolution API Settings")
        s.enabled = 1
        s.global_api_key = "testkey123"
        s.save(ignore_permissions=True)
        # Create test line
        if not frappe.db.exists("Evolution Line", {"instance_name": "_test-evo"}):
            frappe.get_doc({
                "doctype": "Evolution Line",
                "label": "Test",
                "instance_name": "_test-evo",
            }).insert(ignore_permissions=True)

    def tearDown(self):
        frappe.db.rollback()

    def test_status_update_sets_correct_value(self):
        from helpdesk.integrations.evolution import _handle_update, _line
        line = _line("_test-evo")
        # Create a test outgoing message
        msg = frappe.get_doc({
            "doctype": "Baileys Message",
            "direction": "Outgoing",
            "jid": "254712345678@s.whatsapp.net",
            "message": "test",
            "content_type": "text",
            "message_id": "_test-msg-id-001",
            "status": "Sent",
            "line": line.name,
            "is_read": 1,
        }).insert(ignore_permissions=True)

        result = _handle_update([{
            "key": {"id": "_test-msg-id-001",
                    "remoteJid": "254712345678@s.whatsapp.net", "fromMe": True},
            "update": {"status": 3},   # DELIVERY_ACK → Delivered
        }], line)

        updated_status = frappe.db.get_value("Baileys Message", msg.name, "status")
        self.assertEqual(updated_status, "Delivered")

    def test_unknown_status_code_is_ignored(self):
        from helpdesk.integrations.evolution import _handle_update, _line
        line = _line("_test-evo")
        # Status 99 should not raise
        result = _handle_update([{
            "key": {"id": "_nonexistent-id", "remoteJid": "x@s.whatsapp.net", "fromMe": True},
            "update": {"status": 99},
        }], line)
        self.assertEqual(result["status"], "ok")
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_evolution_webhook
```
Expected: `ERROR` — `_handle_update` not defined (or attribute error).

- [ ] **Step 3: Add `_handle_update` to `evolution.py`**

Add after `_handle_contacts_upsert`:

```python
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
```

Also create the tests `__init__.py`:
```python
# helpdesk/integrations/tests/__init__.py
```

- [ ] **Step 4: Run test — expect pass**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_evolution_webhook
```
Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add helpdesk/integrations/evolution.py \
        helpdesk/integrations/tests/__init__.py \
        helpdesk/integrations/tests/test_evolution_webhook.py
git commit -m "feat(evolution): add messages.update status handler + webhook tests"
```

---

## Task 7: `evolution.py` — outgoing: send text + reaction

**Files:**
- Modify: `helpdesk/integrations/evolution.py`

- [ ] **Step 1: Add `send_evolution_reply()` and `send_evolution_reaction()`**

Append to `evolution.py`:

```python
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

    # Evolution API v2: POST /message/sendText/{instance}
    # Verify exact endpoint in Evolution API docs if behaviour differs.
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
            headers=_headers(),
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

    # Evolution API v2: POST /message/sendReaction/{instance}
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
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        frappe.throw(_("Evolution API reaction failed: {0}").format(str(e)))

    return {"status": "ok"}
```

- [ ] **Step 2: Restart and smoke-test import**

```bash
pkill -f "frappe serve" 2>/dev/null; sleep 1
cd /home/frappeuser/bench16
nohup /bin/bash -c 'cd /home/frappeuser/bench16 && bench serve --port 8004' \
  >> /tmp/frappe_serve.log 2>&1 &
sleep 6 && bench --site site16.local console
```
```python
from helpdesk.integrations.evolution import send_evolution_reply, send_evolution_reaction
print("imports ok")
exit()
```

- [ ] **Step 3: Commit**

```bash
git add helpdesk/integrations/evolution.py
git commit -m "feat(evolution): add send_evolution_reply + send_evolution_reaction"
```

---

## Task 8: `evolution.py` — send media

**Files:**
- Modify: `helpdesk/integrations/evolution.py`

- [ ] **Step 1: Add `send_evolution_media()`**

Append to `evolution.py`:

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add helpdesk/integrations/evolution.py
git commit -m "feat(evolution): add send_evolution_media"
```

---

## Task 9: `evolution.py` — utility APIs

**Files:**
- Modify: `helpdesk/integrations/evolution.py`

- [ ] **Step 1: Add utility API functions**

Append to `evolution.py`:

```python
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

    # Resolve line doc for group labels
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
        # Evolution API v2: GET /group/findParticipants/{instance}?groupJid={jid}
        resp = _requests.get(
            _url("group/findParticipants", line_doc.instance_name),
            params={"groupJid": jid},
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        participants = resp.json().get("participants", [])
    except Exception as e:
        frappe.throw(_("Failed to fetch group participants: {0}").format(str(e)))

    # Normalise to {jid, phone, name, isAdmin}
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

    # Enrich names from Baileys Contact
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
        # Evolution API v2: GET /instance/connectionState/{instance}
        resp = _requests.get(
            _url("instance/connectionState", line_doc.instance_name),
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        state = (data.get("instance") or {}).get("state") or ""
        return {"connected": state == "open", "state": state}
    except Exception as e:
        return {"connected": False, "error": str(e)}
```

- [ ] **Step 2: Write test for get_evolution_lines**

Add to `helpdesk/integrations/tests/test_evolution_webhook.py`:

```python
def test_get_evolution_lines_returns_unread_count(self):
    from helpdesk.integrations.evolution import get_evolution_lines
    line = frappe.get_doc("Evolution Line", {"instance_name": "_test-evo"})
    # Create 2 unread incoming messages
    for i in range(2):
        frappe.get_doc({
            "doctype": "Baileys Message",
            "direction": "Incoming",
            "jid": f"2547{i}@s.whatsapp.net",
            "message": f"msg {i}",
            "content_type": "text",
            "message_id": f"_test-unread-{i}",
            "status": "Received",
            "line": line.name,
            "is_read": 0,
        }).insert(ignore_permissions=True)

    lines = get_evolution_lines()
    test_line = next((l for l in lines if l["name"] == line.name), None)
    self.assertIsNotNone(test_line)
    self.assertGreaterEqual(test_line["unread"], 2)
```

- [ ] **Step 3: Run tests**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_evolution_webhook
```
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add helpdesk/integrations/evolution.py \
        helpdesk/integrations/tests/test_evolution_webhook.py
git commit -m "feat(evolution): add utility APIs — lines, conversations, read, participants"
```

---

## Task 10: Update `hooks.py` + analytics line filter

**Files:**
- Modify: `helpdesk/hooks.py`
- Modify: `helpdesk/integrations/baileys.py` (analytics only)

- [ ] **Step 1: Register evolution webhook as guest method in `hooks.py`**

Find the `override_whitelisted_methods` or add a new `guest_methods` section. Check hooks.py for the pattern. Add:

```python
# After the fixtures block in hooks.py:
override_whitelisted_methods = {
    # No overrides needed — evolution webhook is a new endpoint.
}

# Allow the Evolution API server to POST without session auth:
# (add this alongside any existing guest_methods entries)
```

Actually, `@frappe.whitelist(allow_guest=True)` on the function is sufficient — no hooks.py change needed for guest access. Just verify by testing the endpoint.

What DOES need hooks.py is removing `upsert_contact_mapping` from guest methods if it was listed. Check with:

```bash
grep -n "upsert_contact_mapping\|guest_methods\|allow_guest" /home/frappeuser/bench16/apps/helpdesk/helpdesk/hooks.py
```

If present, remove it. If not present (likely), skip.

- [ ] **Step 2: Add `line` param to `get_baileys_analytics()` in `baileys.py`**

Find `get_baileys_analytics` in `baileys.py` (around line 1124). Update the signature and add filtering:

```python
@frappe.whitelist()
def get_baileys_analytics(from_date: str = None, to_date: str = None, line: str = None) -> dict:
    """Return WhatsApp analytics for the given date range, optionally filtered by Evolution Line."""
```

In each SQL query block, add a line filter when `line` is provided. For example, the summary query becomes:

```python
line_filter = "AND line = %(line)s" if line else ""
summary = frappe.db.sql(
    f"""
    SELECT
        COUNT(*) as total,
        SUM(direction = 'Incoming') as incoming,
        SUM(direction = 'Outgoing') as outgoing,
        COUNT(DISTINCT jid) as conversations
    FROM `tabBaileys Message`
    WHERE creation BETWEEN %(from_dt)s AND %(to_dt)s
      AND content_type != 'reaction'
      {line_filter}
    """,
    {"from_dt": from_dt, "to_dt": to_dt, "line": line or ""},
    as_dict=True,
)[0]
```

Apply the same `line_filter` pattern to the `daily`, `hourly`, and any other SQL queries in `get_baileys_analytics()`.

- [ ] **Step 3: Restart and smoke-test**

```bash
pkill -f "frappe serve" 2>/dev/null; sleep 1
cd /home/frappeuser/bench16
nohup /bin/bash -c 'cd /home/frappeuser/bench16 && bench serve --port 8004' \
  >> /tmp/frappe_serve.log 2>&1 &
sleep 6
curl -s -o /dev/null -w "HTTP %{http_code}" http://localhost:8004/api/method/ping
```
Expected: `HTTP 200`.

- [ ] **Step 4: Commit**

```bash
git add helpdesk/hooks.py helpdesk/integrations/baileys.py
git commit -m "feat(evolution): add line filter to analytics + hooks cleanup"
```

---

## Task 11: Frontend — Pinia store + router update

**Files:**
- Create: `desk/src/stores/evolutionLines.ts`
- Modify: `desk/src/router/index.ts`

- [ ] **Step 1: Create the Pinia store**

```typescript
// desk/src/stores/evolutionLines.ts
import { defineStore } from "pinia";
import { createResource } from "frappe-ui";
import { ref, computed } from "vue";

export interface EvolutionLine {
  name: string;
  label: string;
  instance_name: string;
  display_label: string;
  unread: number;
}

export const useEvolutionLinesStore = defineStore("evolutionLines", () => {
  const lines = ref<EvolutionLine[]>([]);

  const linesResource = createResource({
    url: "helpdesk.integrations.evolution.get_evolution_lines",
    auto: true,
    onSuccess(data: EvolutionLine[]) {
      lines.value = data || [];
    },
  });

  const totalUnread = computed(() =>
    lines.value.reduce((sum, l) => sum + (l.unread || 0), 0)
  );

  function reload() {
    linesResource.reload();
  }

  return { lines, totalUnread, reload, loading: linesResource.loading };
});
```

- [ ] **Step 2: Update router — add parameterised route**

In `desk/src/router/index.ts`, replace:
```typescript
{
  path: "/whatsapp",
  name: "WhatsAppChat",
  component: () => import("@/pages/whatsapp/WhatsAppPage.vue"),
},
```
With:
```typescript
{
  path: "/whatsapp/:lineName",
  name: "WhatsAppChat",
  component: () => import("@/pages/whatsapp/WhatsAppPage.vue"),
},
```

- [ ] **Step 3: Build and check for TypeScript errors**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | grep -E "error|Error|TS[0-9]" | head -20
```
Expected: no TypeScript errors. If errors appear, fix them before proceeding.

- [ ] **Step 4: Commit**

```bash
git add desk/src/stores/evolutionLines.ts desk/src/router/index.ts
git commit -m "feat(evolution): add evolutionLines Pinia store + parameterised /whatsapp/:lineName route"
```

---

## Task 12: Frontend — Sidebar dynamic WhatsApp section

**Files:**
- Modify: `desk/src/components/layouts/layoutSettings.ts`
- Modify: `desk/src/components/layouts/Sidebar.vue`

- [ ] **Step 1: Remove hardcoded WhatsApp entries from `layoutSettings.ts`**

Remove these two entries from `agentPortalSidebarOptions`:
```typescript
{
  label: __("WhatsApp"),
  icon: WhatsAppIcon,
  to: "WhatsAppChat",
},
{
  label: __("WA Analytics"),
  icon: LucideBarChart2,
  to: "WhatsAppAnalytics",
},
```
Also remove the unused `WhatsAppIcon` and `LucideBarChart2` imports if no other entries use them.

- [ ] **Step 2: Add dynamic WhatsApp section to `Sidebar.vue`**

In `Sidebar.vue`, add the import:
```typescript
import { useEvolutionLinesStore } from "@/stores/evolutionLines";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";
import LucideBarChart2 from "~icons/lucide/bar-chart-2";
```

Add inside `<script setup>`:
```typescript
const evolutionLinesStore = useEvolutionLinesStore();
const { lines: evolutionLines, totalUnread: waUnread } = storeToRefs(evolutionLinesStore);

// Refresh line unread counts on incoming WA messages
const { $socket } = globalStore();
onMounted(() => {
  $socket.on("helpdesk:baileys-message", () => {
    evolutionLinesStore.reload();
  });
});
onBeforeUnmount(() => {
  $socket.off("helpdesk:baileys-message", evolutionLinesStore.reload);
});
```

Add the import at the top of `<script setup>`:
```typescript
import { globalStore } from "@/stores/globalStore";
import { onBeforeUnmount } from "vue";
```

In the template, after the Notifications `<div>` block and before the `<div class="overflow-y-auto">` block, add:

```html
<!-- WhatsApp lines section (dynamic, driven by Evolution API) -->
<div v-if="evolutionLines.length" class="mb-1">
  <!-- Collapsed mode: single icon with total unread dot -->
  <div v-if="!isExpanded" class="relative my-0.5">
    <SidebarLink
      :label="__('WhatsApp')"
      :icon="WhatsAppIcon"
      :is-expanded="false"
      :is-active="route.path.startsWith('/whatsapp')"
      :to="evolutionLines.length === 1
            ? { name: 'WhatsAppChat', params: { lineName: evolutionLines[0].name } }
            : { name: 'WhatsAppChat', params: { lineName: evolutionLines[0].name } }"
    />
    <span
      v-if="waUnread > 0"
      class="absolute left-1 top-1 size-1.5 rounded-full bg-green-500"
    />
  </div>

  <!-- Expanded mode: collapsible section header + per-line links -->
  <template v-else>
    <div
      class="flex cursor-pointer items-center gap-1.5 px-2 mt-3 mb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-gray-5 select-none"
      @click="waExpanded = !waExpanded"
    >
      <FeatherIcon
        name="chevron-right"
        class="h-3 w-3 text-ink-gray-5 transition-transform duration-200"
        :class="{ 'rotate-90': waExpanded }"
      />
      <span class="flex-1">{{ __("WhatsApp") }}</span>
      <Badge
        v-if="waUnread > 0"
        :label="waUnread > 99 ? '99+' : String(waUnread)"
        theme="green"
        variant="subtle"
        class="text-[10px]"
      />
    </div>
    <nav v-if="waExpanded" class="flex flex-col">
      <SidebarLink
        v-for="line in evolutionLines"
        :key="line.name"
        :icon="WhatsAppIcon"
        :label="line.display_label"
        :to="{ name: 'WhatsAppChat', params: { lineName: line.name } }"
        :is-expanded="true"
        :is-active="route.params.lineName === line.name"
        class="my-0.5 pl-5"
      >
        <template #right>
          <Badge
            v-if="line.unread > 0"
            :label="line.unread > 99 ? '99+' : String(line.unread)"
            theme="green"
            variant="subtle"
          />
        </template>
      </SidebarLink>
      <SidebarLink
        :icon="LucideBarChart2"
        :label="__('Analytics')"
        :to="{ name: 'WhatsAppAnalytics' }"
        :is-expanded="true"
        :is-active="route.name === 'WhatsAppAnalytics'"
        class="my-0.5 pl-5"
      />
    </nav>
  </template>
</div>
```

Add `waExpanded` ref in `<script setup>`:
```typescript
const waExpanded = useStorage("wa-sidebar-expanded", true);
```

Add import:
```typescript
import { FeatherIcon } from "frappe-ui";
```
(FeatherIcon is likely already imported — check first and skip if so.)

- [ ] **Step 3: Build + check for errors**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | grep -E "error|Error|TS[0-9]" | head -20
```

- [ ] **Step 4: Commit**

```bash
git add desk/src/components/layouts/Sidebar.vue \
        desk/src/components/layouts/layoutSettings.ts
git commit -m "feat(evolution): dynamic WhatsApp sidebar section with per-line unread badges"
```

---

## Task 13: Frontend — WhatsAppPage + BaileysConversationList

**Files:**
- Modify: `desk/src/pages/whatsapp/WhatsAppPage.vue`
- Modify: `desk/src/components/whatsapp/BaileysConversationList.vue`
- Modify: `desk/src/components/whatsapp/BaileysChat.vue`

- [ ] **Step 1: Update `WhatsAppPage.vue` to read `lineName` from route**

In `WhatsAppPage.vue`, add `useRoute` import and read the param:

```typescript
import { useRoute } from "vue-router";

const route = useRoute();
const lineName = computed(() => String(route.params.lineName || ""));
```

Pass `lineName` to `BaileysConversationList`:

```html
<BaileysConversationList
  ref="convListRef"
  :line="lineName"
  :selectedJid="selectedJid"
  @select="onSelect"
/>
```

- [ ] **Step 2: Update `BaileysConversationList.vue` — add `line` prop**

Add the `line` prop to the props definition:
```typescript
const props = defineProps<{
  line: string;
  selectedJid: string;
}>();
```

Update every `createResource` that calls a backend API to pass `line: props.line` in params. The key resources to update:

**`conversations` resource** — change `url` from `get_baileys_conversations` to `get_evolution_conversations` and add `line` param:
```typescript
const conversations = createResource({
  url: "helpdesk.integrations.evolution.get_evolution_conversations",
  params: { line: props.line },
  auto: true,
});
```

**`syncContactsResource`**:
```typescript
const syncContactsResource = createResource({
  url: "helpdesk.integrations.evolution.sync_evolution_contacts",  // see note
  ...
});
```
Note: `sync_baileys_contacts` and `sync_baileys_groups` call the old gateway. For now, keep them pointing to the old baileys.py endpoints — these are non-critical and can be migrated later. Only the conversations list is critical.

**`markReadResource`** — update to use evolution endpoint:
```typescript
const markReadResource = createResource({
  url: "helpdesk.integrations.evolution.mark_evolution_messages_read",
});
```

Also watch `props.line` and reload conversations when it changes:
```typescript
watch(() => props.line, () => {
  conversations.reload();
});
```

- [ ] **Step 3: Update `BaileysChat.vue` — pass `line` to reply box**

Find where `BaileysReplyBox` is used and pass the line:

```html
<BaileysReplyBox
  :ticketId="ticketId"
  :line="line"
  :replyTo="replyingTo"
  ...
/>
```

Add `line` to BaileysChat's props (if not already there):
```typescript
const props = defineProps<{
  jid: string;
  line?: string;
  // ... existing props
}>();
```

Update `send_baileys_reply` calls in `BaileysChat.vue` to use `send_evolution_reply`:
```typescript
const sendReplyResource = createResource({
  url: "helpdesk.integrations.evolution.send_evolution_reply",
  ...
});
```

- [ ] **Step 4: Update `BaileysReplyBox.vue` — use evolution endpoints**

Add `line` prop:
```typescript
const props = defineProps<{
  ticketId: string;
  line?: string;
  replyTo: Record<string, any> | null;
}>();
```

Update send and media resource URLs:
```typescript
// sendResource
url: "helpdesk.integrations.evolution.send_evolution_reply"

// mediaResource
url: "helpdesk.integrations.evolution.send_evolution_media"
```

- [ ] **Step 5: Build**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | grep -E "error|Error|TS[0-9]" | head -20
```

- [ ] **Step 6: Commit**

```bash
git add desk/src/pages/whatsapp/WhatsAppPage.vue \
        desk/src/components/whatsapp/BaileysConversationList.vue \
        desk/src/components/whatsapp/BaileysChat.vue \
        desk/src/components/whatsapp/BaileysReplyBox.vue
git commit -m "feat(evolution): wire WhatsAppPage + conversation components to evolution.py APIs"
```

---

## Task 14: Frontend — `WhatsAppAnalytics.vue` line filter

**Files:**
- Modify: `desk/src/pages/whatsapp/WhatsAppAnalytics.vue`

- [ ] **Step 1: Add line filter dropdown**

In `<script setup>`, add:
```typescript
import { useEvolutionLinesStore } from "@/stores/evolutionLines";
import { storeToRefs } from "pinia";

const evolutionLinesStore = useEvolutionLinesStore();
const { lines: evolutionLines } = storeToRefs(evolutionLinesStore);
const selectedLine = ref<string>("");  // "" = All Lines
```

Update the analytics `createResource` to include `line` in params:
```typescript
const analytics = createResource({
  url: "helpdesk.integrations.baileys.get_baileys_analytics",
  params: computed(() => ({
    from_date: fromDate.value,
    to_date: toDate.value,
    line: selectedLine.value || null,
  })),
  auto: true,
});
```

Watch `selectedLine` to re-fetch:
```typescript
watch(selectedLine, () => analytics.reload());
```

- [ ] **Step 2: Add dropdown to the template header**

In the header `<div>` alongside the date preset buttons, add before the date presets:
```html
<!-- Line filter dropdown -->
<select
  v-if="evolutionLines.length > 1"
  v-model="selectedLine"
  class="rounded border border-outline-gray-2 bg-surface-white px-2 py-1 text-xs text-ink-gray-7 focus:outline-none"
>
  <option value="">{{ __("All Lines") }}</option>
  <option v-for="line in evolutionLines" :key="line.name" :value="line.name">
    {{ line.display_label }}
  </option>
</select>
```

Note: The dropdown is hidden (`v-if`) when there's only one line — no point filtering when there's nothing to filter.

- [ ] **Step 3: Build + verify**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | grep -E "error|Error|TS[0-9]" | head -20
```

- [ ] **Step 4: Commit**

```bash
git add desk/src/pages/whatsapp/WhatsAppAnalytics.vue
git commit -m "feat(evolution): add per-line filter dropdown to WhatsApp Analytics"
```

---

## Task 15: End-to-end setup + push

- [ ] **Step 1: Export fixtures**

```bash
cd /home/frappeuser/bench16
bench --site site16.local export-fixtures --app helpdesk
```

- [ ] **Step 2: Clear cache + migrate**

```bash
bench --site site16.local clear-cache
bench --site site16.local migrate
```

- [ ] **Step 3: Run all evolution tests**

```bash
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.helpdesk.doctype.evolution_api_settings.test_evolution_api_settings
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.helpdesk.doctype.evolution_line.test_evolution_line
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_evolution_webhook
```
Expected: all tests pass.

- [ ] **Step 4: Manual smoke test**

In Frappe Desk → Evolution API Settings: set `enabled = 1`, `server_url = http://localhost:3000` (or your Evolution API URL), `global_api_key = yourkey`. Save.

In Frappe Desk → Evolution Line → New: set `label = "Sales"`, `instance_name = "sales"`. Save.

Open the Helpdesk app → verify sidebar shows a "WhatsApp" collapsible section with "Sales" underneath. Click "Sales" → verify you land at `/whatsapp/<lineName>`. Check there are no console errors.

- [ ] **Step 5: Commit + push**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add -A
git commit -m "feat(evolution): complete multi-line WhatsApp via Evolution API"
git push origin develop
```

---

## Notes for the Implementer

1. **Evolution API v2 endpoint shapes** — The send/group/connection endpoints listed in this plan follow the v2 REST spec. If a request returns 404, check the [Evolution API docs](https://doc.evolution-api.com/) for the current endpoint path — minor path changes happen between releases.

2. **`send_evolution_reply` vs `send_baileys_reply`** — The old `send_baileys_reply` is still used by the `TicketActivityPanel` (WhatsApp tab on tickets). Update those call sites in `BaileysChat.vue` / `BaileysReplyBox.vue` as part of Task 13.

3. **Sync contacts / sync groups** — `sync_baileys_contacts()` and `sync_baileys_groups()` call the old custom gateway. These are non-critical (used for bulk sync buttons) and can remain pointing at the old functions until the old gateway is fully retired.

4. **`is_read` backfill** — Existing `Baileys Message` records have `is_read = 0` (the default). If you want historical messages to not inflate the unread count, run once in bench console: `frappe.db.sql("UPDATE \`tabBaileys Message\` SET is_read=1 WHERE line IS NULL")`.

5. **Analytics** — `get_baileys_analytics()` queries the `Baileys Message` table which holds records from both the old Baileys gateway and Evolution API. The `line` filter works for new messages only; old messages have `line = null` and appear in the "All Lines" view.
