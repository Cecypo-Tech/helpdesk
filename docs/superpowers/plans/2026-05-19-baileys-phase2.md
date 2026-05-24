# Baileys Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship six improvements to the Baileys WhatsApp integration: dead-code cleanup, efficient conversations query, contact auto-population, reaction notification suppression, standalone mark-as-read, and delivery status ticks.

**Architecture:** All backend changes are in `helpdesk/integrations/baileys.py`. Frontend changes are isolated to `BaileysChat.vue` (mark-as-read), `WhatsAppPage.vue` (status-update socket event), `BaileysGroupChatTab.vue` (status-update socket event), and `BaileysGatewaySettings.vue` (webhook URL display). No new files needed.

**Tech Stack:** Python/Frappe backend, Vue 3 Composition API, frappe-ui `createResource`, Frappe realtime (socket.io).

**Execution order:** Task 1 → 2 → 3 → 4 → 5 → 6 (each is independent after Task 1 cleans the file).

---

## Files Modified

| File | Tasks |
|------|-------|
| `helpdesk/integrations/baileys.py` | 1, 2, 3, 4, 5, 6 |
| `desk/src/components/whatsapp/BaileysChat.vue` | 5 |
| `desk/src/pages/whatsapp/WhatsAppPage.vue` | 6 |
| `desk/src/components/whatsapp/BaileysGroupChatTab.vue` | 6 |
| `desk/src/components/Settings/BaileysGateway/BaileysGatewaySettings.vue` | 6 |

---

## Task 1: Dead-code cleanup

**Files:**
- Modify: `helpdesk/integrations/baileys.py`

- [ ] **Remove unreachable return in `send_baileys_reaction`**

In `send_baileys_reaction`, after `return {}` on line ~380 there is a dead `return {"ok": True}`. Delete that line so the function ends cleanly:

```python
    if ticket:
        _publish_event(ticket, is_incoming=False)
    else:
        _publish_baileys_event(jid, is_incoming=False)

    return {}
```

- [ ] **Remove DEBUG media logger**

Delete the three lines that log the full media payload (lines ~139–142):

```python
    # Before (delete these lines):
    # DEBUG: log full payload for incoming media to understand gateway format
    content_type_raw = payload.get("contentType", "text")
    if content_type_raw in ("image", "video", "audio", "document"):
        frappe.logger("baileys").info("MEDIA WEBHOOK PAYLOAD: %s", raw_body[:2000])
```

After deletion, `content_type_raw` is no longer used at that point — keep the later `content_type = payload.get("contentType", "text")` assignment on line ~149 (it's a separate variable, same value, used for the DB insert).

- [ ] **Verify Python syntax**

```bash
cd /home/frappeuser/bench16
python3 -c "import ast; ast.parse(open('apps/helpdesk/helpdesk/integrations/baileys.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/integrations/baileys.py
git commit -m "fix(baileys): remove dead return and debug media logger"
```

---

## Task 2: Efficient conversations query

**Files:**
- Modify: `helpdesk/integrations/baileys.py` — `get_baileys_conversations()`

Current implementation loads all Baileys Messages into Python memory and deduplicates in a loop. Replace with a single GROUP BY query.

- [ ] **Replace `get_baileys_conversations` body**

```python
@frappe.whitelist()
def get_baileys_conversations() -> list[dict]:
	"""Return one entry per unique JID sorted by most-recent message first."""
	from frappe.query_builder import DocType
	from frappe.query_builder.functions import Max

	BM = DocType("Baileys Message")

	# One row per JID — most recent message fields via subquery join
	latest = (
		frappe.qb.from_(BM)
		.select(BM.jid, Max(BM.creation).as_("latest_creation"))
		.where(~BM.jid.like("%@broadcast"))
		.groupby(BM.jid)
	)

	# Re-join to get all fields for the latest row per JID
	BM2 = DocType("Baileys Message")
	rows = (
		frappe.qb.from_(BM2)
		.join(latest).on(
			(BM2.jid == latest.jid) & (BM2.creation == latest.latest_creation)
		)
		.select(
			BM2.jid, BM2.sender_name, BM2.message,
			BM2.content_type, BM2.direction, BM2.creation,
		)
		.orderby(BM2.creation, order=frappe.qb.desc)
		.run(as_dict=True)
	)

	# Deduplicate (two messages with identical creation for same JID — extremely rare)
	seen: set[str] = set()
	deduped = []
	for r in rows:
		if r.jid not in seen:
			seen.add(r.jid)
			deduped.append(r)

	settings = _settings()
	group_names = {row.jid: (row.group_name or row.jid) for row in (settings.group_jids or [])}

	jids = [r.jid for r in deduped]
	contacts: dict[str, dict] = {}
	if jids and frappe.db.exists("DocType", "Baileys Contact"):
		for c in frappe.get_all(
			"Baileys Contact",
			filters={"jid": ["in", jids]},
			fields=["jid", "custom_name", "company", "assigned_team"],
		):
			contacts[c.jid] = c

	restrict = settings.get("restrict_chats_by_team")
	user_teams: set[str] = set()
	user_has_any_team = False
	if restrict:
		tm_rows = frappe.get_all("HD Team Member", filters={"user": frappe.session.user}, pluck="parent")
		user_teams = set(tm_rows)
		user_has_any_team = bool(user_teams)

	result = []
	for r in deduped:
		jid = r.jid
		is_grp = _is_group(jid)
		contact = contacts.get(jid, {})
		assigned_team = contact.get("assigned_team") or ""

		if restrict and user_has_any_team and assigned_team and assigned_team not in user_teams:
			continue

		display_name = (
			contact.get("custom_name")
			or (group_names.get(jid) if is_grp else None)
			or r.get("sender_name")
			or jid.split("@")[0]
		)
		result.append({
			"jid": jid,
			"display_name": display_name or jid,
			"company": contact.get("company") or "",
			"assigned_team": assigned_team,
			"is_group": is_grp,
			"last_message": r.get("message") or f"[{r.get('content_type', 'media')}]",
			"last_message_time": str(r["creation"]),
			"last_direction": r.get("direction", "Incoming"),
			"content_type": r.get("content_type", "text"),
		})

	return result
```

- [ ] **Verify syntax**

```bash
cd /home/frappeuser/bench16
python3 -c "import ast; ast.parse(open('apps/helpdesk/helpdesk/integrations/baileys.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/integrations/baileys.py
git commit -m "perf(baileys): replace Python dedup loop with GROUP BY query in get_baileys_conversations"
```

---

## Task 3: Contact auto-population from incoming messages

**Files:**
- Modify: `helpdesk/integrations/baileys.py` — `webhook()` and new helper `_upsert_contact_name()`

When an individual (non-group) message arrives and no `custom_name` override exists for the JID, create or update the `Baileys Contact` with the sender's display name. Manual edits always win.

- [ ] **Add `_upsert_contact_name` helper** (place after `_group_label`, before `# ── Webhook`):

```python
def _upsert_contact_name(jid: str, sender_name: str) -> None:
	"""Silently record sender_name in Baileys Contact if no custom override exists."""
	if not jid or not sender_name or _is_group(jid):
		return
	try:
		if frappe.db.exists("Baileys Contact", {"jid": jid}):
			existing = frappe.db.get_value("Baileys Contact", {"jid": jid}, "custom_name")
			if existing:
				return  # manual override — never clobber
			frappe.db.set_value(
				"Baileys Contact", {"jid": jid}, "custom_name", sender_name, update_modified=False
			)
		else:
			frappe.get_doc({
				"doctype": "Baileys Contact",
				"jid": jid,
				"phone": _phone_from_jid(jid),
				"custom_name": sender_name,
				"company": "",
				"assigned_team": "",
			}).insert(ignore_permissions=True)
	except Exception:
		pass  # never let contact bookkeeping break message delivery
```

- [ ] **Call it in `webhook()` after the message insert**

Find the block after `frappe.get_doc({...}).insert(ignore_permissions=True)` and add one line:

```python
	frappe.get_doc({
		"doctype": "Baileys Message",
		# ... existing fields ...
	}).insert(ignore_permissions=True)

	_upsert_contact_name(jid, sender_name)   # ← add this line
	_publish_baileys_event(jid, is_incoming=True)
	_notify_agents_baileys(jid, message, sender_name)
```

- [ ] **Verify syntax**

```bash
cd /home/frappeuser/bench16
python3 -c "import ast; ast.parse(open('apps/helpdesk/helpdesk/integrations/baileys.py').read()); print('OK')"
```

- [ ] **Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/integrations/baileys.py
git commit -m "feat(baileys): auto-populate contact display name from first incoming message"
```

---

## Task 4: Suppress bell notifications for incoming reactions

**Files:**
- Modify: `helpdesk/integrations/baileys.py` — `webhook()`

Reactions from customers (`content_type == "reaction"`) should not ring the bell.

- [ ] **Add guard in `webhook()` before calling `_notify_agents_baileys`**

In `webhook()`, the call to `_notify_agents_baileys` currently fires unconditionally. Change the block at the bottom to:

```python
	_upsert_contact_name(jid, sender_name)
	_publish_baileys_event(jid, is_incoming=True)
	if content_type != "reaction":
		_notify_agents_baileys(jid, message, sender_name)

	return {"status": "ok"}
```

- [ ] **Verify syntax**

```bash
cd /home/frappeuser/bench16
python3 -c "import ast; ast.parse(open('apps/helpdesk/helpdesk/integrations/baileys.py').read()); print('OK')"
```

- [ ] **Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/integrations/baileys.py
git commit -m "fix(baileys): suppress bell notification for incoming customer reactions"
```

---

## Task 5: Mark-as-read for standalone (JID-based) chats

**Files:**
- Modify: `helpdesk/integrations/baileys.py` — `mark_baileys_messages_read()`
- Modify: `desk/src/components/whatsapp/BaileysChat.vue`

### Backend

- [ ] **Update `mark_baileys_messages_read` to accept `jid` param**

Replace the existing function signature and query:

```python
@frappe.whitelist()
def mark_baileys_messages_read(ticket: str = None, jid: str = None) -> int:
	"""Mark unread incoming Baileys Messages as read, send read receipts to gateway."""
	settings = _settings()

	if not jid and ticket:
		jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	if not jid:
		return 0

	filters: dict = {"jid": jid, "direction": "Incoming", "status": ["not in", ["Read"]]}
	if ticket:
		filters["reference_doctype"] = "HD Ticket"
		filters["reference_name"] = ticket

	unread = frappe.get_all("Baileys Message", filters=filters, fields=["name", "message_id"])

	if not unread:
		return 0

	message_ids = [r.message_id for r in unread if r.message_id]

	if message_ids and settings.enabled and settings.gateway_url:
		gateway_url = (settings.gateway_url or "").rstrip("/")
		api_key = settings.api_key or ""
		try:
			_requests.post(
				f"{gateway_url}/markRead",
				json={"sessionName": settings.session_name or "helpdesk", "jid": jid, "messageIds": message_ids},
				headers={"X-API-Key": api_key},
				timeout=5,
			)
		except Exception:
			pass

	for row in unread:
		frappe.db.set_value("Baileys Message", row.name, "status", "Read", update_modified=False)

	# Clear HD Notifications (ticket-linked chats only)
	if ticket:
		try:
			for notif in frappe.get_all(
				"HD Notification",
				filters={"user_to": frappe.session.user, "reference_ticket": ticket, "notification_type": "WhatsApp", "read": 0},
				pluck="name",
			):
				frappe.db.set_value("HD Notification", notif, "read", 1, update_modified=False)
		except Exception:
			pass

	return len(unread)
```

- [ ] **Verify syntax**

```bash
cd /home/frappeuser/bench16
python3 -c "import ast; ast.parse(open('apps/helpdesk/helpdesk/integrations/baileys.py').read()); print('OK')"
```

### Frontend — BaileysChat.vue

- [ ] **Add `markReadResource` and call it when a conversation is opened**

In `BaileysChat.vue`, in the `<script setup>` section, add after the `messages` resource:

```typescript
const markReadResource = createResource({
  url: "helpdesk.integrations.baileys.mark_baileys_messages_read",
});
```

In the `loadMessages()` function, call mark-read after loading:

```typescript
function loadMessages() {
  if (props.jid) {
    messages.submit({ jid: props.jid });
    markReadResource.submit({ jid: props.jid });
    localStorage.setItem(`baileys_last_read_${props.jid}`, new Date().toISOString());
  }
}
```

- [ ] **Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/integrations/baileys.py desk/src/components/whatsapp/BaileysChat.vue
git commit -m "feat(baileys): mark standalone JID chats as read and send read receipts to gateway"
```

---

## Task 6: Delivery status ticks (status update webhook)

**Files:**
- Modify: `helpdesk/integrations/baileys.py` — new `status_webhook()` endpoint
- Modify: `desk/src/pages/whatsapp/WhatsAppPage.vue`
- Modify: `desk/src/components/whatsapp/BaileysGroupChatTab.vue`
- Modify: `desk/src/components/Settings/BaileysGateway/BaileysGatewaySettings.vue`

### Backend

- [ ] **Add `status_webhook()` in `baileys.py`** (place after `webhook()`, before `# ── Agent send`):

```python
@frappe.whitelist(allow_guest=True)
def status_webhook():
	"""Receive delivery status updates (Sent→Delivered→Read) from the Baileys gateway."""
	if not frappe.db.exists("DocType", "Baileys Gateway Settings"):
		frappe.response["http_status_code"] = 503
		return {"error": "not configured"}

	settings = _settings()
	if not settings.enabled:
		return {"status": "disabled"}

	api_key = frappe.get_request_header("X-API-Key") or frappe.get_request_header("x-api-key")
	stored_key = settings.api_key or ""
	if not stored_key or api_key != stored_key:
		frappe.response["http_status_code"] = 401
		return {"error": "Unauthorized"}

	try:
		payload = frappe.parse_json(frappe.request.data.decode("utf-8"))
	except Exception:
		frappe.response["http_status_code"] = 400
		return {"error": "Invalid JSON"}

	message_id = payload.get("messageId") or ""
	raw_status = (payload.get("status") or "").lower()
	status_map = {"sent": "Sent", "delivered": "Delivered", "read": "Read", "failed": "Failed"}
	status = status_map.get(raw_status)

	if not message_id or not status:
		return {"status": "skipped", "reason": "missing messageId or status"}

	msg = frappe.db.get_value(
		"Baileys Message",
		{"message_id": message_id},
		["name", "jid", "reference_doctype", "reference_name"],
		as_dict=True,
	)
	if not msg:
		return {"status": "skipped", "reason": "message not found"}

	frappe.set_user("Administrator")
	frappe.db.set_value("Baileys Message", msg.name, "status", status, update_modified=False)
	frappe.db.commit()

	frappe.publish_realtime(
		"helpdesk:baileys-status-update",
		message={"message_id": message_id, "status": status, "jid": msg.jid},
	)

	return {"status": "ok"}
```

- [ ] **Verify syntax**

```bash
cd /home/frappeuser/bench16
python3 -c "import ast; ast.parse(open('apps/helpdesk/helpdesk/integrations/baileys.py').read()); print('OK')"
```

### Frontend — WhatsAppPage.vue

- [ ] **Listen for `helpdesk:baileys-status-update` and patch message in BaileysChat**

`WhatsAppPage.vue` already has `baileysChat` ref. Add the socket listener alongside `handleBaileysMessage`:

In `<script setup>`:
```typescript
function handleBaileysStatusUpdate(data: { message_id: string; status: string; jid: string }) {
  if (data.jid === selectedJid.value) {
    baileysChat.value?.patchMessageStatus(data.message_id, data.status);
  }
}
```

In `onMounted`:
```typescript
$socket.on("helpdesk:baileys-status-update", handleBaileysStatusUpdate);
```

In `onBeforeUnmount`:
```typescript
$socket.off("helpdesk:baileys-status-update", handleBaileysStatusUpdate);
```

### Frontend — BaileysChat.vue

- [ ] **Expose `patchMessageStatus` method**

In `BaileysChat.vue`, add to `defineExpose`:

```typescript
defineExpose({
  refresh() {
    loadMessages();
    scrollToBottom();
  },
  patchMessageStatus(messageId: string, status: string) {
    const list: Record<string, any>[] = messages.data || [];
    const msg = list.find((m) => m.message_id === messageId);
    if (msg) msg.status = status;
  },
});
```

### Frontend — BaileysGroupChatTab.vue

- [ ] **Listen for status updates in ticket-linked chat**

In `BaileysGroupChatTab.vue`, find where `$socket.on("helpdesk:baileys-message", ...)` is wired and add alongside it:

```typescript
function handleStatusUpdate(data: { message_id: string; status: string }) {
  const list: Record<string, any>[] = messages.data || [];
  const msg = list.find((m) => m.message_id === data.message_id);
  if (msg) msg.status = data.status;
}
```

Wire in `onMounted` / `onBeforeUnmount` alongside the existing message listener.

### Frontend — BaileysGatewaySettings.vue

- [ ] **Add read-only Status Webhook URL field**

Find where the Gateway URL input is rendered and add below it:

```vue
<div class="field">
  <label class="field-label">Status Webhook URL <span class="text-ink-gray-4 text-xs">(copy to your gateway config)</span></label>
  <div class="flex items-center gap-2">
    <input
      :value="statusWebhookUrl"
      readonly
      class="flex-1 form-input font-mono text-xs"
    />
    <Button size="sm" @click="copyWebhookUrl">Copy</Button>
  </div>
</div>
```

In `<script setup>`:
```typescript
const statusWebhookUrl = computed(() =>
  `${window.location.origin}/api/method/helpdesk.integrations.baileys.status_webhook`
);

function copyWebhookUrl() {
  navigator.clipboard.writeText(statusWebhookUrl.value);
  toast.success("Copied!");
}
```

- [ ] **Restart gunicorn and build assets**

```bash
cd /home/frappeuser/bench16
pkill -f "frappe.app" && bench serve --port 8002 &
bench build --app helpdesk
```

- [ ] **Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/integrations/baileys.py \
        desk/src/pages/whatsapp/WhatsAppPage.vue \
        desk/src/components/whatsapp/BaileysChat.vue \
        desk/src/components/whatsapp/BaileysGroupChatTab.vue \
        desk/src/components/Settings/BaileysGateway/BaileysGatewaySettings.vue
git commit -m "feat(baileys): delivery status webhook, realtime tick updates, and webhook URL in settings"
```

---

## Restart Note

After all Python changes: `pkill -f "frappe.app" && bench serve --port 8002 &`
After all Vue changes: `bench build --app helpdesk` then hard-refresh browser.
