# WA Line (Evolution API) — worker and performance review

**Date:** 2026-09-06
**Scope:** the Evolution API path only: webhook, media, notifications, send,
conversation list, frontend listeners.
**Status:** findings complete, plan awaiting approval. No code changed.

## Part 1 — Findings

Most of this path was already tuned in July and August: media download is off
the webhook, the realtime event carries a preview so the list patches in place,
status updates are batched, unread counts have a floor and a per-agent cursor,
and the sidebar badge is bumped locally. What is left is narrower than the WABA
side was, and two of the items undo work that was already done.

**F1. The WhatsApp page refetches the whole list on every message, defeating
the list's own in-place patch.** `WhatsAppPage.vue` listens to
`helpdesk:baileys-message` and calls `convListRef.reload()` (a fresh first page,
which also discards any older pages the agent had loaded) and
`baileysChat.refresh()` (a full thread reload) — for every message on any line,
for every agent with the page open. `BaileysConversationList.vue` handles the
same event by patching its row from the preview precisely to avoid that query,
and `BaileysChat.vue` already reloads itself for its own JID. So today each
message costs one list query per agent and two thread reloads for the open chat.
Deleting the page-level handler restores the intended behaviour.

**F2. Web push is sent synchronously inside the Evolution webhook.**
`_notify_agents` → `_create_wa_notifications` inserts one HD Notification per
active agent; `HD Notification.after_insert` calls `_send_push_notification`,
which loops over the agent's push subscriptions and calls `pywebpush.webpush()`
— an outbound HTTPS request to the browser vendor's push service — with no
timeout, before the webhook returns 200. Evolution blocks on our response, and
the webhook's own comments say that is why media download was moved out. With A
agents and S subscriptions each, that is A×S serial HTTPS calls per unattended
incoming message. The same hook fires for mentions and assignments from agent
requests. Push is bookkeeping; it belongs in a job, with a timeout.

**F3. Media re-download runs on the `short` queue with a 60-second call.**
`_retry_media_download` is enqueued on `short` and calls Evolution's
`getBase64FromMediaMessage` with `timeout=60`. On Frappe Cloud the short workers
are the same ones WABA ingestion depends on; a burst of photos ties them up.
It is a background fetch that nobody is waiting on synchronously — it belongs
on `default`.

**F4. Bulk contact merges run synchronously in the webhook.** A
`contacts.upsert` event (Evolution sends these in bulk on connect) is handled
inline: for each contact, `_merge_lid_into_pn` reads the contact, may `UPDATE`
every WA Message and HD Ticket for the alias, and commits — per contact. One
reconnect with a few hundred contacts holds the webhook open for all of it.
Move the loop into a `long` job.

**F5. The open WA Line chat does not resync after a reconnect.** The list and
the sidebar badge refetch on `connect`; `BaileysChat.vue` (the thread) does not,
so messages that arrived during a drop are missing until the agent switches
chats. `utils/socketResync` from yesterday's work fits directly.

**F6 (minor).** `BaileysGroupChatTab.vue` (the WA Line tab on a ticket)
refetches the full thread and tab info per event. Ticket-scoped, so cheap in
practice; the event would need to carry the row (as the WABA event now does)
to upsert instead. Not planned.

**Fine as they are.** The send path posts to Evolution inside the agent's
request with 15/30-second timeouts, but the composer is optimistic so the
agent is not waiting on it. The Evolution HTTP session retries idempotent
methods only, so no duplicate sends. `_notify_agents` costs two SELECTs and a
COUNT per incoming message. `get_wa_conversations` is paged, and its message
`LIKE` scan runs only for a typed search. The bot moved to `default` yesterday.

## Part 2 — Plan

Small, independent steps; each with a test written first.

### Step 1 — Stop the page-level refetch (F1)

- `WhatsAppPage.vue`: remove the list reload and chat refresh from
  `handleBaileysMessage`; keep the status-update handler. If the list has no
  row for the JID it already falls back to a debounced first-page merge.
- Verify: `bench build --app helpdesk`; manual: send a message, confirm one
  network request at most (the chat's own reload) rather than three.

### Step 2 — Push notifications in a job, with a timeout (F2)

- `hd_notification.py`: `after_insert` enqueues
  `helpdesk.helpdesk.api.push_notifications.send_push_to_user` on `default`
  with `enqueue_after_commit=True`, instead of calling it inline.
- `push_notifications.py`: pass `timeout=10` to `webpush()`.
- Tests: `after_insert` enqueues rather than calls (patch `frappe.enqueue`);
  `send_push_to_user` passes the timeout (patch `pywebpush.webpush`).
- Note: the same change speeds up mention and assignment notifications.

### Step 3 — Media retry off the short queue (F3)

- Both `_retry_media_download` enqueues in `_handle_upsert` → `queue="default"`.
- Test: incoming media message with no inline base64 enqueues on `default`.

### Step 4 — Contact upserts in a job (F4)

- `webhook()` for `contacts.upsert`: enqueue
  `helpdesk.integrations.wa._handle_contacts_upsert` on `long` with the list,
  return `{"status": "queued"}`. The function already commits per contact,
  which is fine in a job.
- Test: the webhook enqueues on `long` and returns immediately.

### Step 5 — Thread resync on reconnect (F5)

- `BaileysChat.vue`: `watchResync($socket, loadMessages)` on mount, dispose on
  unmount.
- Verify: `yarn -s vitest run` (util already covered), `bench build`.

## Acceptance criteria

- One incoming WA Line message causes at most one conversation-list query per
  agent (only when the JID is new to that client), not one per message.
- The Evolution webhook returns without waiting on any push-service HTTP call.
- No `short`-queue job in wa.py makes a call to Evolution.
- A contacts.upsert of any size returns from the webhook immediately.
- The open chat catches up after a socket drop without switching chats.

## Verification commands

```bash
cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_notifications
cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_baileys
cd /home/kushal/frappe-bench/apps/helpdesk/desk && yarn -s vitest run
cd /home/kushal/frappe-bench && bench build --app helpdesk
```
