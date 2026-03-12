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
- `helpdesk/integrations/whatsapp.py` — all WhatsApp API endpoints and doc_event hooks
- `helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/` — singleton settings DocType
- `helpdesk/hooks.py` — added `WhatsApp Message` doc_events pointing to the integration module

### Frontend
- `desk/src/components/whatsapp/WhatsAppChatTab.vue` — main tab component
- `desk/src/components/whatsapp/WhatsAppBubble.vue` — message bubble (outgoing = green, incoming = surface-white)
- `desk/src/components/whatsapp/WhatsAppReplyBox.vue` — reply input with file attach, paste, drag-drop
- `desk/src/components/icons/WhatsAppIcon.vue` — SVG icon
- `desk/src/components/ticket-agent/TicketActivityPanel.vue` — modified: adds WhatsApp tab
- `desk/src/components/ticket-agent/TicketContactTab.vue` — modified: shows company, designation, mobile
- `desk/src/components/notifications/Notifications.vue` — modified: WhatsApp notification rendering
- `desk/src/stores/notification.ts` — modified: bell reload + sound on incoming WhatsApp

### Key API paths (frontend → backend)
All WhatsApp APIs: `helpdesk.integrations.whatsapp.<function>`

### Keeping in sync with upstream
```bash
git fetch upstream
git merge upstream/develop
# resolve conflicts in the modified files above
bench build --app helpdesk
```

## Dark Mode

frappe-ui's preset already defines `[data-theme='dark']` CSS variable overrides for all semantic tokens (`--ink-gray-*`, `--surface-*`, `--outline-*`). Since the app uses semantic classes almost exclusively, **dark mode is ~90% free** — just needs:

1. A toggle that sets `document.documentElement.setAttribute("data-theme", "dark")` (and removes it for light)
2. Persistence via `localStorage`
3. Fix `WhatsAppBubble.vue`: `bg-green-100 text-green-700` → dark-aware equivalents

`EmailContent.vue` already reads `data-theme` from `document.documentElement` and propagates it to its iframe — the pattern is established.

## Frappe_whatsapp Dependency

The `WhatsApp Message` DocType, `send_read_receipt()`, and the webhook (status updates sent/delivered/read) all come from the `frappe_whatsapp` app. `integrations/whatsapp.py` guards against it not being installed.

## Realtime Events

| Event | Direction | Purpose |
|-------|-----------|---------|
| `helpdesk:whatsapp-message` | server → all users | New incoming/outgoing message |
| `helpdesk:whatsapp-status-update` | server → all users | Delivery status change (sent/delivered/read) |
| `helpdesk:comment-reaction-update` | server → all users | Bell reload |

Both WhatsApp events are broadcast to the `"all"` room (all logged-in System Users auto-join). Room-based routing was avoided because socket.io clients lose room membership on reconnect.

## Contact Phone Lookup

`match_phone_to_contact()` checks:
1. `Contact.mobile_no` / `Contact.phone` (normalized, digits-only comparison)
2. `Contact Phone` child table (fallback for multi-number contacts)

When creating contacts from WhatsApp, always use the `phone_nos` child table with `is_primary_mobile_no: 1` — setting `mobile_no` directly is overwritten by `Contact.validate()`.
