# WA Line (Evolution API) — reactions / edit / delete: root cause investigation

Date: 2026-07-17
Status: Phase 1 (root cause) complete. No fixes applied yet.
Server: Evolution API **v2.3.7** @ `https://hd-whatsapp-api.cecypo.tech`

## Method

Probed the live Evolution API for route existence (a route that exists returns a
400/500 from its handler; a missing route returns Express's `Cannot <METHOD> /path`
404). Confirmed each finding against the Evolution API source at
`EvolutionAPI/evolution-api` (`src/api/routes/chat.router.ts`,
`src/validate/chat.schema.ts`, `src/validate/message.schema.ts`).

No destructive calls were made: probes used empty/invalid bodies that fail before
any send.

## Evidence: what the API actually supports

| Capability | Route (verified) | Probe result |
|---|---|---|
| React / clear | `POST /message/sendReaction/{instance}` | exists — 400 lists only `key` errors |
| Edit | `POST /chat/updateMessage/{instance}` | **exists** |
| Edit (what we call) | `PUT /message/updateMessage/{instance}` | **404 `Cannot PUT`** |
| Edit (what we call, as POST) | `POST /message/updateMessage/{instance}` | **404 `Cannot POST`** |
| Delete for everyone | `DELETE /chat/deleteMessageForEveryone/{instance}` | exists — 400 requires `id`, `fromMe`, `remoteJid` |

Source-confirmed schemas:

- `reactionMessageSchema`: `reaction: { type: 'string' }`, `required: ['key','reaction']`,
  **no `isNotEmpty('reaction')`** → `reaction: ""` is valid. **Clearing works at the API level.**
- `updateMessageSchema`: `{ number, text, key{id, remoteJid, fromMe} }`. No top-level
  `required` (only `isNotEmpty`) — which is why `{}` returns 500, not 400.
  **Our existing payload shape is correct; only the route+method are wrong.**
- `deleteMessageSchema`: `{ id, fromMe, remoteJid, participant? }`, required `id`, `fromMe`, `remoteJid`.

## Root causes

### Edit — never worked on this server

- **E1 (blocker)** `wa.py:1477` calls `PUT /message/updateMessage/{instance}`.
  That route does not exist in Evolution v2 (404 on both PUT and POST).
  Correct: **`POST /chat/updateMessage/{instance}`**. Every edit has always thrown
  `WA API edit failed`. The payload (`number`, `key`, `text`) is already correct.
- **E2** Edit UI is wired only in `BaileysGroupChatTab.vue` (`:allowEdit="true"`, `@edit`).
  `BaileysChat.vue` (standalone WA Line chat page) wires only `@react` — no edit, no retry.

### Reactions — cannot clear, and replacing shows both emojis

- **R1 (blocker)** `wa.py:1395` guard `if not jid or not target_message_id or not emoji: throw`
  rejects `emoji=""`, so a clear can never reach the API — even though the API accepts it.
- **R2 (blocker)** `reactionsMap` (BaileysGroupChatTab / BaileysChat / WhatsAppBusinessChat)
  pushes *every* reaction row into an array keyed only by target message id.
  WhatsApp semantics are **one reaction per sender per message, latest wins**.
  Reacting 👍 then ❤️ renders **both** badges instead of replacing.
- **R3** A cleared reaction is stored as a row with `message=""`. The fold skips it
  (`&& m.message`) but the superseded row remains → **stale badge stays forever**.
- **R4** Bubble props expect `reactions: Array<{emoji, type, sender}>` and compute
  `senders[]`/`hasOwn`, but parents supply only `{emoji, type}` — `sender` is never
  populated, so the sender tooltip is always empty and per-sender dedupe is impossible.
- **R5** Reaction badge is `cursor-default` with **no click handler** — no way to toggle
  your own reaction off from the UI (the `hasOwn` highlight styling exists but is inert).
- **R6** `send_wa_reaction` inserts a new row every time; nothing supersedes the old row.

### Delete — not implemented at all

- **D1 (blocker)** There is **no** `delete_wa_message` endpoint in `wa.py`. Agents cannot
  delete anything. The API supports it (`DELETE /chat/deleteMessageForEveryone`).
- **D2** No delete UI: `WhatsAppBubble` has no `delete` emit.
- **D3 (data-correctness)** Inbound `_handle_delete` (`wa.py:1138`) marks the row
  `status="Failed"` because **`WA Message` has no `is_deleted` field**. Consequences:
  a message the customer deleted renders as a red **failed** bubble, and where
  `allowRetry` is on, the agent is offered a **Retry** button that would re-send a
  message that was deliberately deleted. It also corrupts delivery-status analytics
  (`status='Failed'` is used elsewhere).

## Notes / adjacent observations

- `WA Line.connected_user` is `None` for all three lines (`_test-evo`, `KP`, `SV`).
  `_handle_message` falls back to `Administrator` for mirrored-from-phone messages.
  Not a cause of these bugs; flagged separately.
- `_evo_session` retries only idempotent methods; `DELETE` is already in
  `allowed_methods`, and the corrected edit becomes `POST` (no retry) — both correct.

## Proposed fix scope (pending approval)

1. Edit: point at `POST /chat/updateMessage`; wire edit into `BaileysChat.vue`.
2. Reactions: allow `emoji=""` through; fold per-sender latest-wins; populate `sender`;
   make own-reaction badge clickable to toggle/clear.
3. Delete: add `is_deleted` field + `delete_wa_message` endpoint
   (`DELETE /chat/deleteMessageForEveryone`, outgoing-only); render "This message was
   deleted"; stop overloading `status="Failed"`; suppress Retry for deleted rows.
