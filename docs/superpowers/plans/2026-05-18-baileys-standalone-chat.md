# Baileys Standalone WhatsApp Chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ticket-embedded Baileys WhatsApp tab with a standalone `/whatsapp` page accessible from the helpdesk sidebar — three-column layout (helpdesk sidebar · conversation list · chat pane), no ticket creation, real-time updates, bell notifications.

**Architecture:** The Baileys webhook stops creating HD Tickets and only stores `Baileys Message` records + publishes `helpdesk:baileys-message` realtime events. A new `get_baileys_conversations()` API returns one entry per unique JID sorted by recency. Three new Vue components (`BaileysConversationList`, `BaileysConversationItem`, `BaileysChat`) compose the page; `BaileysReplyBox` and `WhatsAppBubble` are reused unchanged except for a new `jid` prop on the reply box.

**Tech Stack:** Python/Frappe (backend), Vue 3 Composition API + frappe-ui `createResource` (frontend), Frappe realtime socket (live updates)

---

## File Map

| File | Change |
|------|--------|
| `helpdesk/integrations/baileys.py` | Simplify `webhook()`, add `get_baileys_conversations()`, `_publish_baileys_event()`, `_notify_agents_baileys()`; update `get_baileys_messages()`, `send_baileys_reply()`, `send_baileys_reaction()`, `send_baileys_media()`; delete 5 dead functions |
| `helpdesk/tests/test_baileys_standalone.py` | New — Python tests for the simplified webhook + new APIs |
| `desk/src/components/whatsapp/BaileysReplyBox.vue` | Add `jid` prop; use it instead of `ticket` when provided |
| `desk/src/pages/whatsapp/WhatsAppPage.vue` | New — three-column shell |
| `desk/src/components/whatsapp/BaileysConversationList.vue` | New — scrollable conversation list with search + realtime |
| `desk/src/components/whatsapp/BaileysConversationItem.vue` | New — single conversation row |
| `desk/src/components/whatsapp/BaileysChat.vue` | New — right pane chat (reuses WhatsAppBubble + BaileysReplyBox) |
| `desk/src/router/index.ts` | Add `/whatsapp` route |
| `desk/src/components/layouts/layoutSettings.ts` | Add WhatsApp sidebar link |
| `desk/src/stores/notification.ts` | Listen to `helpdesk:baileys-notification` for bell + sound |

---

## Task 1: Backend — simplify webhook, new APIs, update existing APIs

**Files:**
- Modify: `helpdesk/integrations/baileys.py`

Context: `baileys.py` is at `/home/frappeuser/bench16/apps/helpdesk/helpdesk/integrations/baileys.py`. The bench root is `/home/frappeuser/bench16`. Run `bench restart` (not rebuild) after Python changes.

- [ ] **Step 1: Delete dead ticket-routing functions**

Remove these five functions entirely from `baileys.py`. They are only used in the ticket-creation path which is being deleted:

```
_find_open_group_ticket()     — lines ~112-129
_find_open_dm_ticket()        — lines ~132-163
_create_ticket()              — lines ~199-217
_match_phone_to_contact()     — lines ~221-223
_group_team()                 — lines ~191-196
```

Also delete `_set_ticket_status()` since the standalone path doesn't use it. Keep it in a temporary block if `send_baileys_reply(ticket=...)` still calls it — we'll fix that in Step 5.

- [ ] **Step 2: Add `_publish_baileys_event()` and `_notify_agents_baileys()`**

Add these two helpers after the existing `_publish_event()` function:

```python
def _publish_baileys_event(jid: str, is_incoming: bool) -> None:
	frappe.db.commit()
	frappe.publish_realtime(
		"helpdesk:baileys-message",
		message={"jid": jid, "is_incoming": is_incoming},
	)


def _notify_agents_baileys(jid: str, message_text: str, sender_name: str) -> None:
	"""Publish bell notification for standalone WhatsApp chat."""
	settings = _settings()
	quiet_minutes = int(settings.notification_quiet_minutes or 0)
	if quiet_minutes > 0:
		last_outgoing = frappe.get_all(
			"Baileys Message",
			filters={"jid": jid, "direction": "Outgoing"},
			fields=["creation"],
			order_by="creation desc",
			limit=1,
		)
		if last_outgoing:
			minutes_since = time_diff_in_hours(now_datetime(), last_outgoing[0].creation) * 60
			if minutes_since < quiet_minutes:
				return

	frappe.publish_realtime(
		"helpdesk:baileys-notification",
		message={"jid": jid, "message": (message_text or "")[:80], "sender": sender_name},
	)
```

- [ ] **Step 3: Rewrite `webhook()`**

Replace the entire `webhook()` function body with the simplified version below. The function no longer creates tickets — it only stores the message, publishes the realtime event, and fires the notification.

```python
@frappe.whitelist(allow_guest=True)
def webhook():
	"""Receive incoming messages from the Baileys gateway (standalone mode — no ticket creation)."""
	if not frappe.db.exists("DocType", "Baileys Gateway Settings"):
		frappe.response["http_status_code"] = 503
		return {"error": "Baileys Gateway Settings not configured"}

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

	jid = payload.get("jid", "")
	message_id = payload.get("messageId", "")
	sender = payload.get("sender", jid)
	sender_name = payload.get("senderName") or sender.split("@")[0]
	message = payload.get("message", "")
	content_type = payload.get("contentType", "text")
	media_url = payload.get("mediaUrl") or ""
	quoted_message_id = payload.get("quotedMessageId") or ""

	if not jid:
		return {"status": "skipped", "reason": "no jid"}

	if _is_blocked(jid, sender, settings):
		return {"status": "blocked"}

	if message_id and frappe.db.exists("Baileys Message", {"message_id": message_id}):
		return {"status": "duplicate"}

	frappe.set_user("Administrator")

	frappe.get_doc({
		"doctype": "Baileys Message",
		"direction": "Incoming",
		"jid": jid,
		"sender_jid": sender,
		"sender_name": sender_name,
		"profile_name": sender_name,
		"message": message,
		"content_type": content_type or "text",
		"media_url": media_url,
		"message_id": message_id,
		"reply_to_message_id": quoted_message_id,
		"status": "Delivered",
		"reference_doctype": "",
		"reference_name": "",
	}).insert(ignore_permissions=True)

	_publish_baileys_event(jid, is_incoming=True)
	_notify_agents_baileys(jid, message, sender_name)

	return {"status": "ok"}
```

- [ ] **Step 4: Add `get_baileys_conversations()`**

Add this new whitelisted function after `get_connected_phone()`:

```python
@frappe.whitelist()
def get_baileys_conversations() -> list[dict]:
	"""Return one entry per unique JID sorted by most-recent message first."""
	from frappe.query_builder import DocType

	BM = DocType("Baileys Message")
	rows = (
		frappe.qb.from_(BM)
		.select(BM.jid, BM.sender_name, BM.message, BM.content_type, BM.direction, BM.creation)
		.orderby(BM.creation, order=frappe.qb.desc)
		.run(as_dict=True)
	)

	seen: dict[str, dict] = {}
	for r in rows:
		if r.get("jid") and r["jid"] not in seen:
			seen[r["jid"]] = r

	settings = _settings()
	group_names = {row.jid: (row.group_name or row.jid) for row in (settings.group_jids or [])}

	result = []
	for jid, r in seen.items():
		is_grp = _is_group(jid)
		display_name = (
			group_names.get(jid)
			if is_grp
			else (r.get("sender_name") or jid.split("@")[0])
		)
		result.append({
			"jid": jid,
			"display_name": display_name or jid,
			"is_group": is_grp,
			"last_message": r.get("message") or f"[{r.get('content_type', 'media')}]",
			"last_message_time": str(r["creation"]),
			"last_direction": r.get("direction", "Incoming"),
			"content_type": r.get("content_type", "text"),
		})

	return result
```

- [ ] **Step 5: Update `get_baileys_messages()` to accept `jid` directly**

Replace the existing `get_baileys_messages(ticket: str)` signature and body:

```python
@frappe.whitelist()
def get_baileys_messages(jid: str = None, ticket: str = None) -> list[dict]:
	"""Return messages for a conversation. Accepts jid directly or ticket name (backward compat)."""
	from frappe.query_builder import DocType

	if not jid and ticket:
		jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	if not jid:
		return []

	BM = DocType("Baileys Message")
	User = DocType("User")

	rows = (
		frappe.qb.from_(BM)
		.left_join(User).on(User.name == BM.owner)
		.select(
			BM.name, BM.creation, BM.direction, BM.jid, BM.message,
			BM.content_type, BM.media_url, BM.sender_jid, BM.sender_name,
			BM.profile_name, BM.message_id, BM.reply_to_message_id, BM.status, BM.owner,
			User.full_name.as_("sender_full_name"),
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

	return rows
```

- [ ] **Step 6: Update `send_baileys_reply()` to accept `jid` directly**

Replace the existing `send_baileys_reply()` with this version that accepts either `ticket` or `jid`:

```python
@frappe.whitelist()
def send_baileys_reply(
	ticket: str = None,
	jid: str = None,
	message: str = "",
	content_type: str = "text",
	media_url: str | None = None,
	reply_to_message_id: str | None = None,
	reply_to_text: str | None = None,
	reply_to_from_me: bool = False,
) -> dict:
	"""Send a text (or media) reply via the Baileys gateway."""
	settings = _settings()
	if not settings.enabled:
		frappe.throw(_("Baileys gateway is not enabled."))

	if not jid and ticket:
		jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	if not jid:
		frappe.throw(_("No WhatsApp JID provided."))

	if settings.append_agent_initials:
		agent_suffix = f"\n^{_agent_initials()}"
		full_message = f"{message}{agent_suffix}" if message else agent_suffix.strip()
	else:
		full_message = message or ""

	gateway_url = (settings.gateway_url or "").rstrip("/")
	api_key = settings.api_key or ""

	payload: dict = {
		"sessionName": settings.session_name or "helpdesk",
		"jid": jid,
		"message": full_message,
		"contentType": content_type,
	}
	if media_url:
		payload["mediaUrl"] = media_url
	if reply_to_message_id:
		payload["replyToMessageId"] = reply_to_message_id
		payload["replyToText"] = reply_to_text or ""
		payload["replyToFromMe"] = bool(reply_to_from_me)

	try:
		resp = _requests.post(
			f"{gateway_url}/send",
			json=payload,
			headers={"X-API-Key": api_key, "Content-Type": "application/json"},
			timeout=15,
		)
		resp.raise_for_status()
		sent_id = resp.json().get("messageId", "")
	except Exception as e:
		frappe.throw(_("Baileys gateway send failed: {0}").format(str(e)))

	msg_doc = frappe.get_doc({
		"doctype": "Baileys Message",
		"direction": "Outgoing",
		"jid": jid,
		"sender_jid": "",
		"sender_name": frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user,
		"profile_name": "",
		"message": message,
		"content_type": content_type,
		"media_url": media_url or "",
		"message_id": sent_id,
		"reply_to_message_id": reply_to_message_id or "",
		"status": "Sent",
		"reference_doctype": "HD Ticket" if ticket else "",
		"reference_name": ticket or "",
	})
	msg_doc.insert(ignore_permissions=True)

	if ticket:
		# Ticket-based path: keep existing ticket side-effects
		assign_json = frappe.db.get_value("HD Ticket", ticket, "_assign") or "[]"
		if not frappe.parse_json(assign_json):
			try:
				frappe.get_doc("HD Ticket", ticket).assign_agent(frappe.session.user)
			except Exception:
				pass
		_publish_event(ticket, is_incoming=False)
	else:
		_publish_baileys_event(jid, is_incoming=False)

	return {"name": msg_doc.name, "message_id": sent_id, "status": "Sent"}
```

- [ ] **Step 7: Update `send_baileys_reaction()` to accept `jid` directly**

Replace the existing `send_baileys_reaction()`:

```python
@frappe.whitelist()
def send_baileys_reaction(
	ticket: str = None,
	jid: str = None,
	target_message_id: str = "",
	emoji: str = "",
) -> dict:
	"""Send an emoji reaction to a specific Baileys message."""
	settings = _settings()
	if not settings.enabled:
		frappe.throw(_("Baileys gateway is not enabled."))

	if not jid and ticket:
		jid = frappe.db.get_value("HD Ticket", ticket, "baileys_jid")
	if not jid:
		frappe.throw(_("No WhatsApp JID provided."))

	gateway_url = (settings.gateway_url or "").rstrip("/")
	api_key = settings.api_key or ""
	target_direction = frappe.db.get_value("Baileys Message", {"message_id": target_message_id}, "direction") or "Incoming"

	try:
		resp = _requests.post(
			f"{gateway_url}/react",
			json={
				"sessionName": settings.session_name or "helpdesk",
				"jid": jid,
				"messageId": target_message_id,
				"emoji": emoji,
				"fromMe": target_direction == "Outgoing",
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
		"reference_doctype": "HD Ticket" if ticket else "",
		"reference_name": ticket or "",
	}).insert(ignore_permissions=True)

	if ticket:
		_publish_event(ticket, is_incoming=False)
	else:
		_publish_baileys_event(jid, is_incoming=False)

	return {"ok": True}
```

- [ ] **Step 8: Update `send_baileys_media()` to accept `jid` directly**

Replace the existing `send_baileys_media()`:

```python
@frappe.whitelist(allow_guest=False)
def send_baileys_media(ticket: str = None, jid: str = None, message: str = "", content_type: str = "document") -> dict:
	"""Upload a file to Frappe storage and send its public URL via the gateway."""
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

	return send_baileys_reply(
		ticket=ticket,
		jid=jid,
		message=message,
		content_type=content_type,
		media_url=public_url,
	)
```

- [ ] **Step 9: Restart and verify no import errors**

```bash
cd /home/frappeuser/bench16
pkill -f "frappe.app" 2>/dev/null; bench serve --port 8002 &
sleep 3
curl -s http://localhost:8002/api/method/helpdesk.integrations.baileys.get_baileys_conversations \
  -H "X-Frappe-CSRF-Token: $(bench --site site16.local execute 'print(frappe.session.csrf_token)' 2>/dev/null)" \
  | python3 -m json.tool | head -5
```

Expected: JSON response (may be `{"message": []}` if no messages yet, or an auth error — not a 500/import error).

- [ ] **Step 10: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/integrations/baileys.py
git commit -m "feat(backend): standalone Baileys chat — simplified webhook, get_baileys_conversations, jid-direct send APIs"
```

---

## Task 2: Backend tests

**Files:**
- Create: `helpdesk/tests/test_baileys_standalone.py`
- Test: `helpdesk/tests/test_baileys_standalone.py`

Context: Existing test pattern is in `helpdesk/tests/test_baileys.py`. Run tests with `bench --site site16.local run-tests --app helpdesk --module helpdesk.tests.test_baileys_standalone`.

- [ ] **Step 1: Write failing tests**

Create `helpdesk/tests/test_baileys_standalone.py`:

```python
import frappe
from frappe.tests.utils import FrappeTestCase


class TestBaileysStandalone(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.addCleanup(self._restore_settings)
		frappe.db.set_single_value("Baileys Gateway Settings", "enabled", 1)
		frappe.db.set_single_value("Baileys Gateway Settings", "api_key", "testkey")
		frappe.db.set_single_value("Baileys Gateway Settings", "gateway_url", "http://localhost:9999")

	def _restore_settings(self):
		frappe.db.set_single_value("Baileys Gateway Settings", "enabled", 0)
		frappe.db.set_single_value("Baileys Gateway Settings", "api_key", "")

	def _make_message(self, jid="120363test@g.us", direction="Incoming", message="hello", content_type="text", msg_id=None):
		doc = frappe.get_doc({
			"doctype": "Baileys Message",
			"jid": jid,
			"direction": direction,
			"message": message,
			"content_type": content_type,
			"message_id": msg_id or frappe.generate_hash(length=8),
			"sender_name": "Test Sender",
			"sender_jid": jid,
			"status": "Delivered",
		})
		doc.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Baileys Message", doc.name, ignore_permissions=True, force=True)
		return doc

	def test_get_baileys_conversations_returns_one_per_jid(self):
		jid_a = "111@g.us"
		jid_b = "222@s.whatsapp.net"
		self._make_message(jid=jid_a, message="first")
		self._make_message(jid=jid_a, message="second")
		self._make_message(jid=jid_b, message="dm hello")

		from helpdesk.integrations.baileys import get_baileys_conversations
		result = get_baileys_conversations()
		jids = [r["jid"] for r in result]

		self.assertIn(jid_a, jids)
		self.assertIn(jid_b, jids)
		# Each JID appears exactly once
		self.assertEqual(jids.count(jid_a), 1)
		self.assertEqual(jids.count(jid_b), 1)

	def test_get_baileys_conversations_sorted_by_recency(self):
		jid_old = "old111@g.us"
		jid_new = "new222@g.us"
		self._make_message(jid=jid_old, message="older")
		import time; time.sleep(0.05)
		self._make_message(jid=jid_new, message="newer")

		from helpdesk.integrations.baileys import get_baileys_conversations
		result = get_baileys_conversations()
		jids = [r["jid"] for r in result]
		idx_old = next((i for i, r in enumerate(result) if r["jid"] == jid_old), None)
		idx_new = next((i for i, r in enumerate(result) if r["jid"] == jid_new), None)
		if idx_old is not None and idx_new is not None:
			self.assertLess(idx_new, idx_old)

	def test_get_baileys_conversations_last_message_is_most_recent(self):
		jid = "333@g.us"
		self._make_message(jid=jid, message="earlier")
		import time; time.sleep(0.05)
		self._make_message(jid=jid, message="later one")

		from helpdesk.integrations.baileys import get_baileys_conversations
		result = [r for r in get_baileys_conversations() if r["jid"] == jid]
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]["last_message"], "later one")

	def test_get_baileys_messages_by_jid(self):
		jid = "444@g.us"
		self._make_message(jid=jid, message="msg one")
		self._make_message(jid=jid, message="msg two")

		from helpdesk.integrations.baileys import get_baileys_messages
		result = get_baileys_messages(jid=jid)
		messages = [r["message"] for r in result]
		self.assertIn("msg one", messages)
		self.assertIn("msg two", messages)

	def test_webhook_saves_message_without_creating_ticket(self):
		import unittest.mock as mock

		jid = "555@g.us"
		msg_id = frappe.generate_hash(length=10)

		with mock.patch("frappe.publish_realtime"):
			import frappe.local
			class FakeRequest:
				data = frappe.as_json({
					"jid": jid,
					"messageId": msg_id,
					"sender": jid,
					"senderName": "Tester",
					"message": "webhook test",
					"contentType": "text",
				}).encode()
				def get(self, key, default=None):
					return {"X-API-Key": "testkey", "x-api-key": "testkey"}.get(key, default)

			original_request = getattr(frappe.local, "request", None)
			frappe.local.request = FakeRequest()
			try:
				from helpdesk.integrations.baileys import webhook
				result = webhook()
			finally:
				if original_request is not None:
					frappe.local.request = original_request
				else:
					del frappe.local.request

		self.assertEqual(result.get("status"), "ok")
		# Message saved
		saved = frappe.db.get_value("Baileys Message", {"message_id": msg_id}, ["jid", "message"], as_dict=True)
		self.assertIsNotNone(saved)
		self.assertEqual(saved.jid, jid)
		# No ticket created for this JID
		tickets = frappe.get_all("HD Ticket", filters={"baileys_jid": jid})
		self.assertEqual(len(tickets), 0)
		# Cleanup
		frappe.db.delete("Baileys Message", {"message_id": msg_id})
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk --module helpdesk.tests.test_baileys_standalone 2>&1 | tail -20
```

Expected: failures because the functions don't exist yet (Task 1 hasn't been done). If Task 1 is already done, tests may pass.

- [ ] **Step 3: Run tests — expect PASS**

After Task 1 is complete:

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk --module helpdesk.tests.test_baileys_standalone 2>&1 | tail -20
```

Expected: `5 passed`

- [ ] **Step 4: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/tests/test_baileys_standalone.py
git commit -m "test(backend): standalone Baileys webhook + get_baileys_conversations tests"
```

---

## Task 3: BaileysReplyBox — add `jid` prop

**Files:**
- Modify: `desk/src/components/whatsapp/BaileysReplyBox.vue`

Context: This component already has `replyTo` prop and `clearReply` emit (added earlier). We need to add a `jid` prop so the standalone chat can bypass `ticketId`. When `jid` is provided, pass it to the API instead of `ticket`.

- [ ] **Step 1: Add `jid` prop and update `send()` for text messages**

In `BaileysReplyBox.vue`, replace the `defineProps` and the `sendReply.submit(...)` call:

Find this block (around line 86):
```typescript
const props = defineProps<{
  ticketId: string;
  replyTo?: Record<string, any> | null;
}>();
```

Replace with:
```typescript
const props = defineProps<{
  ticketId: string;
  jid?: string | null;
  replyTo?: Record<string, any> | null;
}>();
```

Find the `sendReply.submit({...})` call in `send()` (around line 189):
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

Replace with:
```typescript
sendReply.submit({
  ...(props.jid ? { jid: props.jid } : { ticket: props.ticketId }),
  message: msgText,
  content_type: "text",
  reply_to_message_id: props.replyTo?.message_id || "",
  reply_to_text: props.replyTo?.message || "",
  reply_to_from_me: props.replyTo?.direction === "Outgoing",
});
```

- [ ] **Step 2: Update media send to pass `jid` when available**

In the `send()` function's attachment branch, find the `formData.append("ticket", ...)` line and add jid handling:

Find:
```typescript
formData.append("ticket", props.ticketId);
```

Replace with:
```typescript
if (props.jid) {
  formData.append("jid", props.jid);
} else {
  formData.append("ticket", props.ticketId);
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/components/whatsapp/BaileysReplyBox.vue
git commit -m "feat(ui): add jid prop to BaileysReplyBox for standalone chat"
```

---

## Task 4: Router, sidebar, WhatsAppPage.vue

**Files:**
- Modify: `desk/src/router/index.ts`
- Modify: `desk/src/components/layouts/layoutSettings.ts`
- Create: `desk/src/pages/whatsapp/WhatsAppPage.vue`

- [ ] **Step 1: Add route to router**

In `desk/src/router/index.ts`, add after the `/call-logs` route (around line 139):

```typescript
  {
    path: "/whatsapp",
    name: "WhatsAppChat",
    component: () => import("@/pages/whatsapp/WhatsAppPage.vue"),
  },
```

- [ ] **Step 2: Add sidebar link**

In `desk/src/components/layouts/layoutSettings.ts`, add at the end of `agentPortalSidebarOptions` array, after the Call Logs entry:

```typescript
import WhatsAppIcon from "../icons/WhatsAppIcon.vue";
```

Add to imports at top of file, then add the entry:
```typescript
  {
    label: __("WhatsApp"),
    icon: WhatsAppIcon,
    to: "WhatsAppChat",
  },
```

Full updated file:
```typescript
import LucideBookOpen from "~icons/lucide/book-open";
import LucideCheckSquare from "~icons/lucide/check-square";
import LucideContact2 from "~icons/lucide/contact-2";
import LucideTicket from "~icons/lucide/ticket";
import { OrganizationsIcon } from "../icons";
import PhoneIcon from "../icons/PhoneIcon.vue";
import WhatsAppIcon from "../icons/WhatsAppIcon.vue";
import LucideHome from "~icons/lucide/home";
import { __ } from "@/translation";

export const agentPortalSidebarOptions = [
  {
    label: __("Home"),
    icon: LucideHome,
    to: "Home",
  },
  {
    label: __("Tickets"),
    icon: LucideTicket,
    to: "TicketsAgent",
  },
  {
    label: __("Tasks"),
    icon: LucideCheckSquare,
    to: "TasksAgent",
  },
  {
    label: __("Knowledge Base"),
    icon: LucideBookOpen,
    to: "AgentKnowledgeBase",
  },
  {
    label: "Customers",
    icon: OrganizationsIcon,
    to: "CustomerList",
  },
  {
    label: __("Contacts"),
    icon: LucideContact2,
    to: "ContactList",
  },
  {
    label: __("Call Logs"),
    icon: PhoneIcon,
    to: "CallLogs",
  },
  {
    label: __("WhatsApp"),
    icon: WhatsAppIcon,
    to: "WhatsAppChat",
  },
];

export const customerPortalSidebarOptions = [
  {
    label: __("Tickets"),
    icon: LucideTicket,
    to: "TicketsCustomer",
  },
  {
    label: __("Knowledge Base"),
    icon: LucideBookOpen,
    to: "CustomerKnowledgeBase",
  },
];
```

- [ ] **Step 3: Create WhatsAppPage.vue**

```bash
mkdir -p /home/frappeuser/bench16/apps/helpdesk/desk/src/pages/whatsapp
```

Create `desk/src/pages/whatsapp/WhatsAppPage.vue`:

```vue
<template>
  <div class="flex h-full overflow-hidden">
    <BaileysConversationList
      :selectedJid="selectedJid"
      @select="onSelect"
    />
    <BaileysChat :jid="selectedJid" :displayName="selectedDisplayName" />
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import BaileysConversationList from "@/components/whatsapp/BaileysConversationList.vue";
import BaileysChat from "@/components/whatsapp/BaileysChat.vue";

const selectedJid = ref<string | null>(null);
const selectedDisplayName = ref<string>("");

function onSelect(jid: string, displayName: string) {
  selectedJid.value = jid;
  selectedDisplayName.value = displayName;
}
</script>
```

- [ ] **Step 4: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/router/index.ts desk/src/components/layouts/layoutSettings.ts desk/src/pages/whatsapp/WhatsAppPage.vue
git commit -m "feat(ui): add /whatsapp route and sidebar link"
```

---

## Task 5: BaileysConversationItem + BaileysConversationList

**Files:**
- Create: `desk/src/components/whatsapp/BaileysConversationItem.vue`
- Create: `desk/src/components/whatsapp/BaileysConversationList.vue`

- [ ] **Step 1: Create BaileysConversationItem.vue**

Create `desk/src/components/whatsapp/BaileysConversationItem.vue`:

```vue
<template>
  <div
    class="flex cursor-pointer items-center gap-3 border-b border-outline-gray-2 px-3 py-3 hover:bg-surface-gray-2"
    :class="selected ? 'bg-surface-gray-2' : ''"
    @click="$emit('select', jid, displayName)"
  >
    <!-- Avatar: deterministic color from JID -->
    <div
      class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white"
      :style="{ background: avatarColor }"
    >
      {{ avatarLetter }}
    </div>

    <!-- Content -->
    <div class="min-w-0 flex-1">
      <div class="flex items-center justify-between gap-1">
        <span class="truncate text-sm font-semibold text-ink-gray-9">{{ displayName }}</span>
        <span class="shrink-0 text-[11px] text-ink-gray-5">{{ formattedTime }}</span>
      </div>
      <div class="mt-0.5 flex items-center justify-between gap-1">
        <span class="truncate text-xs text-ink-gray-5">
          <span v-if="lastDirection === 'Outgoing'" class="text-ink-gray-4">✓✓ </span>
          <span v-if="contentType !== 'text' && !lastMessage" class="italic">
            [{{ contentType }}]
          </span>
          <span v-else>{{ lastMessage }}</span>
        </span>
        <span
          v-if="hasUnread"
          class="ml-1 h-2 w-2 shrink-0 rounded-full bg-green-500"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  jid: string;
  displayName: string;
  isGroup: boolean;
  lastMessage: string;
  lastMessageTime: string;
  lastDirection: string;
  contentType: string;
  hasUnread: boolean;
  selected: boolean;
}>();

defineEmits<{
  (e: "select", jid: string, displayName: string): void;
}>();

const avatarLetter = computed(() => (props.displayName || "?")[0].toUpperCase());

const avatarColor = computed(() => {
  const colors = ["#128c7e", "#7e57c2", "#e67e22", "#c0392b", "#2980b9", "#27ae60", "#8e44ad"];
  let hash = 0;
  for (const ch of props.jid) hash = ((hash * 31) + ch.charCodeAt(0)) & 0x7fffffff;
  return colors[hash % colors.length];
});

const formattedTime = computed(() => {
  if (!props.lastMessageTime) return "";
  const d = new Date(props.lastMessageTime);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffDays = Math.floor(diffMs / 86400000);
  if (diffDays === 0) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { day: "2-digit", month: "2-digit" });
});
</script>
```

- [ ] **Step 2: Create BaileysConversationList.vue**

Create `desk/src/components/whatsapp/BaileysConversationList.vue`:

```vue
<template>
  <div class="flex w-60 shrink-0 flex-col border-r border-outline-gray-2 bg-surface-gray-1">
    <!-- Header -->
    <div class="border-b border-outline-gray-2 px-4 py-3">
      <h2 class="text-sm font-semibold text-ink-gray-9">WhatsApp</h2>
    </div>

    <!-- Search -->
    <div class="border-b border-outline-gray-2 px-3 py-2">
      <input
        v-model="search"
        type="text"
        placeholder="Search..."
        class="w-full rounded-lg border border-outline-gray-3 bg-surface-white px-3 py-1.5 text-xs text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none"
      />
    </div>

    <!-- List -->
    <div class="flex-1 overflow-y-auto">
      <div v-if="conversations.loading && !conversations.data" class="flex justify-center py-8">
        <LoadingIndicator :scale="5" class="text-ink-gray-5" />
      </div>
      <div
        v-else-if="!filteredList.length"
        class="py-10 text-center text-xs text-ink-gray-5"
      >
        {{ search ? "No results" : "No conversations" }}
      </div>
      <BaileysConversationItem
        v-for="conv in filteredList"
        :key="conv.jid"
        :jid="conv.jid"
        :displayName="conv.display_name"
        :isGroup="conv.is_group"
        :lastMessage="conv.last_message"
        :lastMessageTime="conv.last_message_time"
        :lastDirection="conv.last_direction"
        :contentType="conv.content_type"
        :hasUnread="isUnread(conv)"
        :selected="conv.jid === selectedJid"
        @select="(jid, name) => $emit('select', jid, name)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator } from "frappe-ui";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { globalStore } from "@/stores/globalStore";
import BaileysConversationItem from "./BaileysConversationItem.vue";

const props = defineProps<{ selectedJid: string | null }>();
const emit = defineEmits<{ (e: "select", jid: string, displayName: string): void }>();

const { $socket } = globalStore();
const search = ref("");

const conversations = createResource({
  url: "helpdesk.integrations.baileys.get_baileys_conversations",
  auto: true,
});

const filteredList = computed(() => {
  const list: any[] = conversations.data || [];
  if (!search.value.trim()) return list;
  const q = search.value.toLowerCase();
  return list.filter(
    (c) =>
      (c.display_name || "").toLowerCase().includes(q) ||
      (c.last_message || "").toLowerCase().includes(q)
  );
});

function isUnread(conv: any): boolean {
  if (conv.last_direction !== "Incoming") return false;
  if (conv.jid === props.selectedJid) return false;
  const key = `baileys_last_read_${conv.jid}`;
  const lastRead = localStorage.getItem(key);
  if (!lastRead) return true;
  return new Date(conv.last_message_time) > new Date(lastRead);
}

function handleNewMessage() {
  conversations.reload();
}

onMounted(() => {
  $socket.on("helpdesk:baileys-message", handleNewMessage);
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:baileys-message", handleNewMessage);
});
</script>
```

- [ ] **Step 3: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/components/whatsapp/BaileysConversationItem.vue desk/src/components/whatsapp/BaileysConversationList.vue
git commit -m "feat(ui): BaileysConversationList and BaileysConversationItem components"
```

---

## Task 6: BaileysChat.vue

**Files:**
- Create: `desk/src/components/whatsapp/BaileysChat.vue`

Context: This is the right pane. It reuses `WhatsAppBubble` and `BaileysReplyBox`. It listens to `helpdesk:baileys-message` realtime events filtered by `jid`. It uses `get_baileys_messages(jid=...)` directly. The `displayName` prop comes from the parent `WhatsAppPage`.

- [ ] **Step 1: Create BaileysChat.vue**

Create `desk/src/components/whatsapp/BaileysChat.vue`:

```vue
<template>
  <!-- Empty state -->
  <div
    v-if="!jid"
    class="flex flex-1 flex-col items-center justify-center text-ink-gray-4"
  >
    <WhatsAppIcon class="mb-3 h-14 w-14" />
    <p class="text-sm">Select a conversation</p>
  </div>

  <div v-else class="flex flex-1 flex-col overflow-hidden">
    <!-- Header -->
    <div class="flex items-center gap-3 border-b border-outline-gray-2 bg-surface-gray-1 px-4 py-2.5">
      <WhatsAppIcon class="h-4 w-4 shrink-0 text-green-600" />
      <div class="min-w-0 flex-1">
        <div class="truncate text-sm font-semibold text-ink-gray-9">
          {{ displayName || jid.split("@")[0] }}
        </div>
        <div v-if="phoneDisplay" class="text-[11px] text-ink-gray-5">
          Connected: {{ phoneDisplay }}
        </div>
      </div>
    </div>

    <!-- Messages -->
    <div ref="messagesContainer" class="flex-1 overflow-y-auto bg-[#e5ddd5] px-5 py-4">
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
            <span class="rounded-full bg-surface-white px-2 text-[11px] font-medium text-ink-gray-5">
              {{ dateKey }}
            </span>
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

    <!-- Reply box -->
    <BaileysReplyBox
      ticketId=""
      :jid="jid"
      :replyTo="replyingTo"
      @sent="onMessageSent"
      @clearReply="replyingTo = null"
    />
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator, toast } from "frappe-ui";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { globalStore } from "@/stores/globalStore";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";
import WhatsAppBubble from "./WhatsAppBubble.vue";
import BaileysReplyBox from "./BaileysReplyBox.vue";

const props = defineProps<{
  jid: string | null;
  displayName: string;
}>();

const { $socket } = globalStore();
const messagesContainer = ref<HTMLElement | null>(null);
const replyingTo = ref<Record<string, any> | null>(null);

const connectedPhone = createResource({
  url: "helpdesk.integrations.baileys.get_connected_phone",
  auto: true,
});

const messages = createResource({
  url: "helpdesk.integrations.baileys.get_baileys_messages",
  auto: false,
});

const sendReactionResource = createResource({
  url: "helpdesk.integrations.baileys.send_baileys_reaction",
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send reaction");
  },
});

function loadMessages() {
  if (props.jid) {
    messages.submit({ jid: props.jid });
    localStorage.setItem(`baileys_last_read_${props.jid}`, new Date().toISOString());
  }
}

watch(
  () => props.jid,
  (newJid) => {
    if (newJid) {
      replyingTo.value = null;
      loadMessages();
    }
  },
  { immediate: true }
);

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

function startReply(message: Record<string, any>) {
  replyingTo.value = message;
}

function sendReaction(emoji: string, targetMessageId: string) {
  if (!targetMessageId) {
    toast.error("Cannot react: message has no WhatsApp ID yet");
    return;
  }
  sendReactionResource.submit({ jid: props.jid, target_message_id: targetMessageId, emoji });
}

function onMessageSent() {
  replyingTo.value = null;
  if (props.jid) messages.submit({ jid: props.jid });
  scrollToBottom();
}

function handleRealtimeMessage(data: { jid: string; is_incoming: boolean }) {
  if (data.jid === props.jid) {
    if (props.jid) messages.submit({ jid: props.jid });
    if (data.is_incoming) {
      scrollToBottom();
      localStorage.setItem(`baileys_last_read_${props.jid}`, new Date().toISOString());
    }
  }
}

watch(messageList, () => { scrollToBottom(); });

onMounted(() => {
  $socket.on("helpdesk:baileys-message", handleRealtimeMessage);
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:baileys-message", handleRealtimeMessage);
});
</script>
```

- [ ] **Step 2: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/components/whatsapp/BaileysChat.vue
git commit -m "feat(ui): BaileysChat standalone chat pane component"
```

---

## Task 7: Notification store — bell + sound for Baileys messages

**Files:**
- Modify: `desk/src/stores/notification.ts`

Context: The store already has a `$socket.on("helpdesk:whatsapp-message", ...)` handler. We add a second handler for `helpdesk:baileys-notification` that does the same: reload notifications + play sound.

- [ ] **Step 1: Add `helpdesk:baileys-notification` listener**

In `desk/src/stores/notification.ts`, after the existing `$socket.on("helpdesk:whatsapp-message", ...)` block (around line 151–161), add:

```typescript
  $socket.on("helpdesk:baileys-notification", (data: { jid: string; message: string; sender: string }) => {
    if (isCustomerPortal.value) return;
    resource.reload();
    try {
      const audio = new Audio("/assets/frappe/sounds/alert.mp3");
      audio.volume = 0.4;
      audio.play();
    } catch (_) {}
  });
```

- [ ] **Step 2: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/stores/notification.ts
git commit -m "feat(ui): bell notification on incoming Baileys messages in standalone chat"
```

---

## Task 8: Build and verify

**Files:** None (build only)

- [ ] **Step 1: Build**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | grep -E "ERROR|error TS|✓ built|✓ [0-9]+ modules"
```

Expected:
```
✓ XXXX modules transformed.
✓ built in XXs
```

No `ERROR` or `error TS` lines.

- [ ] **Step 2: Hard refresh and verify sidebar**

After build, hard refresh the helpdesk (Ctrl+Shift+R). Confirm:
- "WhatsApp" link appears in the helpdesk sidebar
- Clicking it navigates to `/helpdesk/whatsapp`
- The three-column layout renders: conversation list on the left (showing existing conversations), empty state on the right

- [ ] **Step 3: Verify conversation list**

In the browser at `/helpdesk/whatsapp`:
- Conversations should appear (loaded from `get_baileys_conversations()`)
- Clicking a conversation highlights it and loads the chat on the right
- Existing messages appear as WhatsApp bubbles (green outgoing, white incoming)

- [ ] **Step 4: Verify reply and reaction**

- Type a message and press Enter → message appears (outgoing bubble)
- Hover a bubble → reply and react buttons appear
- Click reply → green quoted preview appears in input, send → quoted bubble shows
- Click react → emoji picker, click emoji → reaction badge appears

- [ ] **Step 5: Verify notifications**

Send a test webhook to simulate an incoming message:

```bash
curl -s -X POST http://localhost:8002/api/method/helpdesk.integrations.baileys.webhook \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $(bench --site site16.local execute 'import frappe; print(frappe.db.get_single_value("Baileys Gateway Settings", "api_key"))' 2>/dev/null | tail -1)" \
  -d '{"jid":"120363409735363669@g.us","messageId":"test999","sender":"254700000001@s.whatsapp.net","senderName":"Test User","message":"hello standalone","contentType":"text"}'
```

Expected: `{"message":{"status":"ok"}}` and the bell icon updates in the UI.

---

## Cross-Reference: Spec Coverage

| Spec requirement | Task |
|-----------------|------|
| Three-column layout | Task 4 (WhatsAppPage), Task 5 (ConversationList), Task 6 (Chat) |
| All chats by recency, search | Task 5 (BaileysConversationList) |
| Ticket creation removed | Task 1 Step 3 (webhook rewrite) |
| Bell notifications | Task 1 Step 2 (`_notify_agents_baileys`), Task 7 (notification.ts) |
| `get_baileys_conversations()` | Task 1 Step 4 |
| `get_baileys_messages(jid)` | Task 1 Step 5 |
| `send_baileys_reply(jid=...)` | Task 1 Step 6 |
| `send_baileys_reaction(jid=...)` | Task 1 Step 7 |
| `send_baileys_media(jid=...)` | Task 1 Step 8 |
| BaileysReplyBox jid prop | Task 3 |
| Router + sidebar link | Task 4 |
| Realtime updates in chat | Task 6 (`helpdesk:baileys-message` listener) |
| Realtime updates in list | Task 5 (`helpdesk:baileys-message` listener) |
| Existing ticket history preserved | No task needed — TicketActivityPanel unchanged |
| Backend tests | Task 2 |
