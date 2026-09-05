# Finish — WABA reactiveness, contact list, company search, AI bot

**Date:** 2026-09-05
**Branch:** `feat/waba-reactiveness` (off `develop`), 5 commits
**Plan:** `artifacts/superpowers/plan.md` · **Log:** `artifacts/superpowers/execution.md`

## Summary of changes

1. **Dedicated workers.** Procfile (bench root, outside this repo) now runs a
   `short`-only worker and a `default,long` worker, so inbound WhatsApp
   ingestion never waits behind embeddings, ERPNext sync, POS invoices or
   old-message syncs. The bot job moved from `short` to `default`.
2. **Realtime events carry the message.** Every stored WhatsApp Business
   message is announced, ticket-linked or not, integration enabled or not.
   The conversation list moves its row in place and the thread appends the
   bubble; one debounced reconcile per burst replaces the two full refetches
   per message per agent. The socket reconnects indefinitely, and the views
   refetch on reconnect and when the tab regains focus. Reactions no longer
   count as a conversation's last message or open tickets of their own.
3. **Optimistic WABA send.** A pending bubble appears the moment the agent
   hits send (held text shows as one growing bubble; undo removes it), is
   resolved on the response, and stays as Failed with Retry on error.
4. **Company search.** The list search now matches the ticket's customer, a
   contact's company name, and contacts linked to an HD Customer.
5. **Bot hygiene.** Every model and embedding call is bounded by a new
   `Request Timeout (Seconds)` setting (default 30); the gap suggestion runs
   in its own job after the reply; read receipts run in a job instead of the
   agent's request; a typing indicator goes out before the bot's searches;
   reactions and button ids no longer reach the model; Haiku is addressed by
   its current id.

Phase 6 (WABA thumbnails, Meta media-by-id) was optional and was not done.

## Verification

| Command | Result |
|---|---|
| `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.<m>` for test_wa_realtime_events, test_wa_conversation_page, test_wa_conversation_paging, test_bot, test_llm, test_wa_read_receipt, test_wa_outbound_hold, test_wa_templates, test_contact_verification, test_wa_webhook_perf, test_wa_contact_link, test_wa_normalized_phone, test_wa_unread_state, test_wa_notifications, test_bot_product_scoping, test_kb_gap_product | all 16 modules OK, 197 tests (5, 16, 7, 27, 7, 13, 6, 17, 55, 3, 8, 8, 5, 8, 9, 3) |
| `cd desk && yarn -s vitest run` | 5 files, 63 tests passed |
| `bench build --app helpdesk` | built, 2890 modules (pre-existing CSS warning only) |
| `bench --site dev.localhost reload-doc helpdesk doctype helpdesk_bot_settings` | field `llm_timeout_seconds` present |

New tests, each written before its change and seen failing first: 5 realtime
event tests, 3 conversation-page tests, 3 bot tests, 4 LLM tests, 4 read-receipt
tests, 30 frontend unit tests (waRealtime 14, socketResync 5, waOptimistic 11).

One run of the realtime module died in the test runner's ERPNext record
preload (optimistic lock on tabItem); the rerun and every later run passed.

## Review pass

**Blocker** — none.

**Major**
- Production process manager not touched. The Procfile change only affects
  `bench start`; the supervisor/systemd config on the production host needs the
  same split (`bench worker --queue short` on its own) or Phase 1 does nothing
  there. The `bench start` on this box also needs a restart to pick up the
  Procfile; a dedicated short worker was started by hand in the meantime and
  the honcho-managed combined worker is still running old code, so until the
  restart some jobs may run pre-change code.
- Not verified end to end on a real WhatsApp conversation. The integration is
  `enabled = 0` on this site and there is no test number wired up. Every path
  is covered by tests at the boundary, but the browser flow (pending bubble →
  resolve, list moving in place, resync after sleep) needs one manual pass.

**Minor**
- An incoming message from an unknown number under "Skip Ticket Creation" now
  plays the alert sound (it is announced like any other row). It was silent
  before only because it was invisible; if that is unwanted, gate the sound on
  `ticket` in `stores/notification.ts`.
- A WABA image bubble from the "insert" event renders as a media placeholder
  until the "ingest" event or the reconcile brings the attachment, because
  upstream frappe_whatsapp attaches the file after inserting the row.
- `mark_wa_messages_read` returns the number of rows handed to the job rather
  than the number marked; no caller reads the value.
- `google-generativeai` is the deprecated SDK; the timeout was added on it
  rather than migrating to `google-genai`. Separate change.

**Nit**
- `_send_wa_read_receipts` and `send_wa_typing_indicator` both post a read
  receipt for the same message when the bot answered first; Meta accepts the
  duplicate.
- `wa.py` mixes tabs and spaces across regions (pre-existing); new code
  matches whichever region it sits in.

## Follow-ups

- Restart `bench start` here; apply the worker split to production.
- Manual pass on a real WABA ticket with `enabled = 1`.
- Phase 6 if wanted: thumbnails for WABA images via the existing
  `_save_thumbnail_file`; Meta media upload by id needs upstream
  frappe_whatsapp changes.
- Migrate `google-generativeai` → `google-genai`.

## Manual validation steps

1. `WhatsApp Helpdesk Settings` → Enable WABA Ticket Auto-Creation, save.
2. Open `/helpdesk/whatsapp-business`; from a test phone send a message.
   Expect: the list row moves to the top with the text within a second, the
   thread (if that phone is open) shows the bubble without a spinner.
3. Reply from the composer. Expect: bubble appears at once with a clock,
   settles to a tick on the response; with `outbound_hold_seconds > 0` the
   bubble shows the merged text and the strip counts down; Undo removes it.
4. Disconnect the network for 30 s, reconnect. Expect: the list and thread
   catch up without a refresh (watch the network tab for one refetch).
5. Search the list for part of a company name shown on a row.
6. `Helpdesk Bot Settings` → set Request Timeout to 1, enable the bot, send a
   question. Expect: an escalation message, an Error Log with the timeout,
   and the worker free again within seconds.
