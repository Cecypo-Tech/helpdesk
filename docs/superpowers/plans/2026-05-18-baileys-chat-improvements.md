# Baileys Chat Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the Baileys WhatsApp chat panel inside the helpdesk ticket view — fixing the double-tab UX, adding working reply-to and reactions, showing the connected phone number in the chat header, adding a Baileys settings page to the helpdesk Settings modal, and making the `^XX` agent initials suffix configurable.

**Architecture:** Changes span four layers — the Node.js Baileys gateway (new `/react` endpoint, quoted-reply support in `/send`, phone in `/health`, reaction forwarding in webhook), the Frappe Python backend (new APIs, two new DocType fields), the Vue ticket panel (tab logic fix, chat tab UI upgrade, reply box upgrade), and the helpdesk Settings modal (new Baileys Gateway page). No ticket creation or routing logic is touched.

**Tech Stack:** Node.js + @whiskeysockets/baileys (gateway), Python/Frappe (backend), Vue 3 Composition API + frappe-ui (frontend)

---

## File Map

| File | Change |
|------|--------|
| `helpdesk/integrations/baileys_gateway/src/index.js` | Expose phone in `/health`; add `/react`; quoted support in `/send`; forward reactions + quoted context in webhook |
| `helpdesk/helpdesk/doctype/baileys_message/baileys_message.json` | Add `reply_to_message_id` Data field; add `reaction` to `content_type` options |
| `helpdesk/helpdesk/doctype/baileys_gateway_settings/baileys_gateway_settings.json` | Add `append_agent_initials` Check field (default 1) |
| `helpdesk/integrations/baileys.py` | `send_baileys_reaction()`, `get_connected_phone()`, initials toggle, `reply_to_message_id` in send/webhook/get_messages |
| `desk/src/components/ticket-agent/TicketActivityPanel.vue` | Make `hasWhatsApp` computed (hide for Baileys tickets); rename Baileys tab to "WhatsApp" |
| `desk/src/components/whatsapp/BaileysGroupChatTab.vue` | Phone header; reactions + reply-to wiring; filter reaction records from display list |
| `desk/src/components/whatsapp/BaileysReplyBox.vue` | `replyTo` prop, quoted preview, `clearReply` emit, pass reply params to backend |
| `desk/src/components/Settings/BaileysGateway/BaileysGatewaySettings.vue` | **New** — Baileys settings page for the Settings modal |
| `desk/src/components/Settings/settingsModal.ts` | Import and register `BaileysGatewaySettings` in Integrations section |

---

## Task 1: Gateway — phone in /health, /react endpoint, quoted in /send, reactions in webhook

**Files:**
- Modify: `helpdesk/integrations/baileys_gateway/src/index.js`

- [ ] **Step 1: Expose connected phone in /health**

Replace:
```javascript
app.get("/health", (_, res) => res.json({ connected: isConnected, hasQr: !!qrString, session: SESSION_NAME }));
```
With:
```javascript
app.get("/health", (_, res) => {
  const rawId = sock?.user?.id || "";
  const phone = rawId ? rawId.split(":")[0].split("@")[0] : null;
  res.json({ connected: isConnected, hasQr: !!qrString, session: SESSION_NAME, phone });
});
```

- [ ] **Step 2: Add POST /react endpoint**

Insert after the `/markRead` handler (line ~122) and before `/groups`:
```javascript
app.post("/react", auth, async (req, res) => {
  if (!isConnected) return res.status(503).json({ error: "Not connected" });
  const { jid, messageId, emoji, fromMe } = req.body;
  if (!jid || !messageId) return res.status(400).json({ error: "jid and messageId required" });
  try {
    await sock.sendMessage(jid, {
      react: { text: emoji ?? "", key: { remoteJid: jid, id: messageId, fromMe: fromMe ?? false } },
    });
    res.json({ ok: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});
```

- [ ] **Step 3: Add quoted-reply support to POST /send**

Replace the entire `/send` handler with:
```javascript
app.post("/send", auth, async (req, res) => {
  if (!isConnected) return res.status(503).json({ error: "Not connected" });
  const { jid, message, mediaUrl, contentType = "text", replyToMessageId, replyToText, replyToFromMe } = req.body;
  if (!jid || (!message && !mediaUrl)) return res.status(400).json({ error: "jid and message/mediaUrl required" });
  try {
    let content;
    if (!mediaUrl || contentType === "text") {
      content = { text: message };
    } else if (contentType === "image") {
      content = { image: { url: mediaUrl }, caption: message || "" };
    } else if (contentType === "video") {
      content = { video: { url: mediaUrl }, caption: message || "" };
    } else if (contentType === "audio") {
      content = { audio: { url: mediaUrl }, mimetype: "audio/mp4" };
    } else {
      content = { document: { url: mediaUrl }, fileName: message || "file" };
    }
    const options = {};
    if (replyToMessageId) {
      options.quoted = {
        key: { remoteJid: jid, id: replyToMessageId, fromMe: replyToFromMe ?? false },
        message: { conversation: replyToText || "" },
      };
    }
    const sent = await sock.sendMessage(jid, content, options);
    res.json({ messageId: sent.key.id });
  } catch (err) { res.status(500).json({ error: err.message }); }
});
```

- [ ] **Step 4: Forward reactions and quoted context in messages.upsert**

Replace the `sock.ev.on("messages.upsert", ...)` handler with:
```javascript
sock.ev.on("messages.upsert", async ({ messages, type }) => {
  if (type !== "notify" || !WEBHOOK_URL) return;
  for (const msg of messages) {
    if (msg.key.fromMe || !msg.message) continue;
    const jid        = msg.key.remoteJid;
    const sender     = msg.key.participant || jid;
    const senderName = msg.pushName || sender.split("@")[0];
    const mc         = msg.message;
    let text = "", contentType = "text", quotedMessageId = "";

    if (mc.conversation) {
      text = mc.conversation;
    } else if (mc.extendedTextMessage) {
      text = mc.extendedTextMessage.text;
      quotedMessageId = mc.extendedTextMessage.contextInfo?.stanzaId || "";
    } else if (mc.imageMessage) {
      contentType = "image"; text = mc.imageMessage.caption || "";
      quotedMessageId = mc.imageMessage.contextInfo?.stanzaId || "";
    } else if (mc.videoMessage) {
      contentType = "video"; text = mc.videoMessage.caption || "";
    } else if (mc.audioMessage) {
      contentType = "audio";
    } else if (mc.documentMessage) {
      contentType = "document"; text = mc.documentMessage.caption || "";
    } else if (mc.reactionMessage) {
      contentType = "reaction";
      text = mc.reactionMessage.text || "";
      quotedMessageId = mc.reactionMessage.key?.id || "";
    } else {
      continue;
    }

    try {
      await axios.post(WEBHOOK_URL, {
        jid, messageId: msg.key.id, sender, senderName,
        message: text, contentType, timestamp: msg.messageTimestamp,
        quotedMessageId,
      }, { headers: { "X-API-Key": API_KEY }, timeout: 15000 });
    } catch (err) { logger.error({ err: err.message, jid }, "Webhook failed"); }
  }
});
```

- [ ] **Step 5: Restart gateway and verify /health returns phone**

```bash
# If running locally for dev, restart the process. Check the response:
curl -s -H "X-API-Key: <your-api-key>" http://localhost:3001/health
```
Expected: `{"connected":true,"hasQr":false,"session":"helpdesk","phone":"254712345678"}` — or `"phone":null` if not yet connected.

- [ ] **Step 6: Commit gateway changes**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/integrations/baileys_gateway/src/index.js
git commit -m "feat(gateway): expose phone in /health, add /react, support quoted replies, forward reactions in webhook"
```

---

## Task 2: BaileysMessage DocType — add reply_to_message_id + reaction content type

**Files:**
- Modify: `helpdesk/helpdesk/doctype/baileys_message/baileys_message.json`

- [ ] **Step 1: Add reply_to_message_id to field_order**

In `"field_order"`, add `"reply_to_message_id"` directly after `"message_id"`:
```json
"field_order": [
  "direction",
  "jid",
  "col_break_jid",
  "sender_jid",
  "sender_name",
  "col_break_ref",
  "reference_doctype",
  "reference_name",
  "section_message",
  "message",
  "content_type",
  "col_break_media",
  "media_url",
  "message_id",
  "reply_to_message_id",
  "section_meta",
  "status",
  "profile_name"
]
```

- [ ] **Step 2: Add the field object after the message_id field**

In `"fields"`, after the `message_id` field object `{...,"fieldname": "message_id",...}`, insert:
```json
{
  "fieldname": "reply_to_message_id",
  "fieldtype": "Data",
  "label": "Reply To Message ID",
  "read_only": 1
},
```

- [ ] **Step 3: Add reaction to content_type options**

Find the `content_type` field object and update `"options"`:
```json
{
  "fieldname": "content_type",
  "fieldtype": "Select",
  "label": "Content Type",
  "options": "text\nimage\nvideo\naudio\ndocument\nreaction"
}
```

- [ ] **Step 4: Run migration**

```bash
cd /home/frappeuser/bench16
bench --site site16.local migrate
```
Expected: Completes without error. `tabBaileys Message` now has a `reply_to_message_id` column.

- [ ] **Step 5: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/helpdesk/doctype/baileys_message/baileys_message.json
git commit -m "feat(doctype): add reply_to_message_id field and reaction content type to Baileys Message"
```

---

## Task 3: BaileysGatewaySettings DocType — add append_agent_initials field

**Files:**
- Modify: `helpdesk/helpdesk/doctype/baileys_gateway_settings/baileys_gateway_settings.json`

- [ ] **Step 1: Add append_agent_initials to field_order**

In `"field_order"`, add `"append_agent_initials"` after `"agent_reply_status"`:
```json
"field_order": [
  "section_connection", "enabled", "gateway_url", "col_break_conn", "api_key", "session_name",
  "section_ticket_defaults", "default_ticket_type", "col_break_defaults", "default_team",
  "col_break_email", "placeholder_email_domain",
  "section_dm_behaviour", "unknown_contact_action", "col_break_dm", "new_conversation_timeout_hours",
  "section_status_automation", "customer_reply_status", "col_break_status", "agent_reply_status",
  "append_agent_initials",
  "section_notifications", "notification_quiet_minutes",
  "section_groups", "group_jids",
  "section_blocklist", "blocked_jids"
]
```

- [ ] **Step 2: Add the field object after agent_reply_status**

In `"fields"`, after the `agent_reply_status` field object, insert:
```json
{
  "default": "1",
  "fieldname": "append_agent_initials",
  "fieldtype": "Check",
  "label": "Append Agent Initials to Outgoing Messages",
  "description": "When enabled, appends ^XX (agent initials) to every outgoing Baileys message."
},
```

- [ ] **Step 3: Run migration**

```bash
cd /home/frappeuser/bench16
bench --site site16.local migrate
```
Expected: Completes cleanly.

- [ ] **Step 4: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/helpdesk/doctype/baileys_gateway_settings/baileys_gateway_settings.json
git commit -m "feat(doctype): add append_agent_initials toggle to Baileys Gateway Settings"
```

---

## Task 4: Backend Python — initials toggle, reaction API, phone API, webhook + get_messages

**Files:**
- Modify: `helpdesk/integrations/baileys.py`
- Create: `helpdesk/tests/test_baileys.py`

- [ ] **Step 1: Write failing tests**

Create `helpdesk/tests/__init__.py` (empty) if it does not exist, then create `helpdesk/tests/test_baileys.py`:

```python
import frappe
import unittest
from unittest.mock import patch, MagicMock


class TestBaileysInitials(unittest.TestCase):

    def _get_baileys_ticket(self):
        name = frappe.db.get_value("HD Ticket", {"baileys_jid": ["!=", ""]}, "name")
        if not name:
            self.skipTest("No Baileys ticket in DB")
        return name

    def _mock_requests_post(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"messageId": "test-id"}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

    def test_initials_appended_when_enabled(self):
        """send_baileys_reply must append ^XX when append_agent_initials=1."""
        from helpdesk.integrations import baileys as b

        frappe.db.set_value("Baileys Gateway Settings", None, "append_agent_initials", 1)
        frappe.clear_cache()

        ticket = self._get_baileys_ticket()
        with patch("helpdesk.integrations.baileys._requests") as mock_req:
            self._mock_requests_post(mock_req.post)
            b.send_baileys_reply(ticket=ticket, message="hello")
            payload = mock_req.post.call_args.kwargs.get("json") or mock_req.post.call_args.args[1]
        self.assertIn("^", payload["message"])

    def test_initials_omitted_when_disabled(self):
        """send_baileys_reply must NOT append ^XX when append_agent_initials=0."""
        from helpdesk.integrations import baileys as b

        frappe.db.set_value("Baileys Gateway Settings", None, "append_agent_initials", 0)
        frappe.clear_cache()

        ticket = self._get_baileys_ticket()
        with patch("helpdesk.integrations.baileys._requests") as mock_req:
            self._mock_requests_post(mock_req.post)
            b.send_baileys_reply(ticket=ticket, message="hello")
            payload = mock_req.post.call_args.kwargs.get("json") or mock_req.post.call_args.args[1]

        # Restore
        frappe.db.set_value("Baileys Gateway Settings", None, "append_agent_initials", 1)
        frappe.clear_cache()
        self.assertNotIn("^", payload["message"])
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk --module helpdesk.tests.test_baileys
```
Expected: `ImportError` or assertion errors — confirms the tests are driving implementation.

- [ ] **Step 3: Apply initials toggle in send_baileys_reply**

In `send_baileys_reply`, replace:
```python
agent_suffix = f"\n^{_agent_initials()}"
full_message = f"{message}{agent_suffix}" if message else agent_suffix.strip()
```
With:
```python
if settings.append_agent_initials:
    agent_suffix = f"\n^{_agent_initials()}"
    full_message = f"{message}{agent_suffix}" if message else agent_suffix.strip()
else:
    full_message = message or ""
```

Note: `settings = _settings()` is already called at the top of `send_baileys_reply` — move the `settings` call to just before the initials check if it's not already there.

- [ ] **Step 4: Extend send_baileys_reply signature for reply-to params**

Change the function signature from:
```python
def send_baileys_reply(
    ticket: str,
    message: str,
    content_type: str = "text",
    media_url: str | None = None,
    reply_to_message_id: str | None = None,
) -> dict:
```
To:
```python
def send_baileys_reply(
    ticket: str,
    message: str,
    content_type: str = "text",
    media_url: str | None = None,
    reply_to_message_id: str | None = None,
    reply_to_text: str | None = None,
    reply_to_from_me: bool = False,
) -> dict:
```

In the `payload` dict construction, after the existing `if media_url:` block, add:
```python
if reply_to_message_id:
    payload["replyToMessageId"] = reply_to_message_id
    payload["replyToText"] = reply_to_text or ""
    payload["replyToFromMe"] = bool(reply_to_from_me)
```

In the `msg_doc = frappe.get_doc({...})` call, add:
```python
"reply_to_message_id": reply_to_message_id or "",
```

- [ ] **Step 5: Add send_baileys_reaction function**

After the `send_baileys_media` function, add:

```python
@frappe.whitelist()
def send_baileys_reaction(ticket: str, target_message_id: str, emoji: str) -> dict:
    """Send an emoji reaction to a specific Baileys message."""
    settings = _settings()
    if not settings.enabled:
        frappe.throw(_("Baileys gateway is not enabled."))

    jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
    if not jid:
        frappe.throw(_("This ticket is not linked to a Baileys chat."))

    gateway_url = (settings.gateway_url or "").rstrip("/")
    api_key = settings.api_key or ""

    try:
        resp = _requests.post(
            f"{gateway_url}/react",
            json={
                "sessionName": settings.session_name or "helpdesk",
                "jid": jid,
                "messageId": target_message_id,
                "emoji": emoji,
                "fromMe": False,
            },
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        frappe.throw(_("Baileys reaction failed: {0}").format(str(e)))

    frappe.get_doc({
        "doctype": "Baileys Message",
        "direction": "Outgoing",
        "jid": jid,
        "sender_jid": "",
        "sender_name": frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user,
        "profile_name": "",
        "message": emoji,
        "content_type": "reaction",
        "media_url": "",
        "message_id": "",
        "reply_to_message_id": target_message_id,
        "status": "Sent",
        "reference_doctype": "HD Ticket",
        "reference_name": ticket,
    }).insert(ignore_permissions=True)

    _publish_event(ticket, is_incoming=False)
    return {"ok": True}
```

- [ ] **Step 6: Add get_connected_phone function**

After `fetch_gateway_groups`, add:

```python
@frappe.whitelist()
def get_connected_phone() -> dict:
    """Return phone number of the connected WhatsApp account from the gateway /health endpoint."""
    settings = _settings()
    if not settings.enabled or not settings.gateway_url:
        return {"phone": None, "connected": False}
    try:
        resp = _requests.get(
            f"{settings.gateway_url.rstrip('/')}/health",
            headers={"X-API-Key": settings.api_key or ""},
            timeout=5,
        )
        data = resp.json()
        return {"phone": data.get("phone"), "connected": data.get("connected", False)}
    except Exception:
        return {"phone": None, "connected": False}
```

- [ ] **Step 7: Store reply_to_message_id in webhook**

In `webhook()`, after:
```python
media_url = payload.get("mediaUrl") or ""
```
Add:
```python
quoted_message_id = payload.get("quotedMessageId") or ""
```

In the `frappe.get_doc({...})` call that inserts the BaileysMessage, add:
```python
"reply_to_message_id": quoted_message_id,
```

- [ ] **Step 8: Fix get_baileys_messages to return real reply/reaction data**

In `get_baileys_messages()`, add `BM.reply_to_message_id` to the `.select(...)` call:
```python
.select(
    BM.name, BM.creation, BM.direction, BM.jid, BM.message,
    BM.content_type, BM.media_url, BM.sender_jid, BM.sender_name,
    BM.profile_name, BM.message_id, BM.reply_to_message_id, BM.status, BM.owner,
    User.full_name.as_("sender_full_name"),
)
```

In the post-processing loop, replace the two hardcoded lines:
```python
# Remove these:
m["is_reply"] = 0
m["reply_to_message_id"] = ""
```
With:
```python
m["is_reply"] = 1 if (m.get("reply_to_message_id") and m.get("content_type") != "reaction") else 0
```

- [ ] **Step 9: Run tests — expect pass**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk --module helpdesk.tests.test_baileys
```
Expected: Both tests PASS.

- [ ] **Step 10: Restart and commit**

```bash
cd /home/frappeuser/bench16
bench restart
```
```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/integrations/baileys.py helpdesk/tests/__init__.py helpdesk/tests/test_baileys.py
git commit -m "feat(backend): reaction API, phone API, initials toggle, reply_to in send/webhook/get_messages"
```

---

## Task 5: TicketActivityPanel.vue — fix tab logic

**Files:**
- Modify: `desk/src/components/ticket-agent/TicketActivityPanel.vue:99-145`

- [ ] **Step 1: Make hasWhatsApp computed and rename Baileys tab**

Find and replace these two lines:
```typescript
const hasWhatsApp = true;
const hasBaileys = computed(() => Boolean(ticket.value?.doc?.baileys_jid));
```
With:
```typescript
const hasBaileys = computed(() => Boolean(ticket.value?.doc?.baileys_jid));
const hasWhatsApp = computed(() => !hasBaileys.value);
```

Find the WhatsApp tab push:
```typescript
if (hasWhatsApp) {
  _tabs.push({
    name: "whatsapp",
    label: "WhatsApp",
    icon: WhatsAppIcon,
  });
}
```
Change to:
```typescript
if (hasWhatsApp.value) {
  _tabs.push({
    name: "whatsapp",
    label: "WhatsApp",
    icon: WhatsAppIcon,
  });
}
```

Find the Baileys tab push:
```typescript
if (hasBaileys.value) {
  _tabs.push({
    name: "baileys",
    label: "Group Chat",
    icon: WhatsAppIcon,
  });
}
```
Change to:
```typescript
if (hasBaileys.value) {
  _tabs.push({
    name: "baileys",
    label: "WhatsApp",
    icon: WhatsAppIcon,
  });
}
```

- [ ] **Step 2: Build**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk
```
Expected: Build completes without TypeScript errors.

- [ ] **Step 3: Verify in browser**

Open a Baileys ticket (has `baileys_jid`): confirm exactly ONE "WhatsApp" tab, no "Group Chat".
Open a non-Baileys ticket (no `baileys_jid`): confirm the frappe_whatsapp "WhatsApp" tab still appears.

- [ ] **Step 4: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/components/ticket-agent/TicketActivityPanel.vue
git commit -m "feat(ui): hide frappe_whatsapp tab for Baileys tickets, rename Baileys tab to WhatsApp"
```

---

## Task 6: BaileysGroupChatTab.vue — phone header, reactions + reply-to wiring

**Files:**
- Modify: `desk/src/components/whatsapp/BaileysGroupChatTab.vue`

- [ ] **Step 1: Replace the script block**

Replace the entire `<script setup lang="ts">` block with:

```typescript
<script setup lang="ts">
import { createResource, LoadingIndicator, toast } from "frappe-ui";
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { globalStore } from "@/stores/globalStore";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";
import WhatsAppBubble from "./WhatsAppBubble.vue";
import BaileysReplyBox from "./BaileysReplyBox.vue";

const props = defineProps<{
  ticketId: string;
}>();

const { $socket } = globalStore();
const messagesContainer = ref<HTMLElement | null>(null);
const pickingUp = ref(false);
const replyingTo = ref<Record<string, any> | null>(null);

const messages = createResource({
  url: "helpdesk.integrations.baileys.get_baileys_messages",
  params: { ticket: props.ticketId },
  auto: true,
});

const tabInfo = createResource({
  url: "helpdesk.integrations.baileys.get_ticket_baileys_info",
  params: { ticket: props.ticketId },
  auto: true,
});

const connectedPhone = createResource({
  url: "helpdesk.integrations.baileys.get_connected_phone",
  auto: true,
});

const markReadResource = createResource({
  url: "helpdesk.integrations.baileys.mark_baileys_messages_read",
});

const pickUpResource = createResource({
  url: "helpdesk.integrations.baileys.pickup_baileys_ticket",
  onSuccess() {
    pickingUp.value = false;
    tabInfo.reload();
    toast.success("Assigned to you");
  },
  onError(e: any) {
    pickingUp.value = false;
    toast.error(e?.messages?.[0] || "Could not assign ticket");
  },
});

const sendReactionResource = createResource({
  url: "helpdesk.integrations.baileys.send_baileys_reaction",
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send reaction");
  },
});

const allMessages = computed<Record<string, any>[]>(() => messages.data || []);

const messageList = computed(() =>
  allMessages.value.filter((m) => m.content_type !== "reaction")
);

const messageByMsgId = computed(() => {
  const map: Record<string, Record<string, any>> = {};
  for (const m of allMessages.value) {
    if (m.message_id) map[m.message_id] = m;
  }
  return map;
});

const reactionsMap = computed(() => {
  const map: Record<string, Array<{ emoji: string; type: string }>> = {};
  for (const m of allMessages.value) {
    if (m.content_type === "reaction" && m.reply_to_message_id && m.message) {
      if (!map[m.reply_to_message_id]) map[m.reply_to_message_id] = [];
      map[m.reply_to_message_id].push({ emoji: m.message, type: m.type });
    }
  }
  return map;
});

const groupedMessages = computed(() => {
  const groups: Record<string, any[]> = {};
  for (const msg of messageList.value) {
    const date = new Date(msg.creation).toLocaleDateString(undefined, {
      year: "numeric", month: "short", day: "numeric",
    });
    if (!groups[date]) groups[date] = [];
    groups[date].push(msg);
  }
  return groups;
});

const phoneDisplay = computed(() => {
  const phone = connectedPhone.data?.phone;
  return phone ? `+${phone}` : null;
});

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value)
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
  });
}

function scrollToMessage(messageId: string) {
  if (!messageId || !messagesContainer.value) return;
  const el = messagesContainer.value.querySelector(`[data-msg-id="${messageId}"]`) as HTMLElement | null;
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.style.transition = "background 0.2s";
  el.style.background = "rgba(99,178,115,0.25)";
  setTimeout(() => { el.style.background = ""; }, 1200);
}

function pickUp() {
  pickingUp.value = true;
  pickUpResource.submit({ ticket: props.ticketId });
}

function startReply(message: Record<string, any>) {
  replyingTo.value = message;
}

function sendReaction(emoji: string, targetMessageId: string) {
  if (!targetMessageId) {
    toast.error("Cannot react: message has no WhatsApp ID yet");
    return;
  }
  sendReactionResource.submit({ ticket: props.ticketId, target_message_id: targetMessageId, emoji });
}

function onMessageSent() {
  replyingTo.value = null;
  messages.reload();
  tabInfo.reload();
  scrollToBottom();
}

function markAsRead() {
  markReadResource.submit({ ticket: props.ticketId });
}

function handleRealtimeMessage(data: { ticket: string; is_incoming: boolean }) {
  if (String(data.ticket) === String(props.ticketId)) {
    messages.reload();
    tabInfo.reload();
    if (data.is_incoming) scrollToBottom();
    markAsRead();
  }
}

watch(messageList, () => { scrollToBottom(); });

onMounted(() => {
  $socket.on("helpdesk:whatsapp-message", handleRealtimeMessage);
  scrollToBottom();
  markAsRead();
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:whatsapp-message", handleRealtimeMessage);
});
</script>
```

- [ ] **Step 2: Replace the template block**

Replace the entire `<template>` block with:

```html
<template>
  <div class="flex flex-1 flex-col overflow-hidden">
    <!-- Chat header: connected phone + optional group name -->
    <div
      v-if="phoneDisplay"
      class="flex items-center gap-2 border-b border-outline-gray-2 bg-surface-gray-1 px-4 py-2"
    >
      <WhatsAppIcon class="h-4 w-4 text-green-600" />
      <span class="text-xs text-ink-gray-6">
        Connected:
        <span class="font-medium text-ink-gray-8">{{ phoneDisplay }}</span>
      </span>
      <span
        v-if="tabInfo.data?.is_group && tabInfo.data?.group_name"
        class="ml-auto text-[11px] text-ink-gray-5"
      >
        {{ tabInfo.data.group_name }}
      </span>
    </div>

    <!-- Messages area -->
    <div ref="messagesContainer" class="flex-1 overflow-y-auto px-5 py-4">
      <div v-if="messages.loading && !messages.data" class="flex justify-center py-10">
        <LoadingIndicator :scale="6" class="text-ink-gray-5" />
      </div>

      <div
        v-else-if="!messageList.length"
        class="flex flex-col items-center justify-center py-16 text-ink-gray-5"
      >
        <WhatsAppIcon class="mb-3 h-10 w-10 text-ink-gray-4" />
        <p class="text-sm">No messages yet</p>
      </div>

      <div v-else class="space-y-3">
        <template v-for="(group, dateKey) in groupedMessages" :key="dateKey">
          <div class="my-4 flex items-center gap-3">
            <div class="flex-1 border-t border-outline-gray-2" />
            <span class="text-[11px] font-medium text-ink-gray-5">{{ dateKey }}</span>
            <div class="flex-1 border-t border-outline-gray-2" />
          </div>
          <WhatsAppBubble
            v-for="msg in group"
            :key="msg.name"
            :data-msg-id="msg.message_id"
            :message="msg"
            :reactions="reactionsMap[msg.message_id] || []"
            :replyToMessage="msg.is_reply && msg.reply_to_message_id ? messageByMsgId[msg.reply_to_message_id] || null : null"
            @reply="startReply"
            @react="sendReaction"
            @scrollToReply="scrollToMessage"
          />
        </template>
      </div>
    </div>

    <!-- Bottom area -->
    <div v-if="tabInfo.data?.has_baileys">
      <div
        v-if="!tabInfo.data.is_assigned"
        class="flex items-center justify-between border-t border-outline-gray-2 bg-surface-gray-1 px-4 py-2"
      >
        <span class="text-xs text-ink-gray-5">Not assigned to you</span>
        <button
          :disabled="pickingUp"
          class="text-xs font-medium text-blue-600 hover:underline disabled:opacity-50"
          @click="pickUp"
        >
          {{ pickingUp ? "Assigning..." : "Assign to me" }}
        </button>
      </div>
      <BaileysReplyBox
        :ticketId="ticketId"
        :replyTo="replyingTo"
        @sent="onMessageSent"
        @clearReply="replyingTo = null"
      />
    </div>

    <div
      v-else-if="tabInfo.fetched && tabInfo.data && !tabInfo.data.has_baileys"
      class="border-t border-outline-gray-2 px-4 py-3 text-center text-xs text-ink-gray-5"
    >
      This ticket is not linked to a Baileys chat.
    </div>
  </div>
</template>
```

- [ ] **Step 3: Build and verify**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk
```

Open a Baileys ticket. Confirm:
- Phone header appears (e.g. "Connected: +254712345678")
- Hovering a message bubble exposes the reaction picker (WhatsAppBubble handles this)
- Clicking reply on a bubble populates the reply preview in the reply box

- [ ] **Step 4: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/components/whatsapp/BaileysGroupChatTab.vue
git commit -m "feat(ui): phone header, reactions and reply-to wiring in Baileys chat tab"
```

---

## Task 7: BaileysReplyBox.vue — reply-to preview + clearReply

**Files:**
- Modify: `desk/src/components/whatsapp/BaileysReplyBox.vue`

- [ ] **Step 1: Add replyTo prop and clearReply emit**

Replace:
```typescript
const props = defineProps<{
  ticketId: string;
}>();

const emit = defineEmits<{
  (e: "sent"): void;
}>();
```
With:
```typescript
const props = defineProps<{
  ticketId: string;
  replyTo?: Record<string, any> | null;
}>();

const emit = defineEmits<{
  (e: "sent"): void;
  (e: "clearReply"): void;
}>();
```

- [ ] **Step 2: Pass reply params in the text-only send branch**

In the `send()` function, replace:
```typescript
sendReply.submit({ ticket: props.ticketId, message: msgText, content_type: "text" });
```
With:
```typescript
sendReply.submit({
  ticket: props.ticketId,
  message: msgText,
  content_type: "text",
  reply_to_message_id: props.replyTo?.message_id || "",
  reply_to_text: props.replyTo?.message || "",
  reply_to_from_me: props.replyTo?.direction === "Outgoing",
});
```

- [ ] **Step 3: Add reply-to preview in template**

Inside `<template>`, immediately before `<div class="flex items-end gap-2">`, insert:

```html
<!-- Reply-to preview -->
<div
  v-if="replyTo"
  class="mb-2 flex items-start gap-2 rounded-lg border-l-4 border-green-500 bg-surface-gray-1 px-3 py-2"
>
  <div class="min-w-0 flex-1">
    <p class="text-[11px] font-medium text-green-600">
      {{ replyTo.direction === "Outgoing" ? "You" : (replyTo.sender_name || "Customer") }}
    </p>
    <p class="truncate text-xs text-ink-gray-6">{{ replyTo.message || "(media)" }}</p>
  </div>
  <button
    class="shrink-0 text-ink-gray-4 hover:text-ink-gray-7"
    @click="emit('clearReply')"
    title="Cancel reply"
  >
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
      <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    </svg>
  </button>
</div>
```

- [ ] **Step 4: Build and verify**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk
```

In a Baileys ticket:
- Click reply on a message → green quoted preview appears above the textarea
- Click X on the preview → preview clears
- Send a reply → the WhatsApp customer receives it as a quoted reply

- [ ] **Step 5: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/components/whatsapp/BaileysReplyBox.vue
git commit -m "feat(ui): reply-to preview and clearReply in BaileysReplyBox"
```

---

## Task 8: BaileysGatewaySettings.vue + settingsModal.ts

**Files:**
- Create: `desk/src/components/Settings/BaileysGateway/BaileysGatewaySettings.vue`
- Modify: `desk/src/components/Settings/settingsModal.ts`

- [ ] **Step 1: Create BaileysGatewaySettings.vue**

```bash
mkdir -p /home/frappeuser/bench16/apps/helpdesk/desk/src/components/Settings/BaileysGateway
```

Create `desk/src/components/Settings/BaileysGateway/BaileysGatewaySettings.vue`:

```html
<template>
  <SettingsLayoutBase :description="__('Configure the Baileys WhatsApp gateway.')">
    <template #title>
      <div class="flex items-center gap-2">
        <h1 class="text-lg font-semibold text-ink-gray-8">{{ __("Baileys Gateway") }}</h1>
        <Badge v-if="isDirty" :label="__('Unsaved')" theme="orange" variant="subtle" />
      </div>
    </template>
    <template #header-actions>
      <Button
        :label="__('Save')"
        variant="solid"
        @click="save"
        :disabled="!isDirty"
        :loading="settings.save.loading"
      />
    </template>
    <template #content>
      <div v-if="settings.get.loading && !settings.doc" class="flex justify-center mt-12">
        <LoadingIndicator class="w-4" />
      </div>
      <div v-else-if="settings.doc" class="space-y-8">

        <!-- Connection -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">{{ __("Connection") }}</div>
          <div class="mt-4 flex items-center justify-between">
            <div class="flex flex-col gap-1">
              <span class="text-sm font-medium text-ink-gray-8">{{ __("Enabled") }}</span>
              <span class="text-p-sm text-ink-gray-6">{{ __("Activate the Baileys WhatsApp gateway.") }}</span>
            </div>
            <Switch
              :modelValue="Boolean(settings.doc.enabled)"
              @update:modelValue="settings.doc.enabled = $event ? 1 : 0"
            />
          </div>
          <div class="mt-4 grid grid-cols-2 gap-4">
            <FormControl
              type="text"
              :label="__('Gateway URL')"
              v-model="settings.doc.gateway_url"
              :description="__('e.g. http://localhost:3001')"
            />
            <FormControl
              type="password"
              :label="__('API Key')"
              v-model="settings.doc.api_key"
              :description="__('Shared secret (X-API-Key header).')"
            />
          </div>
          <div class="mt-4 max-w-xs">
            <FormControl type="text" :label="__('Session Name')" v-model="settings.doc.session_name" />
          </div>
        </div>

        <hr />

        <!-- Connection Status -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">{{ __("Connection Status") }}</div>
          <div class="mt-3 flex items-center gap-3">
            <Button :label="__('Check Status')" @click="checkStatus" :loading="statusResource.loading" />
            <div v-if="statusResult" class="flex items-center gap-2">
              <span
                class="h-2 w-2 rounded-full"
                :class="statusResult.connected ? 'bg-green-500' : 'bg-red-400'"
              />
              <span class="text-sm text-ink-gray-7">
                {{ statusResult.connected ? __("Connected") : __("Disconnected") }}
                <span v-if="statusResult.connected && statusResult.phone" class="ml-1 text-ink-gray-5">
                  (+{{ statusResult.phone }})
                </span>
              </span>
            </div>
          </div>
        </div>

        <hr />

        <!-- Chat Behaviour -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">{{ __("Chat Behaviour") }}</div>
          <div class="mt-4 flex items-center justify-between">
            <div class="flex flex-col gap-1">
              <span class="text-sm font-medium text-ink-gray-8">{{ __("Append Agent Initials") }}</span>
              <span class="text-p-sm text-ink-gray-6">
                {{ __("Adds ^XX (agent initials) to the end of each outgoing message.") }}
              </span>
            </div>
            <Switch
              :modelValue="Boolean(settings.doc.append_agent_initials)"
              @update:modelValue="settings.doc.append_agent_initials = $event ? 1 : 0"
            />
          </div>
          <div class="mt-4 max-w-xs">
            <FormControl
              type="number"
              :label="__('Notification Quiet Period (minutes)')"
              v-model="settings.doc.notification_quiet_minutes"
              :description="__('Suppress bell if an agent replied within this many minutes. 0 = always notify.')"
            />
          </div>
        </div>

        <hr />

        <!-- Ticket Defaults -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">{{ __("Ticket Defaults") }}</div>
          <div class="mt-4 grid grid-cols-2 gap-4">
            <FormControl type="text" :label="__('Default Team')" v-model="settings.doc.default_team" />
            <FormControl
              type="text"
              :label="__('Default Ticket Type')"
              v-model="settings.doc.default_ticket_type"
            />
          </div>
          <div class="mt-4 max-w-xs">
            <FormControl
              type="text"
              :label="__('Placeholder Email Domain')"
              v-model="settings.doc.placeholder_email_domain"
              :description="__('Domain for synthetic addresses on group and unknown-contact tickets.')"
            />
          </div>
        </div>

      </div>
    </template>
  </SettingsLayoutBase>
</template>

<script setup lang="ts">
import {
  Badge, Button, createDocumentResource, createResource,
  FormControl, LoadingIndicator, Switch, toast,
} from "frappe-ui";
import { computed, ref, watch } from "vue";
import { __ } from "@/translation";
import { disableSettingModalOutsideClick } from "../settingsModal";
import SettingsLayoutBase from "@/components/layouts/SettingsLayoutBase.vue";

const settings = createDocumentResource({
  doctype: "Baileys Gateway Settings",
  name: "Baileys Gateway Settings",
  auto: true,
});

const statusResult = ref<{ connected: boolean; phone?: string | null } | null>(null);

const statusResource = createResource({
  url: "helpdesk.integrations.baileys.get_connected_phone",
  onSuccess(data: any) { statusResult.value = data; },
  onError() { statusResult.value = { connected: false }; },
});

function checkStatus() {
  statusResult.value = null;
  statusResource.fetch();
}

const isDirty = computed(() => {
  if (!settings.doc || !settings.originalDoc) return false;
  return JSON.stringify(settings.doc) !== JSON.stringify(settings.originalDoc);
});

watch(isDirty, (val) => { disableSettingModalOutsideClick.value = val; });

async function save() {
  try {
    await settings.save.submit();
    toast.success(__("Baileys Gateway settings saved"));
  } catch (e: any) {
    toast.error(e?.messages?.[0] || __("Failed to save settings"));
  }
}
</script>
```

- [ ] **Step 2: Register in settingsModal.ts**

In `desk/src/components/Settings/settingsModal.ts`:

Add import (with the other component imports at the top):
```typescript
import BaileysGatewaySettings from "./BaileysGateway/BaileysGatewaySettings.vue";
```

In the `tabs` computed, inside the Integrations section array, add after the WhatsApp entry:
```typescript
{
  label: __("Baileys Gateway"),
  icon: markRaw(WhatsAppIcon),
  component: markRaw(BaileysGatewaySettings),
  condition: () => auth.isAdmin,
},
```

Add `"Baileys Gateway"` to the `TabName` type union:
```typescript
type TabName =
  | "Profile"
  | "Email Accounts"
  | "Email Notifications"
  | "General"
  | "Agents"
  | "Invite Agents"
  | "Teams"
  | "SLA Policies"
  | "Business Holidays"
  | "Assignment Rules"
  | "Field Dependencies"
  | "Telephony"
  | "WhatsApp"
  | "Baileys Gateway"
  | "Saved Replies";
```

- [ ] **Step 3: Build and verify**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk
```

Open Settings → Integrations. Confirm:
- "Baileys Gateway" entry appears below "WhatsApp"
- Settings page loads with Connection, Status, Chat Behaviour, Ticket Defaults sections
- "Check Status" button calls the backend and shows the connected phone (e.g. "+254712345678")
- Append Agent Initials toggle is present and saves correctly

- [ ] **Step 4: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/components/Settings/BaileysGateway/BaileysGatewaySettings.vue
git add desk/src/components/Settings/settingsModal.ts
git commit -m "feat(settings): add Baileys Gateway settings page with status check and initials toggle"
```

---

## Self-Review

**Spec coverage:**
1. ✅ Tab UX — Task 5: `hasWhatsApp` made computed, Baileys tab renamed "WhatsApp", frappe_whatsapp tab hidden for Baileys tickets
2. ✅ Reply-to — Tasks 1+4+6+7: gateway `quoted` option in `/send`, backend params + `reply_to_message_id` stored, chat tab wires `@reply`, reply box shows preview and passes params
3. ✅ Reactions — Tasks 1+4+6: gateway `/react` endpoint, backend `send_baileys_reaction()` + outgoing record, chat tab wires `@react`, `reactionsMap` computed from reaction-type messages
4. ✅ Phone in chat header — Tasks 1+4+6: gateway exposes phone in `/health`, `get_connected_phone()` backend API, chat header shows "+XXXXXX"
5. ✅ Baileys settings in Settings modal — Task 8: `BaileysGatewaySettings.vue` created, registered in `settingsModal.ts`
6. ✅ Initials toggle — Tasks 3+4+8: `append_agent_initials` field added to DocType, backend respects it, settings UI has the toggle

**Placeholder scan:** No TBD, TODO, or incomplete sections. All code blocks are complete and self-contained.

**Type consistency:**
- `replyingTo`: `Record<string, any> | null` — defined in Task 6 (chat tab), consumed in Task 7 (reply box `replyTo` prop) ✅
- `reactionsMap` shape `Record<string, Array<{ emoji: string; type: string }>>` — matches the `reactions` prop format expected by `WhatsAppBubble` (verified from existing `WhatsAppChatTab.vue` usage) ✅
- `sendReaction(emoji: string, targetMessageId: string)` — defined in Task 6, matches `WhatsAppBubble`'s `@react` event signature ✅
- `get_connected_phone()` returns `{ phone, connected }` — used consistently in Task 4 (definition), Task 6 (`connectedPhone.data?.phone`), Task 8 (`statusResult.value = data`) ✅
- `reply_to_message_id` field name consistent across DocType JSON (Task 2), Python backend (Task 4), Vue props (Tasks 6+7) ✅
- `content_type: "reaction"` — added to DocType options (Task 2), stored by backend (Task 4), filtered in frontend `messageList` computed (Task 6) ✅
