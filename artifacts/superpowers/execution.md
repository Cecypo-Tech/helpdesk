# Execution log — WABA reactiveness plan (2026-09-05)

Plan: artifacts/superpowers/plan.md

## Phase 1 — Dedicated workers, bot off the short queue

- Files: `~/frappe-bench/Procfile` (outside this repo), `helpdesk/integrations/bot.py`, `helpdesk/tests/test_bot.py`
- Procfile worker split into `worker_short` (queue `short`) and `worker_long` (`default,long`). Takes effect on the next `bench start`; a dedicated `bench worker --queue short` was started in the background meanwhile.
- `bot.process_message` now enqueued on `default` for both WABA and WA Line handlers.
- New test `test_bot_job_stays_off_the_short_queue` (written first, failed with `'short' != 'default'`, passes after the change).
- Verify: `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_bot` → 25 tests OK.
- Production supervisor config not touched (needs a maintenance window; see plan risks).

## Phase 2 — Event payloads, in-place updates, resync

- Backend: `helpdesk/integrations/wa.py` (`_fw_event_payload`, `_publish_fw_message(doc=, origin=)`, `on_whatsapp_message_insert` announces every stored row before the `enabled` gate, `_announce_incoming` passes the doc, `link_incoming_message` never opens a ticket from a reaction, `get_whatsapp_conversations` excludes reactions from the last-message ranking).
- Backend tests: new `helpdesk/tests/test_wa_realtime_events.py` (5, written first, all failed, all pass); `test_a_reaction_is_not_the_last_message` added to `test_wa_conversation_page.py`.
- Frontend: `desk/src/utils/waRealtime.ts` (apply event to list, upsert into thread), `desk/src/utils/socketResync.ts` (refetch on reconnect / tab visible), `desk/src/socket.ts` (reconnect forever, capped backoff), `WhatsAppConversationList.vue` (in-place row move, refetch only when phone unknown, debounced reconcile on ingest), `WhatsAppChatTab.vue` and `WhatsAppBusinessChat.vue` (upsert bubble from event, one debounced reconcile per burst, resync), `WhatsAppBusinessPage.vue` (re-resolve active ticket only for the selected phone on the linked event), `stores/notification.ts` (sound on insert, bell on ingest — no double sound), `TicketActivityPanel.vue` (badge ignores the unlinked insert event).
- Frontend tests: `waRealtime.spec.ts` (14), `socketResync.spec.ts` (5); `yarn -s vitest run` → 52 passed.
- Backend regression: test_wa_conversation_page (14), test_wa_conversation_paging (7), test_wa_webhook_perf (3), test_wa_contact_link (8), test_contact_verification (55), test_wa_normalized_phone (8), test_wa_templates (17), test_wa_outbound_hold (6), test_wa_read_receipt (9), test_wa_unread_state (5) — all OK.
- `bench build --app helpdesk` → built (2889 modules).
- Note: the first realtime-test run died in the runner's ERPNext test-record preload (optimistic lock on tabItem); a rerun passed. Environmental, unrelated to the change.

