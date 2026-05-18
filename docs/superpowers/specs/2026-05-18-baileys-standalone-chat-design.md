# Baileys Standalone WhatsApp Chat — Design Spec

## Goal

Replace the ticket-embedded Baileys chat panel with a fully standalone WhatsApp-like chat interface accessible from the helpdesk sidebar. Incoming Baileys messages no longer create HD Tickets. Agents chat directly with contacts and groups from a dedicated `/whatsapp` page.

## Decisions Made

| Topic | Decision |
|-------|----------|
| Layout | Three-column: helpdesk sidebar · conversation list · chat pane |
| Conversation list | All chats (groups + DMs) sorted by recency, with unread badges and search |
| Ticket integration | Removed entirely — webhook no longer creates HD Tickets |
| Bell notifications | New inbound messages trigger the helpdesk bell for agents |
| Existing Baileys tickets | Left as read-only history; no new ones will be created |

---

## Architecture

### Data flow

```
WhatsApp message arrives
  → Baileys gateway POSTs to /api/method/helpdesk.integrations.baileys.webhook
  → Save Baileys Message (DocType, unchanged)
  → _publish_event() → broadcasts helpdesk:baileys-message over Frappe realtime
  → _notify_agents() → fires bell notification (respects notification_quiet_minutes)
  (ticket creation removed entirely)
```

### Storage

`Baileys Message` DocType is the single source of truth. No changes to its schema needed for this feature. Fields used:

| Field | Purpose |
|-------|---------|
| `jid` | Identifies the conversation (group or DM) |
| `sender_name` | Display name for DMs |
| `message` | Text content / emoji for reactions |
| `content_type` | text / image / video / audio / document / reaction |
| `direction` | Incoming / Outgoing |
| `reply_to_message_id` | Threading |
| `creation` | Used for recency sorting |

Unread count: messages with `direction = "Incoming"` and `creation > last_read_at` for the session. Since there is no persistent per-agent read cursor yet, unread is defined as incoming messages since the agent last opened that conversation (tracked in `localStorage` keyed by JID, updated when the conversation is selected).

---

## Backend Changes

### `helpdesk/integrations/baileys.py`

**`webhook()` — simplify**

Remove `_get_or_create_ticket()` and all ticket-related logic. Keep:
1. Validate API key
2. Parse payload into `Baileys Message`
3. Call `_publish_event(jid, message_doc)`
4. Call `_notify_agents(message_doc)`

**Delete these functions** (dead code once webhook is simplified):
- `_get_or_create_ticket()`
- `_reopen_or_new_ticket()`
- `_set_ticket_status()`

**New `get_baileys_conversations()` (whitelisted)**

Returns one entry per unique JID, sorted by latest message descending.

```python
@frappe.whitelist()
def get_baileys_conversations():
    """Return conversation list for the standalone WhatsApp chat page."""
    BM = frappe.qb.DocType("Baileys Message")
    rows = (
        frappe.qb.from_(BM)
        .select(
            BM.jid,
            BM.sender_name,
            BM.message,
            BM.content_type,
            BM.direction,
            BM.creation,
        )
        .orderby(BM.creation, order=frappe.qb.desc)
        .run(as_dict=True)
    )

    seen = {}
    for r in rows:
        if r["jid"] not in seen:
            seen[r["jid"]] = r

    settings = frappe.get_single("Baileys Gateway Settings")
    group_names = {g.jid: g.group_name for g in (settings.group_jids or [])}

    result = []
    for jid, r in seen.items():
        is_group = jid.endswith("@g.us")
        display_name = group_names.get(jid) or (jid if is_group else r.get("sender_name") or jid.split("@")[0])
        result.append({
            "jid": jid,
            "display_name": display_name,
            "is_group": is_group,
            "last_message": r["message"] or f"[{r['content_type']}]",
            "last_message_time": str(r["creation"]),
            "last_direction": r["direction"],
            "content_type": r["content_type"],
        })

    return result
```

**`_notify_agents()` — adapt for standalone chat**

Reuse the notification pattern from the WhatsApp integration. Fire a bell notification when `direction == "Incoming"`, respecting `notification_quiet_minutes`.

```python
def _notify_agents(msg_doc):
    settings = frappe.get_single("Baileys Gateway Settings")
    quiet = int(settings.notification_quiet_minutes or 0)
    if quiet:
        cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-quiet)
        recent_outgoing = frappe.db.exists("Baileys Message", {
            "jid": msg_doc.jid,
            "direction": "Outgoing",
            "creation": [">", cutoff],
        })
        if recent_outgoing:
            return
    frappe.publish_realtime(
        "helpdesk:baileys-notification",
        {"jid": msg_doc.jid, "message": msg_doc.message, "sender": msg_doc.sender_name},
        after_commit=True,
    )
```

**`get_baileys_messages()` — add jid parameter, keep ticket for compat**

Accept either `jid` directly (new standalone chat) or `ticket` (existing `BaileysGroupChatTab` for historical tickets). When `jid` is provided it takes precedence; otherwise look up `jid` from the ticket's `baileys_jid` field as before.

```python
@frappe.whitelist()
def get_baileys_messages(jid: str = None, ticket: str = None):
    """Return messages for a conversation. Accepts jid directly or ticket name."""
    if not jid and ticket:
        jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
    if not jid:
        return []

    BM = frappe.qb.DocType("Baileys Message")
    rows = (
        frappe.qb.from_(BM)
        .select(
            BM.name, BM.message_id, BM.jid, BM.sender,
            BM.sender_name, BM.message, BM.content_type,
            BM.direction, BM.attach, BM.creation,
            BM.reply_to_message_id, BM.status,
        )
        .where(BM.jid == jid)
        .where(BM.content_type != "reaction")
        .orderby(BM.creation)
        .run(as_dict=True)
    )

    # Build reactions map: message_id → list of {emoji, direction}
    reactions = frappe.get_all(
        "Baileys Message",
        filters={"jid": jid, "content_type": "reaction"},
        fields=["reply_to_message_id", "message as emoji", "direction"],
    )
    reactions_map: dict[str, list] = {}
    for r in reactions:
        reactions_map.setdefault(r["reply_to_message_id"], []).append(
            {"emoji": r["emoji"], "type": r["direction"]}
        )

    for m in rows:
        m["reactions"] = reactions_map.get(m["message_id"], [])
        m["is_reply"] = 1 if (m.get("reply_to_message_id") and m.get("content_type") != "reaction") else 0

    return rows
```

**`send_baileys_reply()` — accept jid directly**

Add `jid: str` as a direct parameter (in addition to keeping `ticket` for backward compat with existing Baileys ticket views). When `jid` is provided, use it directly; otherwise look up `jid` from the ticket's `baileys_jid` field.

---

## Frontend

### New files

#### `desk/src/pages/whatsapp/WhatsAppPage.vue`

Three-column shell page. Holds `selectedJid` ref. Passes it down to `BaileysChat`.

```
┌──────────────────────────────────────────────────────────┐
│ Helpdesk sidebar (existing, unchanged)                   │
├────────────────────────┬─────────────────────────────────┤
│ BaileysConversationList│ BaileysChat                     │
│ (240px, fixed)         │ (flex-1)                        │
│                        │                                 │
│ 🔍 Search...           │  [chat header: name + phone]    │
│ ─────────────────────  │                                 │
│ ● SIMBA SECURITY  12:41│  [WhatsAppBubble messages]      │
│   Hi, any update?      │                                 │
│ ─────────────────────  │  [BaileysReplyBox]              │
│   Dhiren Samji   10:22 │                                 │
│   Sure, I'll check     │                                 │
└────────────────────────┴─────────────────────────────────┘
```

#### `desk/src/components/whatsapp/BaileysConversationList.vue`

- `createResource` calling `get_baileys_conversations`; auto-fetches on mount
- Search input filters by `display_name` and `last_message` client-side
- Listens to `frappe.realtime.on("helpdesk:baileys-message", ...)` → re-fetches conversation list and bumps the matched JID to top
- Emits `select(jid)` when a row is clicked; highlights the active row
- Unread count stored in `localStorage` as `baileys_last_read_{jid}` (ISO timestamp, updated on select)

#### `desk/src/components/whatsapp/BaileysConversationItem.vue`

Props: `{ jid, displayName, isGroup, lastMessage, lastMessageTime, lastDirection, unreadCount }`

- Avatar: circle with initial letter, color derived from JID hash
- Name + last message preview (truncated)
- Timestamp (relative: "12:41", "Yesterday", "Mon")
- Unread badge (green circle with count) when `unreadCount > 0`
- `✓✓` delivery indicator when `lastDirection === "Outgoing"`

#### `desk/src/components/whatsapp/BaileysChat.vue`

Props: `{ jid: string | null }`

- Empty state when `jid` is null: centered WhatsApp icon + "Select a conversation"
- Header: conversation name + (for groups) member count if available; connected phone shown as subtitle using existing `get_connected_phone()`
- Message list: `createResource` on `get_baileys_messages(jid)`; watch `jid` prop and reload on change; scroll to bottom on load/new message
- Reuses `WhatsAppBubble` (reactions + reply-to already wired)
- Reuses `BaileysReplyBox` (reply-to preview, `clearReply`, media send already implemented)
- Listens to `helpdesk:baileys-message` realtime event — if `event.jid === props.jid`, append new message to list and scroll to bottom
- Listens to `helpdesk:whatsapp-status-update` — update bubble status icon

### Modified files

#### `desk/src/router/index.ts`

Add route:
```typescript
{
  path: "/whatsapp",
  name: "WhatsAppChat",
  component: () => import("@/pages/whatsapp/WhatsAppPage.vue"),
},
```

#### `desk/src/components/layouts/layoutSettings.ts`

Add sidebar entry after "Call Logs":
```typescript
{
  label: __("WhatsApp"),
  icon: WhatsAppIcon,
  to: "WhatsAppChat",
},
```

#### `desk/src/stores/notification.ts`

Listen to `helpdesk:baileys-notification` realtime event → increment unread count + play sound (same as existing WhatsApp notification handling).

---

## What Is NOT Changed

- `Baileys Message` DocType schema — unchanged
- `Baileys Gateway Settings` DocType — unchanged
- `BaileysReplyBox.vue` — already supports reply-to and media, no changes needed
- `WhatsAppBubble.vue` — already supports reactions and reply context, no changes needed
- `TicketActivityPanel.vue` — Baileys tab remains for existing tickets (213–222) as read-only history; no new Baileys tickets will be created
- Gateway (`index.js`) — no changes needed
- `send_baileys_reply()`, `send_baileys_reaction()` — keep as-is; `BaileysChat` calls them with `jid` directly

---

## Out of Scope

- Sending new media from the standalone chat page (sending text + reacting + replying is in scope; new image/document attach can be added later)
- Read receipts (mark-as-read) sent back to WhatsApp
- Contact management or merging JIDs with Frappe Contacts
- Pagination of message history (load all messages for a JID)
