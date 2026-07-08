# WhatsApp Business (WABA) Chat Interface — Design Spec

## Goal

Two problems reported against WABA (`frappe_whatsapp`) tickets:

1. Tickets auto-created from an incoming WhatsApp Business message show `via Email` in the ticket header instead of `via WhatsApp`.
2. There is no chat-style, cross-ticket view of WhatsApp Business conversations. Agents can only see WABA messages one ticket at a time, inside that ticket's "WhatsApp" tab — unlike the WA Line (Evolution API) integration, which has a dedicated `/whatsapp` page with a conversation list on the left (`BaileysConversationList.vue`) and a chat pane on the right (`BaileysChat.vue`).

This spec covers both: a one-line/one-patch bug fix, and a new standalone chat page for WABA that mirrors the WA Line page's shape.

## Decisions Made

| Topic | Decision |
|-------|----------|
| `ticket_channel` bug | Fix `on_whatsapp_message_insert()` to set `ticket_channel: "WhatsApp"` on ticket creation |
| Existing mislabeled tickets | Backfill via a one-off patch, not just fixed going forward |
| Conversation unit | One row per **phone number**, spanning all HD Tickets ever created for that phone (not one row per ticket) |
| Right-pane content | Chat-only (name, phone, messages, reply box) — no inline ticket status/priority/SLA editing. Link out to the full ticket page for that |
| Navigation | New sidebar entry ("WhatsApp Business"), separate from the existing "WhatsApp" (WA Line) entry |
| Existing ticket-embedded WhatsApp tab | Left untouched — this is an additive surface, not a replacement |
| New chat composer (agent-initiated, no prior inbound) | Out of scope — WABA's 24h window means agents can't originate new conversations anyway |

---

## Part 1 — `ticket_channel` bug fix

### Root cause

`helpdesk/helpdesk/doctype/hd_ticket/hd_ticket.py:77-78`:
```python
if not self.ticket_channel:
    self.ticket_channel = "Portal" if self.via_customer_portal else "Email"
```
`on_whatsapp_message_insert()` (`helpdesk/integrations/wa.py:2342-2364`) builds `ticket_data` without `ticket_channel`, so every WABA-originated ticket silently defaults to `"Email"`.

### Fix

Add one key to `ticket_data` in `on_whatsapp_message_insert()`:
```python
ticket_data = {
    "doctype": "HD Ticket",
    "subject": subject,
    "raised_by": email,
    "description": doc.message or "",
    "via_customer_portal": 0,
    "ticket_channel": "WhatsApp",
}
```

### Backfill patch

New patch module `helpdesk/patches/backfill_whatsapp_ticket_channel.py`, registered in `patches.txt`:

```python
import frappe

def execute():
    if not frappe.db.exists("DocType", "WhatsApp Message"):
        return
    tickets = frappe.db.sql(
        """
        SELECT DISTINCT wm.reference_name AS ticket
        FROM `tabWhatsApp Message` wm
        INNER JOIN `tabHD Ticket` t ON t.name = wm.reference_name
        WHERE wm.reference_doctype = 'HD Ticket'
          AND t.ticket_channel != 'WhatsApp'
        """,
        as_dict=True,
    )
    for row in tickets:
        frappe.db.set_value("HD Ticket", row.ticket, "ticket_channel", "WhatsApp", update_modified=False)
```

Runs once via `bench migrate`. Only touches tickets that actually have at least one linked `WhatsApp Message` — never reclassifies a genuinely email/portal ticket.

---

## Part 2 — Standalone WhatsApp Business chat page

### Architecture

```
Sidebar: "WhatsApp Business" (new entry, next to existing "WhatsApp")
  → route: /whatsapp-business
      ┌──────────────────────────┬───────────────────────────────┐
      │ WhatsAppConversationList │ WhatsAppBusinessChat           │
      │ (left, resizable panel)  │ (right, flex-1)                │
      │                          │                                 │
      │ 🔍 Search...             │ [header: name, phone,           │
      │ ───────────────────────  │  "View ticket #NNN →"]         │
      │ ● Rahul K.        04:26  │ [WhatsAppBubble messages,      │
      │   Good evening...        │  stitched across tickets]      │
      │ ───────────────────────  │ [WhatsAppReplyBox]             │
      │   Dhiren S.       10:22  │                                 │
      └──────────────────────────┴───────────────────────────────┘
```

Conversation identity is the **normalized phone number** (`_normalize_phone()`, already used throughout `wa.py`), not a JID and not a single ticket. A phone number's chat history spans every `WhatsApp Message` ever linked to any ticket for that number — visually one continuous thread even though the underlying ticket changes each time the previous one resolves or times out (`new_conversation_timeout_hours`, default 24h).

### Backend changes (`helpdesk/integrations/wa.py`)

**`get_whatsapp_conversations()`** (new, whitelisted) — one row per phone number, most recent message first:
```python
@frappe.whitelist()
def get_whatsapp_conversations() -> list[dict]:
    """One entry per phone number for the WABA standalone chat page, most-recent first."""
    if not frappe.db.exists("DocType", "WhatsApp Message"):
        return []
    WM = frappe.qb.DocType("WhatsApp Message")
    rows = (
        frappe.qb.from_(WM)
        .select(WM["from"], WM["to"], WM.type, WM.message, WM.content_type, WM.creation, WM.profile_name)
        .orderby(WM.creation, order=frappe.qb.desc)
        .run(as_dict=True)
    )
    latest: dict[str, dict] = {}
    for r in rows:
        phone = _normalize_phone(r["from"] if r["type"] == "Incoming" else r["to"])
        if not phone or phone in latest:
            continue
        latest[phone] = r

    # Contact display names, keyed by normalized phone — reuse the same
    # Contact.mobile_no/phone + Contact Phone child-table lookup that
    # match_phone_to_contact() already does for the ticket-embedded tab.
    result = []
    for phone, r in latest.items():
        contact_name = match_phone_to_contact(phone)
        display_name = (
            frappe.db.get_value("Contact", contact_name, "first_name")
            if contact_name else None
        ) or r.get("profile_name") or phone
        result.append({
            "phone": phone,
            "display_name": display_name,
            "last_message": r["message"] or f"[{r['content_type']}]",
            "last_message_time": str(r["creation"]),
            "last_direction": r["type"],
        })
    return result
```

**`get_whatsapp_messages()`** — add a `phone` parameter. When given, query all `WhatsApp Message` rows where `_normalize_phone(from) == phone OR _normalize_phone(to) == phone`, across every `reference_name`, ordered by creation. (Existing `ticket`/`jid` behavior unchanged — this is a third, additive path.)

**`get_active_whatsapp_ticket_for_phone(phone)`** (new, whitelisted) — resolves which ticket a reply should attach to. Reuses the exact "open ticket within timeout window" lookup already in `on_whatsapp_message_insert()` (wa.py:2312-2333) so replies from the new page land on the same ticket the ticket-embedded tab would use. If no open ticket exists (e.g. the last one resolved), returns the most recent ticket regardless of status — `_send_fw_reply()` doesn't require an open ticket, and reopening logic already exists via `agent_reply_status`.

No changes to `_send_fw_reply()`, `_send_fw_reaction()`, `WhatsAppBubble.vue`, or the ticket-embedded `WhatsAppChatTab.vue`.

### Frontend changes

New files, modeled directly on the WA Line equivalents:

| New file | Modeled on | Notes |
|---|---|---|
| `desk/src/pages/whatsapp/WhatsAppBusinessPage.vue` | `WhatsAppPage.vue` | Same two-column resizable shell, mobile back-button handling |
| `desk/src/components/whatsapp/WhatsAppConversationList.vue` | `BaileysConversationList.vue` | No "new chat" composer (WABA can't originate outside the 24h window); search filters by name/phone/last message |
| `desk/src/components/whatsapp/WhatsAppConversationItem.vue` | `BaileysConversationItem.vue` | Same avatar/name/preview/timestamp/unread-badge shape |
| `desk/src/components/whatsapp/WhatsAppBusinessChat.vue` | `BaileysChat.vue` | Resolves active ticket via `get_active_whatsapp_ticket_for_phone`, then reuses `WhatsAppBubble` for the message list and `WhatsAppReplyBox` (passing the resolved `ticketId`) for sending. Header shows a "View ticket #NNN →" link instead of inline ticket controls |

Modified files:
- `desk/src/router/index.ts` — add `/whatsapp-business` route
- `desk/src/components/layouts/layoutSettings.ts` — add sidebar entry
- Realtime: reuse existing `helpdesk:whatsapp-message` / `helpdesk:whatsapp-status-update` events (already fire on every WABA message; the new list/chat components subscribe to them the same way `BaileysConversationList`/`BaileysChat` subscribe to their `baileys-*` equivalents)

### Unread tracking

Same pattern as `BaileysConversationList`: `localStorage`-keyed last-read timestamp per phone number, no new backend field.

---

## Out of Scope

- Agent-initiated new conversations (no phone number picker/composer) — blocked by WABA's 24h customer-initiated window regardless of UI
- Message edit history / reactions parity with WA Line — `frappe_whatsapp` doesn't support these upstream; `get_whatsapp_messages()`'s frappe_whatsapp branch already returns `is_edited: 0, edit_history: []` unconditionally, unchanged here
- Changing how `WhatsAppChatTab.vue` (the ticket-embedded tab) works — stays exactly as-is
- Template sending (post-24h-window) from the new page — the existing template picker stays ticket-page-only for this pass; agents needing it click through via "View ticket #NNN"

## Testing / Verification

- `on_whatsapp_message_insert()`: create a new WABA ticket via webhook simulation, assert `ticket_channel == "WhatsApp"`
- Backfill patch: seed an HD Ticket with `ticket_channel = "Email"` linked to a `WhatsApp Message`, run the patch, assert it flips to `"WhatsApp"`; seed a genuine email ticket with no linked `WhatsApp Message`, assert the patch leaves it untouched
- `get_whatsapp_conversations()`: multiple tickets for the same phone (one resolved, one open) collapse into a single conversation row with the latest message
- `get_whatsapp_messages(phone=...)`: messages from two different tickets for the same phone appear interleaved by `creation`, not grouped by ticket
- `get_active_whatsapp_ticket_for_phone()`: returns the open ticket when one exists within the timeout window; falls back to the most recent ticket otherwise
- Manual: open `/whatsapp-business`, select a conversation with history spanning a resolved + a reopened ticket, send a reply, confirm it lands on the correct (active) ticket and appears in both the new page and that ticket's embedded WhatsApp tab
