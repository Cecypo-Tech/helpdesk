# Multi-WhatsApp Lines via Evolution API

**Date:** 2026-05-22
**Status:** Approved for implementation

## Summary

Replace the single-gateway Baileys integration with a multi-line architecture backed by [Evolution API v2](https://doc.evolution-api.com/). One Evolution API server manages N WhatsApp instances. Each instance is a "line" in Helpdesk. The sidebar shows one collapsible "WhatsApp" section with a link per line (phone/label + unread badge). Analytics gains a line-filter dropdown.

## Motivation

The existing integration runs one custom Node.js Baileys gateway per WhatsApp number. As the number of lines grows, so does the operational overhead (deployments, updates, monitoring). Evolution API is a purpose-built multi-tenant WhatsApp server — one process, N sessions — eliminating that overhead entirely.

---

## Data Model

### New: `Evolution API Settings` (singleton)

Replaces `Baileys Gateway Settings`. Drops all per-connection fields.

| Field | Type | Notes |
|---|---|---|
| `enabled` | Check | Master switch |
| `server_url` | Data | Evolution API base URL, e.g. `https://evolution.example.com` |
| `global_api_key` | Password | Evolution global API key (sent as `apikey` header) |
| `default_ticket_type` | Link → HD Ticket Type | |
| `default_team` | Link → HD Team | |
| `placeholder_email_domain` | Data | Synthetic email domain for group/unknown tickets |
| `unknown_contact_action` | Select | Skip / Create Ticket Only / Create Contact and Ticket |
| `new_conversation_timeout_hours` | Int | DM ticket reuse window |
| `customer_reply_status` | Link → HD Ticket Status | |
| `agent_reply_status` | Link → HD Ticket Status | |
| `append_agent_initials` | Check | Append `^XX` to outgoing messages |
| `restrict_chats_by_team` | Check | Team-scoped chat visibility |
| `notification_quiet_minutes` | Int | Skip bell if agent replied within N minutes |

### New: `Evolution Line` (non-singleton)

One record per WhatsApp number. Admin creates these; instances are paired in the Evolution API UI.

| Field | Type | Notes |
|---|---|---|
| `label` | Data | Friendly name shown in sidebar and analytics filter. Falls back to `instance_name` if blank. |
| `instance_name` | Data | Must exactly match the instance name in Evolution API. Unique. |
| `connected_user` | Link → User | Credited as owner of fromMe-mirrored messages. Falls back to Administrator. |
| `group_jids` | Table → Baileys Gateway Group JID | Per-line group name/team mappings |
| `blocked_jids` | Table → Baileys Blocked JID | Per-line blocklist |

### Modified: `Baileys Message`

Add two fields:

| Field | Type | Notes |
|---|---|---|
| `line` | Link → Evolution Line | Set at webhook time. Null on historical records. |
| `is_read` | Check | 0 on incoming, 1 on outgoing. Set to 1 by `mark_messages_read`. Used for sidebar unread counts. |

### Modified: `HD Ticket` (custom field)

| Field | Type | Notes |
|---|---|---|
| `baileys_line` | Link → Evolution Line | Which line this ticket's conversation belongs to. Required for reply routing. |

### Retained unchanged

- `Baileys Contact` — global, phone-keyed, no line scope needed
- `Baileys Gateway Group JID` — child DocType, reused by `Evolution Line.group_jids`
- `Baileys Blocked JID` — child DocType, reused by `Evolution Line.blocked_jids`
- `baileys_jid` custom field on HD Ticket — still identifies the conversation JID

---

## Backend

### New module: `helpdesk/integrations/evolution.py`

All new functionality lives here. `baileys.py` is kept read-only for historical data queries and is not extended.

#### Core helpers

```python
def _settings():
    return frappe.get_single("Evolution API Settings")

def _line(instance_name: str):
    doc = frappe.get_doc("Evolution Line", {"instance_name": instance_name})
    if not doc:
        frappe.throw(f"Unknown Evolution instance: {instance_name}")
    return doc

def _api_headers():
    return {"apikey": _settings().global_api_key, "Content-Type": "application/json"}

def _api_url(path: str, instance: str) -> str:
    base = (_settings().server_url or "").rstrip("/")
    return f"{base}/{path}/{instance}"
```

#### Webhook endpoint (`POST .../evolution/webhook`)

Evolution API posts all events to a single configured URL. Auth: verify `apikey` header against `settings.global_api_key`.

```
payload.event         → dispatch to handler
payload.instance      → resolve Evolution Line record
payload.data          → message/status data
```

**Events handled:**

| Event | Action |
|---|---|
| `messages.upsert` | Create `Baileys Message`, create/update `HD Ticket`, notify agents |
| `messages.update` | Update `Baileys Message.status` (DELIVERY_ACK → Delivered, READ → Read) |
| `connection.update` | Optional: log connection state changes |

**Text extraction** from `messages.upsert` data:
```
data.message.conversation
  OR data.message.extendedTextMessage.text
  OR data.message.imageMessage.caption
  OR data.message.videoMessage.caption
  OR data.message.documentMessage.caption
```

**fromMe messages** — when `data.key.fromMe == true`, store as `Outgoing` direction, credit `line.connected_user` (or Administrator).

**Phone resolution** — Evolution returns `remoteJid` as `254712345678@s.whatsapp.net`. No LID complexity; extract phone with `jid.split("@")[0]`.

#### Outgoing messages

```python
# Text
POST {server_url}/message/sendText/{instance_name}
body: {"number": phone_or_jid, "text": message}

# Media
POST {server_url}/message/sendMedia/{instance_name}
body: {"number": ..., "mediatype": "image|video|document|audio",
       "media": url_or_base64, "caption": "..."}

# Reaction
POST {server_url}/message/sendReaction/{instance_name}
body: {"key": {"id": msg_id, "remoteJid": jid, "fromMe": false}, "reaction": "👍"}
```

Reply routing: `line_name = frappe.db.get_value("HD Ticket", ticket, "baileys_line")` → `line = frappe.get_doc("Evolution Line", line_name)`.

#### Group participants

```
GET {server_url}/group/findParticipants/{instance_name}?groupJid={jid}
```

Response includes participant phone numbers directly — no LID resolution needed. Enrich names from `Baileys Contact` as before.

#### Conversations list

```
GET {server_url}/chat/findChats/{instance_name}
```

Accepts `line` param in the whitelisted function; frontend passes currently-viewed line.

#### New API: `get_evolution_lines()`

Called by sidebar on mount and on realtime `helpdesk:baileys-message` events.

```python
@frappe.whitelist()
def get_evolution_lines() -> list[dict]:
    lines = frappe.get_all("Evolution Line", fields=["name", "label", "instance_name"])
    for line in lines:
        line["display_label"] = line["label"] or line["instance_name"]
        line["unread"] = frappe.db.count(
            "Baileys Message",
            {"line": line["name"], "direction": "Incoming", "is_read": 0}
        )
    return lines
```

#### Modified: `get_baileys_analytics()` (the one exception in `baileys.py`)

Add optional `line` parameter. When provided, appends `AND line = %(line)s` to all queries. When null, returns combined stats across all lines. The `line` value is the `Evolution Line` docname. This is the only function in `baileys.py` that is modified — it queries `Baileys Message` which now has the `line` field regardless of which integration wrote the record.

#### Realtime events

`_publish_baileys_event()` adds `line` to the socket payload:
```python
{"jid": jid, "is_incoming": True, "line": line.name, "ticket": ticket_name}
```

Frontend uses `line` to refresh only the affected line's unread count.

#### `hooks.py` changes

Add the new Evolution webhook URL to `Frappe Desk`'s allowed guest methods so it can receive unauthenticated POSTs. Keep existing `baileys.py` doc_event hooks for `WhatsApp Message` (frappe_whatsapp integration, unrelated). Remove `upsert_contact_mapping` from guest methods — Evolution API sends contact data via `contacts.upsert` webhook events instead of pushing mappings; contact enrichment is handled inside `evolution.py`'s event dispatcher.

---

## Frontend

### Router

```
/whatsapp/:lineName      →  WhatsAppPage.vue   (was /whatsapp)
/whatsapp/analytics      →  WhatsAppAnalytics.vue  (unchanged)
```

The sidebar link for each line navigates to `/whatsapp/{line.name}`.

### Sidebar (`Sidebar.vue` + `layoutSettings.ts`)

Remove the hardcoded `"WhatsApp"` and `"WA Analytics"` entries from `agentPortalSidebarOptions`.

Add a dynamic `Section` block driven by `get_evolution_lines()`:

```
▾ WhatsApp                          [7]  ← total unread, green badge
    📱 Sales                        [5]  ← active line, green highlight
    📱 Support                      [2]
    📊 Analytics
```

- Section header label: **WhatsApp**
- Total unread on header: sum of all line unread counts, green badge when > 0
- Each line: `SidebarLink` with `line.display_label`, unread badge, navigates to `/whatsapp/:lineName`
- Analytics: fixed sub-link navigating to `/whatsapp/analytics`
- **Collapsed (icon-only) mode:** WhatsApp icon with a green dot when any line has unread — same pattern as the Notifications bell dot

Unread refresh: listen to `helpdesk:baileys-message` realtime event; on each event call `get_evolution_lines()` to re-fetch counts (or increment the specific line's count from the event payload's `line` field for a cheaper update).

### `WhatsAppPage.vue`

Reads `lineName` from `useRoute().params`. Passes as `line` prop to `BaileysConversationList`. Shows line label in the page header.

### `BaileysConversationList.vue`

New prop: `line: string` (Evolution Line docname). All `createResource` calls pass `line` as a param. Conversation list header shows `line.display_label`. Sync contacts / sync groups buttons operate on this line only.

### `BaileysChat.vue`

No structural changes. Send/react/media calls go via ticket ID; the backend resolves the line from `ticket.baileys_line`. The only change: pass `line` prop through to `BaileysReplyBox` in case it needs it for direct-JID sends (non-ticket chats).

### `WhatsAppAnalytics.vue`

Add a line-filter `<select>` dropdown alongside the existing date-range picker in the page header. Options: "All Lines" (null) + one option per `Evolution Line`. On change, re-fetch analytics with `{ line: selectedLine }`. Line options loaded from `get_evolution_lines()`.

---

## Setup Flow (one-time per environment)

1. Deploy and configure Evolution API v2 server
2. In Evolution API UI: create an instance (e.g. `sales`), scan QR to pair
3. In Helpdesk → Evolution API Settings: enter `server_url` and `global_api_key`, enable
4. In Helpdesk → Evolution Line (new record): set `label = "Sales"`, `instance_name = "sales"`
5. In Evolution API: configure webhook URL → `https://{site}/api/method/helpdesk.integrations.evolution.webhook`
6. Messages flow in; line appears in sidebar

---

## What `baileys.py` covers going forward

`baileys.py` is **not modified**. It remains the handler for existing `Baileys Message` records that predate this feature and for the existing `frappe_whatsapp` doc_event hooks (unrelated to Baileys gateway). All new incoming messages are handled by `evolution.py`.

---

## Out of scope

- Evolution API instance create/delete/QR from within Helpdesk UI (managed in Evolution's own UI)
- Migrating historical `Baileys Message.line = null` records (left as-is, excluded from per-line analytics filters)
- Multi-agent read-state (is_read is global, not per-agent)
- WhatsApp Business API provider (Evolution supports it but not addressed here)
