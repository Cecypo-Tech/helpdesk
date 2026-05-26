# CLAUDE.md — Helpdesk Fork

This is a **fork** of `frappe/helpdesk` with a WhatsApp integration added. See `AGENTS.md` for upstream coding conventions (semantic Tailwind tokens, Vue 3 Composition API, etc.) — follow those.

## Environment

- Bench root: `/home/frappeuser/bench16`
- Site: `site16.local`
- Web server: port 8002 · SocketIO: port 9002
- After Python changes: restart gunicorn (`pkill -f "frappe.app" && bench serve --port 8002 &`)
- After Vue/TS changes: `bench build --app helpdesk` then hard-refresh

## What This Fork Adds

### Backend
- `helpdesk/integrations/wa.py` — all WhatsApp API endpoints and doc_event hooks (both WABA and WA Line paths live here)
- `helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/` — singleton settings for the WABA path
- `helpdesk/hooks.py` — `WhatsApp Message` doc_events pointing to `wa.py` integration handlers

### Frontend
- `desk/src/components/whatsapp/WhatsAppChatTab.vue` — main tab component (handles both WABA and WA Line display)
- `desk/src/components/whatsapp/WhatsAppBubble.vue` — message bubble (outgoing = green, incoming = surface-white)
- `desk/src/components/whatsapp/WhatsAppReplyBox.vue` — reply input with file attach, paste, drag-drop
- `desk/src/components/icons/WhatsAppIcon.vue` — SVG icon
- `desk/src/components/ticket-agent/TicketActivityPanel.vue` — modified: adds WhatsApp tab
- `desk/src/components/ticket-agent/TicketContactTab.vue` — modified: shows company, designation, mobile
- `desk/src/components/notifications/Notifications.vue` — modified: WhatsApp notification rendering
- `desk/src/stores/notification.ts` — modified: bell reload + sound on incoming WhatsApp

### Key API paths (frontend → backend)
All WhatsApp APIs: `helpdesk.integrations.wa.<function>`

### Keeping in sync with upstream
```bash
git fetch upstream
git merge upstream/develop
# resolve conflicts in the modified files above
bench build --app helpdesk
```

---

## Two WhatsApp Integrations

This fork supports **two completely separate WhatsApp integrations** that share the same UI tab and backend module (`wa.py`). They are distinguished by whether an HD Ticket has the custom field `baileys_jid` set.

### 1. WABA — WhatsApp Business API via `frappe_whatsapp`

| Item | Detail |
|------|--------|
| **Provider** | Meta's official WhatsApp Business API |
| **App dependency** | `frappe_whatsapp` (must be installed) |
| **Message DocType** | `WhatsApp Message` (from `frappe_whatsapp`) |
| **Ticket link** | `WhatsApp Message.reference_name → HD Ticket` |
| **Routing key** | HD Ticket has **no** `baileys_jid` value |
| **Reply function** | `_send_fw_reply()` |
| **Media function** | `_send_fw_reply()` with `media_url` → sets `attach` field on `WhatsApp Message` |
| **24-hr window** | Enforced — after 24 h only templates can be sent |
| **Settings** | `WhatsApp Helpdesk Settings` singleton |
| **Realtime events** | `helpdesk:whatsapp-message`, `helpdesk:whatsapp-status-update` |

The `frappe_whatsapp` controller (`WhatsAppMessage.before_insert → send_outgoing`) handles the actual Meta API call. When sending media, `attach` **must** be set on the `WhatsApp Message` doc — if it is empty for a non-text `content_type`, Meta rejects the request.

### 2. WA Line — Evolution API / Baileys

| Item | Detail |
|------|--------|
| **Provider** | Self-hosted Evolution API (Baileys wrapper) |
| **App dependency** | None — uses `WA API Settings` + `WA Line` DocTypes in helpdesk |
| **Message DocType** | `WA Message` (custom DocType in this app) |
| **Ticket link** | `HD Ticket.baileys_jid` custom field + `WA Message.reference_name` |
| **Routing key** | HD Ticket has a `baileys_jid` value |
| **Reply function** | `send_wa_reply()` Baileys path |
| **Media function** | `send_wa_media()` → `send_wa_reply()` with `media_url` → `WA Message.media_url` |
| **24-hr window** | Not enforced (no window restriction) |
| **Settings** | `WA API Settings` singleton + `WA Line` per-instance docs |
| **Realtime events** | `helpdesk:baileys-message`, `helpdesk:baileys-status-update` |

Custom fields on `HD Ticket` (added via fixtures):
- `baileys_jid` — the WhatsApp JID (e.g. `2547XXXXXXXX@s.whatsapp.net`)
- `baileys_line` — the `WA Line` name that owns this conversation

**Important**: `baileys_jid` and `baileys_line` are custom fields. On sites that haven't run `bench migrate` after installing WA Line fixtures, filtering `HD Ticket` by `baileys_jid` raises an `OperationalError`. All such queries must be wrapped in `try/except`.

### Routing Logic in `send_wa_reply()`

```
send_wa_reply(ticket, jid=None, ...)
  ├── ticket + no jid → look up HD Ticket.baileys_jid
  │     ├── has baileys_jid → Baileys/WA Line path
  │     └── no baileys_jid  → _send_fw_reply() [WABA path]
  └── jid provided → Baileys/WA Line path directly
```

The same ticket view/tab renders both integrations — `get_whatsapp_ticket_info()` returns `via_frappe_whatsapp: True` for WABA tickets so the frontend can show the 24-hr window UI.

---

## Dark Mode

frappe-ui's preset already defines `[data-theme='dark']` CSS variable overrides for all semantic tokens (`--ink-gray-*`, `--surface-*`, `--outline-*`). Since the app uses semantic classes almost exclusively, **dark mode is ~90% free** — just needs:

1. A toggle that sets `document.documentElement.setAttribute("data-theme", "dark")` (and removes it for light)
2. Persistence via `localStorage`
3. Fix `WhatsAppBubble.vue`: `bg-green-100 text-green-700` → dark-aware equivalents

`EmailContent.vue` already reads `data-theme` from `document.documentElement` and propagates it to its iframe — the pattern is established.

## Realtime Events

| Event | Direction | Integration | Purpose |
|-------|-----------|-------------|---------|
| `helpdesk:whatsapp-message` | server → all | WABA | New incoming/outgoing frappe_whatsapp message |
| `helpdesk:whatsapp-status-update` | server → all | WABA | Delivery status change (sent/delivered/read) |
| `helpdesk:baileys-message` | server → all | WA Line | New incoming/outgoing Baileys message |
| `helpdesk:baileys-status-update` | server → all | WA Line | WA Line delivery status change |
| `helpdesk:whatsapp-message-edit` | server → all | WA Line | Message text edited |
| `helpdesk:comment-reaction-update` | server → all | both | Bell reload |

All events are broadcast to the `"all"` room. Room-based routing was avoided because socket.io clients lose room membership on reconnect.

## Contact Phone Lookup

`match_phone_to_contact()` checks:
1. `Contact.mobile_no` / `Contact.phone` (normalized, digits-only comparison)
2. `Contact Phone` child table (fallback for multi-number contacts)

When creating contacts from WhatsApp, always use the `phone_nos` child table with `is_primary_mobile_no: 1` — setting `mobile_no` directly is overwritten by `Contact.validate()`.
