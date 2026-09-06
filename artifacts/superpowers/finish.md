# Finish — WA Line (Evolution API) worker and performance fixes

**Date:** 2026-09-06
**Branch:** `perf/wa-line-workers` (off `develop`), 3 commits
**Plan:** `artifacts/superpowers/plan.md` · **Log:** `artifacts/superpowers/execution.md`

## Summary of changes

1. **WhatsApp page no longer refetches the list per message.** The page-level
   `helpdesk:baileys-message` handler reloaded the whole conversation list and
   the open thread on every event for every agent; both components already
   handle the event themselves. Removed.
2. **Web push runs in a job with a timeout.** `HD Notification.after_insert`
   enqueues `send_push_to_user` on `default` after commit instead of calling
   the push service inline; `webpush()` gets `timeout=10`. Applies to WhatsApp,
   mention and assignment notifications alike.
3. **Media re-download moved from `short` to `default`.** It is a 60-second
   call to Evolution; the short workers are what WABA ingestion waits on.
4. **Bulk `contacts.upsert` events are queued on `long`.** The webhook returns
   `{"status": "queued"}` immediately instead of merging contacts (with a commit
   each) while Evolution waits.
5. **Open WA Line thread resyncs after a reconnect** and when the tab becomes
   visible, like the list and sidebar already did.

F6 (the ticket-tab full refetch per event) was noted as minor and not done.

## Verification

| Command | Result |
|---|---|
| `run-tests --module helpdesk.tests.test_push_notifications` | 2 OK (new, failed first) |
| `run-tests --module helpdesk.tests.test_wa_line_jobs` | 2 OK (new, failed first) |
| `run-tests --module helpdesk.tests.test_wa_notifications` | 8 OK |
| `run-tests --module helpdesk.tests.test_wa_evolution_contact_sync` | 21 OK |
| `run-tests --module helpdesk.tests.test_wa_contact_dedupe` | 11 OK |
| `run-tests --module helpdesk.tests.test_baileys` | 2 skipped (pre-existing skips) |
| `cd desk && yarn -s vitest run` | 63 passed |
| `bench build --app helpdesk` | built |

## Review pass

**Blocker** — none.

**Major** — none.

**Minor**
- `contacts.upsert` now returns before anything is written, so a contact name
  from a bulk event shows up a few seconds later than before. Names from
  message `pushName` still land inline via `_upsert_contact_name`.
- Push delivery is now at the mercy of the `default` worker's backlog; on a
  busy bench a push can trail the bell by seconds. Acceptable for a
  notification that already had no delivery guarantee.
- `_send_push_notification()` is kept only for the existing deep-link test
  and any manual call; production goes through the job.

**Nit**
- The test for the contacts event builds a fake request with werkzeug to
  reach `webhook()`; a small dispatch helper would make that cleaner.

## Follow-ups

- F6 if wanted: have `_publish_wa_event` carry the full row so
  `BaileysGroupChatTab` can upsert instead of refetching.
- The `default` queue now carries the bot, push, media retries and read
  receipts. On Frappe Cloud, watch `/app/rq-job` for a growing `default`
  backlog and raise the worker count if it appears.

## Manual validation steps

1. Open `/helpdesk/whatsapp/<line>` with the network tab open; receive a
   message on a chat that is not selected. Expect no request to
   `get_wa_conversations` (row patches in place) and none to
   `get_whatsapp_messages`.
2. Select the chat; receive another message. Expect exactly one
   `get_whatsapp_messages` request.
3. Drop the network for 30 s with a chat open, then reconnect. Expect the
   thread to catch up without switching chats.
4. Enable push for an agent, send an unattended message. Expect the push to
   arrive from the worker (Error Log stays empty; `/app/rq-job` shows a
   completed `send_push_to_user`).
