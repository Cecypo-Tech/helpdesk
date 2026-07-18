# WA Line — customer text replies show without their quote box

Date: 2026-07-18
Branch: `wa-line-reactions-edit-delete`

## Symptom

When a customer replies to (quotes) a message, the reply appears in the helpdesk
chat as a plain message — the "replying to …" quote box is missing. Confirmed by
the user against the helpdesk UI: the message **is** present, only the quote
context is absent. (Earlier "whole message missing" framing was a misread; nothing
is dropped.)

## Root cause (confirmed by live webhook capture)

Evolution API v2 delivers a **plain-text** reply as a bare `conversation` and puts
the quote at the **top-level `data.contextInfo`**, a sibling of `message` — not
inside `message.extendedTextMessage.contextInfo`:

```json
{
  "message": { "conversation": "Quote test gamma" },
  "messageType": "conversation",
  "contextInfo": {
    "quotedMessage": { "conversation": "Brioche bread" },
    "stanzaId": "ACFB212CF299F5146132274A5A84278E"   // the replied-to message id
  }
}
```

`_handle_upsert` only looked for `contextInfo` **inside** `message` (the media
sub-messages and `extendedTextMessage`). So for text replies it never found the
`stanzaId`, stored `reply_to_message_id=''`, and the UI had nothing to render.

Media replies were unaffected because their `contextInfo` **is** nested inside the
media sub-message — which is exactly why only *text* replies lost their quote box.

## How it was found

Backend tracing added to `webhook()`/`_handle_upsert` dumped the full `data` object
for every text message. A live quoted reply ("quote test gamma") showed the quote
sitting at `data.contextInfo.stanzaId`, matching the target message's id. All
tracing was removed after the finding.

Incidental discoveries (not fixed here, noted for later):
- The helpdesk line is **SV** (webhook → `helpdesk.integrations.wa.webhook`); **KP**
  belongs to the separate `frappe_whatsapp_evo` invoice app.
- Testing from the user's own phone double-stores each message (Incoming on KP,
  Outgoing on SV) via multi-device echo — a test artifact, not a real-customer path.

## Fix

New pure helper `_extract_reply_target(data, raw_msg, content_type)` that checks, in
order: reaction key id → `extendedTextMessage`/media `contextInfo` → **top-level
`data.contextInfo`**. `_handle_upsert` now calls it. One added fallback line carries
the fix; message-scoped context still wins over the top-level fallback.

## Verification

- `test_wa_reply_target` — 8 unit tests from the real captured payloads. The
  load-bearing test fails without the `data.contextInfo` fallback (verified).
- Full WA suite green: reply_target 8, reactions 6, edit_delete 14, message_by_id 4,
  pagination 12, thumbnails 17.
- Live confirmation pending: send a fresh quoted reply, confirm the quote box renders.

## Follow-up

- **Backfill**: messages already stored with empty `reply_to_message_id` (e.g.
  "How long?", "Bread.") won't gain a quote box retroactively. A one-off backfill
  could re-derive `reply_to` from stored `raw_message` where present, but text rows
  don't store `raw_message`, so most historical text replies can't be recovered.
  New replies are fixed from now on.
