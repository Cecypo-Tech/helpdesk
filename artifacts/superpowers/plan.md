# WABA reactiveness, contact list, company search, AI bot — review and plan

**Date:** 2026-09-05
**Scope:** WhatsApp Business API (frappe_whatsapp) path only, plus the shared bot.
**Status:** findings complete, plan awaiting approval. No code changed.

## Part 1 — Findings (root causes, with evidence)

### A. Why the WABA chat feels slow, even with `outbound_hold_seconds = 0`

**A1. One background worker serves every queue.** `sites/common_site_config.json`
has `background_workers: 1` and the Procfile runs a single
`bench worker --queue short,default,long`. Inbound WABA ingestion
(`wa_ingest.process_incoming_message`, queue `short`), the bot
(`bot.process_message`, queue `short`) and every long job (daily embeddings,
hourly ERPNext sync, klik_pos invoice jobs, `sync_wa_old_messages`, backups)
share that one worker. `logs/worker.log` shows the same worker id running the
`long` queue. Any long job in progress delays every incoming WhatsApp message
until it finishes. This is the single biggest latency source for *incoming*.

**A2. Sending is synchronous end to end and never optimistic.** The agent's
request runs `_send_fw_reply` → `WhatsAppMessage.before_insert` →
`make_post_request` to Meta with **no HTTP timeout** (frappe's
`integrations/utils.make_request` sets none) → response → then the browser
refetches the *whole* thread and ticket info twice (once from the `delivered`
emit, once from the socket event). The outgoing bubble appears only after Meta
has answered. The WA Line tab already has optimistic bubbles (shipped
2026-07-17, `BaileysReplyBox` `optimistic` / `optimistic-resolve` /
`optimistic-remove`); the WABA tab does not.

**A3. Realtime events carry no payload, so every client refetches.**
`helpdesk:whatsapp-message` is `{ticket, is_incoming}` broadcast to room `all`.
On each event every open agent session refetches the full thread, ticket info,
and, on the Business page, the first conversation page. That page query runs a
window function over the whole `tabWhatsApp Message` table twice and then
loads **every Contact with a phone** (`frappe.get_all("Contact", or_filters=…)`
is not filtered by the page's phones despite the comment). Cost scales with
agents × messages.

**A4. The socket dies quietly and nothing resyncs.** `desk/src/socket.ts` sets
`reconnectionAttempts: 5`. After a laptop sleep or a tunnel hiccup the client
stops reconnecting for good; there is no refetch on reconnect, no refetch on
tab visibility, and no polling fallback. From then on neither the thread nor
the list updates until a hard refresh. Intermittent, which matches "doesn't
always reflect".

**A5. The bot can park the only worker for minutes.** `bot.process_message`
makes up to six serial network calls (article embedding, resolved-ticket
embedding, Outline search, gap-tracking LLM call, main LLM call, Meta send).
The Anthropic client uses the SDK default 10-minute timeout; the Gemini calls
set none. A slow LLM response blocks ingestion of every other incoming message
on the site (A1). The gap-tracking LLM call also runs *before* the customer
gets their reply.

**A6. Media.** Inbound media is downloaded from Meta synchronously inside
Meta's webhook request by upstream `frappe_whatsapp` (two HTTP GETs, no
timeout) before the row is inserted. Outbound media is stored as a public File
and sent to Meta as a link, which Meta must fetch back through the Cloudflare
tunnel. WABA image bubbles load the full-size original because only the WA
Line path sets `thumbnail_url`.

**A7. Environment on this box.** `dev.cecypo.tech` is a Cloudflare tunnel to
`localhost:8000`, i.e. the Werkzeug dev server, not gunicorn behind nginx. And
`WhatsApp Helpdesk Settings.enabled` is currently **0** on `dev.localhost`,
which makes `on_whatsapp_message_insert` return before it publishes anything or
enqueues ingestion. On this site, right now, nothing WABA-related is reactive
by construction. (The five incoming rows in the table are all unlinked.)

**A8. Read receipts run in the agent's request.** `mark_wa_messages_read`
posts to Meta once per unread message with a 10 s timeout, from the request
the chat tab fires on mount and on every incoming message.

### B. Why the contact list's last-message line is sometimes stale

**B1 (confirmed in code).** The list refreshes only on
`helpdesk:whatsapp-message`, and that event is published only for
*ticket-linked* messages (`_publish_fw_message` is reached from
`_announce_incoming` and from the outgoing branch, both of which require
`reference_name`). So none of these ever notify the list:
- an incoming message from an unknown number when `unknown_contact_action` is
  "Skip Ticket Creation" (the current setting),
- any message while `enabled = 0` (A7),
- an outgoing row created without a ticket reference (frappe_whatsapp desk UI,
  bulk sends).
The SQL still picks that row as the phone's newest message, so the next
refresh shows it, but nothing triggers that refresh.

**B2.** A dead socket (A4) produces the same symptom for every message.

**B3 (minor).** An incoming reaction becomes the conversation's "last message"
(an emoji), flips `last_direction` to Incoming and therefore shows the unread
dot and lands in "Awaiting reply". Also, when a customer reacts after their
previous ticket was resolved, `link_incoming_message` raises a brand-new ticket
from the reaction.

### C. Company name is not searchable

`_waba_search_phones` matches `Contact.first_name`, `last_name` and `name`
only. The company shown on each row is `HD Ticket.customer` (resolved from the
phone's newest ticket-linked message). Neither that nor `Contact.company_name`
is searched.

### D. AI bot wiring

What is right and should stay: dispatch from the ingestion job rather than a
doc hook (so `reference_name` exists), idempotent job ids, `system=True` sends
so the bot never claims a ticket or moves it out of Open, escalation on any
LLM failure, skip when an agent is assigned, KB-only prompt with product and
category scoping.

Obvious improvements:
- **D1** Bot job shares the single worker (A1, A5). Give it its own queue, or
  at least `default`, once a dedicated `short` worker exists.
- **D2** No timeouts or bounded retries on LLM and embedding calls (project
  rule 5). Anthropic: `Anthropic(timeout=…, max_retries=…)`. Gemini:
  `request_options={"timeout": …}`.
- **D3** Gap-tracking LLM call runs inline before the reply; defer it to its
  own job after the reply is sent.
- **D4** No customer-facing feedback while the bot thinks. Meta's Cloud API
  accepts a `typing_indicator` alongside the read receipt on the messages
  endpoint; one POST before the LLM call makes the wait feel intentional.
- **D5** SDK and model hygiene: `google-generativeai` 0.8.6 is the deprecated
  SDK (replacement is `google-genai`); `anthropic` is 0.120 while 1.x exists;
  the Haiku id is the dated `claude-haiku-4-5-20251001` (current id is
  `claude-haiku-4-5`); `test_connection` falls back to the string
  "Gemini Flash 2.0", which matches no provider branch.
- **D6** Conversation history feeds reaction and button rows to the model as
  turns; only `image` attachments are passed (documents ignored); the Anthropic
  path labels every image `image/jpeg` regardless of actual type.
- **D7** Read receipts (A8) belong in a job, not the agent's request.

## Part 2 — Plan

Each phase is independently mergeable and verifiable. Phases 1, 2 and 4
address the three reported symptoms directly; 3 and 5 are the "better way".

### Phase 1 — Dedicated workers (config only, no app code)

1. Procfile: replace the single worker line with
   `worker_short: bench worker --queue short` and
   `worker_long: bench worker --queue default,long`. Mirror in the production
   supervisor config (ask before touching production).
2. Move `bot.process_message` enqueues to queue `default` so a slow LLM never
   sits in front of ingestion.
3. Verify: `ps aux | grep "worker --queue"` shows two workers; enqueue a
   deliberately slow `long` job and confirm a `short` job still completes
   within a second.

### Phase 2 — Event payloads, in-place updates, resync (fixes B1, B2, most of A3/A4)

Backend (`wa.py`, `wa_ingest.py`):
1. `_publish_fw_message` gains a payload: `phone` (normalized), `name`,
   `type`, `content_type`, `message` (first 120 chars), `creation`, `status`,
   `attach`, `profile_name`, `ticket` (may be empty).
2. Publish for **every** stored WhatsApp Message, not only ticket-linked ones:
   in `on_whatsapp_message_insert`, emit an `after_commit` event before the
   `enabled` gate and before the unknown-contact skip. Keep the job's
   `immediate=True` publish for the moment a ticket link exists (the thread
   view needs that one). Dedupe on the client by `name`.
3. Tests (`test_wa_notifications.py` or new `test_wa_realtime_payload.py`):
   an unlinked incoming message publishes; an outgoing message publishes with
   the phone; payload shape is stable.

Frontend:
4. `WhatsAppConversationList.vue`: apply the event in place — update
   `last_message`, `last_message_time`, `last_direction`, move the row to the
   top; refetch page 1 only when the phone is not loaded. Extract the merge
   into `desk/src/utils/waConversationList.ts` with a vitest spec.
5. `WhatsAppChatTab.vue` and `WhatsAppBusinessChat.vue`: when the payload's
   ticket or phone matches, append or upsert the row by `name` instead of
   refetching; keep one debounced refetch as a safety net.
6. `socket.ts`: `reconnectionAttempts: Infinity` with capped delay; export a
   `onReconnect` hook. Both chat views and the list refetch on `connect` after
   a disconnect and on `document.visibilitychange` → visible.
7. Ignore reaction rows when computing the list's last message and unread
   state (B3, backend: exclude `content_type = 'reaction'` from the
   `ROW_NUMBER` source; `link_incoming_message`: never open a new ticket from a
   reaction).

### Phase 3 — Optimistic WABA send (fixes A2)

1. Port the `optimistic` / `optimistic-resolve` / `optimistic-remove` emits
   from `BaileysReplyBox.vue` into `WhatsAppReplyBox.vue`, including the
   outbound-hold path (the pending bubble shows while held, with the existing
   countdown strip), and the media path (blob preview immediately).
2. `WhatsAppChatTab.vue` and `WhatsAppBusinessChat.vue` render the pending
   bubble (existing `status: "Pending"` clock in `WhatsAppBubble`) and resolve
   it to the real docname; drop the double refetch on `delivered`.
3. A failed send keeps the bubble with a Retry affordance instead of a toast
   only.
4. Verify with the existing vitest suites plus a new spec for the resolve /
   dedupe helper; manual check on a real WABA ticket.

### Phase 4 — Company search (fixes C)

1. `_waba_search_phones`: add phones from (a) `HD Ticket.customer LIKE` via
   `WhatsApp Message.normalized_phone` on ticket-linked rows, and (b)
   `Contact.company_name LIKE` through the existing contact phone resolution.
2. Test in `test_wa_conversation_page.py`: a conversation whose ticket's
   customer is "Acme Ltd" is found by "acme"; a contact with
   `company_name` set is found by it.
3. Placeholder text in the list becomes "Search name, company, phone…".

### Phase 5 — Bot hygiene (D1–D7)

1. `llm.py`: client timeouts and bounded retries for both providers; read
   them from `Helpdesk Bot Settings` (`llm_timeout_seconds`, default 30).
   Embedding calls get the same timeout.
2. Update the Anthropic model id to `claude-haiku-4-5`; fix the
   `test_connection` fallback string. (Moving off `google-generativeai` to
   `google-genai` is a separate change; note only.)
3. Defer `_handle_kb_gap` to its own job after the reply is sent.
4. Send a read receipt + typing indicator before the LLM call (one POST, via
   `_post_wa_read_receipt` extended with `typing_indicator`), guarded by
   `allow_auto_read_receipt`.
5. Move `mark_wa_messages_read`'s Meta calls into a job; the endpoint updates
   local state and returns immediately.
6. Filter reaction and button rows out of `_get_conversation_history`.
7. Tests: timeout is passed to the client (mocked); gap job is enqueued rather
   than awaited; history excludes reactions.

### Phase 6 — Media (later, optional)

- Generate `thumbnail_url` for WABA images in the ingestion job using the
  existing `_save_thumbnail_file`, and return it from `get_whatsapp_messages`.
- Uploading outbound media to Meta by id instead of a public link, and moving
  inbound download out of the webhook, both require changes in upstream
  `frappe_whatsapp`, which this bench tracks without a fork. Not planned.

## Acceptance criteria

- An incoming WABA message from any number (linked or not) updates the
  conversation list row within a second of the webhook, with no long job able
  to delay it.
- An agent's outgoing text appears in the thread immediately as a pending
  bubble and settles to sent/delivered without a full refetch.
- After a socket disconnect and reconnect, or the tab regaining focus, the
  thread and list are current without a hard refresh.
- Typing part of a company name in the list search returns that company's
  conversations.
- A bot reply never blocks ingestion of other messages, and every LLM call
  has a timeout.

## Verification commands

```bash
cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_conversation_page
cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_notifications
cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_bot
cd /home/kushal/frappe-bench/apps/helpdesk/desk && yarn test:unit
cd /home/kushal/frappe-bench && bench build --app helpdesk
```

## Risks and open questions

- Phase 1 touches the production process manager; needs a maintenance window
  and confirmation of where production runs (this box serves
  `dev.cecypo.tech` through cloudflared to the dev server).
- Publishing an event for unlinked incoming messages exposes a phone number
  and preview to every logged-in agent's socket, same as the list already
  does; `restrict_chats_by_team` is not applied to realtime today and would
  need to be if it is ever turned on.
- The WABA `enabled` flag on dev is off; end-to-end verification needs it on
  with a test number.
