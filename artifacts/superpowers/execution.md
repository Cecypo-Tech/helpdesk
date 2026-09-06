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

## Phase 3 — Optimistic WABA send

- Files: `desk/src/utils/waOptimistic.ts` (new: pending bubble model, merge, resolve, retry), `desk/src/utils/__tests__/waOptimistic.spec.ts` (11 tests), `WhatsAppReplyBox.vue` (pending bubble on send, held text shown as one growing bubble, undo removes it, media bubbles with blob preview, failed sends kept with Retry, remaining files restored to the composer on a failure), `WhatsAppChatTab.vue` and `WhatsAppBusinessChat.vue` (pending bubbles kept apart from the fetched thread and merged for render, resolve/remove/retry handlers, no thread refetch on `delivered`).
- Verify: `yarn -s vitest run` → 63 passed; `bench build --app helpdesk` → built (2890 modules).
- Manual check still required on a real WABA ticket (integration `enabled` is 0 on this site).

## Phase 4 — Company search

- Files: `helpdesk/integrations/wa.py` (`_waba_search_phones` also matches the ticket's customer, `Contact.company_name`, and contacts linked to an HD Customer), `desk/src/components/whatsapp/WhatsAppConversationList.vue` (placeholder), `helpdesk/tests/test_wa_conversation_page.py` (2 new tests, failed first, pass after).
- Verify: `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_conversation_page` → 16 tests OK.

## Phase 5 — Bot hygiene

- `helpdesk/integrations/llm.py`: `request_timeout()` from the new `llm_timeout_seconds` setting (default 30 s), Anthropic client built with `timeout` + `max_retries=2`, Gemini calls carry `request_options={"timeout": …}`, model id `claude-haiku-4-5`; `embeddings.embed` bounded the same way.
- `helpdesk_bot_settings.json`: `llm_timeout_seconds` (Int, default 30). `helpdesk_bot_settings.py`: `test_connection` uses the shared client/model constants (the "Gemini Flash 2.0" fallback matched no branch).
- `helpdesk/integrations/bot.py`: gap suggestion runs in its own job (`record_kb_gap`, queue `default`) after the reply; reactions and button rows no longer feed the model as turns; a read receipt + typing indicator goes out before the searches and model call on WABA.
- `helpdesk/integrations/wa.py`: `_post_wa_read_receipt(typing=)`, `send_wa_typing_indicator`, and `mark_wa_messages_read` now hands the receipts to `_send_wa_read_receipts` (job, deduplicated per ticket) instead of posting to Meta inside the agent's request.
- Tests (written first, failed, then pass): test_llm (7), test_bot (27), test_wa_read_receipt (13). Existing receipt tests now call the job body directly.
- `bench --site dev.localhost reload-doc helpdesk doctype helpdesk_bot_settings` run so the field exists on this site; other sites pick it up on `bench migrate`.

## Finish

- Final backend run: 16 modules, 197 tests, all OK. `yarn -s vitest run`: 63 passed. `bench build --app helpdesk`: built.
- Review pass and follow-ups in `artifacts/superpowers/finish.md`.
- Phase 6 (optional) not done.


# Execution log — WA Line worker/performance plan (2026-09-06)

Plan: artifacts/superpowers/plan.md

## Step 1 — Stop the page-level refetch (F1)

- File: `desk/src/pages/whatsapp/WhatsAppPage.vue` — removed the `helpdesk:baileys-message` handler that reloaded the whole list and refreshed the open chat on every event; the list and chat components already handle it themselves. Status-update handler kept.
- Verify: `bench build --app helpdesk` → built. Manual: one message should now cause at most the chat's own reload.

