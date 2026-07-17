# WA Line (Evolution API) — Reply-Quote Robustness, Multi-File Upload, Optimistic Send

**Date:** 2026-07-17
**Author:** Kushal (with Claude)
**Status:** Approved design — pending spec review

## Scope

Three independent improvements to the WA Line (Evolution API) chat surface. They
share files but are otherwise decoupled and can ship/verify separately.

1. **Reply-quote robustness** — a customer's reply shows their text but not the
   message they quoted.
2. **Multi-file attachments** — allow selecting/dropping/pasting several files at
   once (currently limited to one).
3. **Optimistic send + client-side image downscaling** — make uploads feel
   instant and actually transfer faster.

Out of scope: the WABA (`frappe_whatsapp`) path, background-worker send offload
(explicitly deprioritised), any change to `wa.py` send/webhook semantics beyond
what Item 1's defensive endpoint requires.

---

## Item 1 — Reply-quote robustness

### Investigation summary (what is NOT broken)

Verified against the live dev database (`dev.localhost`, taking real traffic):

- Incoming replies **store `reply_to_message_id` correctly** for text and all
  media types (webhook extraction in `_handle_upsert`).
- 40 incoming replies carry a quote; **38 targets exist** in `tabWA Message`. The
  2 misses are mid-June messages whose quoted target was never captured.
- `get_whatsapp_messages` returns the quoted target for **all 38** even in the
  single-page realtime path (`reply_targets` list or on-page bubble).
- `BaileysGroupChatTab.vue`, `BaileysChat.vue`, `WhatsAppBubble.vue`, and
  `_finalize_wa_rows` are wired correctly; `type`, `attach`, `is_reply` all set.

Conclusion: the data pipeline works on this DB. When a target truly can't be
resolved, `WhatsAppBubble` renders the placeholder **"Original message not
available"** with a "…" sender — the box shows but empty. This matches the
reported symptom, so the real cause is one of: (a) target genuinely absent from
DB, (b) stale frontend build on the affected environment, or (c) production DB
diverges (migrations/indexes not applied).

### Decision

**Defensive fix now + live reproduction by the user** to confirm root cause
before declaring it fixed. We do not ship a blind fix for a verified-working
pipeline.

### Defensive change

- **New whitelisted endpoint** `get_wa_message_by_message_id(message_id, jid)` in
  `helpdesk/integrations/wa.py`: returns a single finalized WA Message row (via
  the same `_select_messages` + `_finalize_wa_rows` shape) or `None`. Filtered by
  `jid` to stay within the conversation. Wrapped in the standard
  try/except for the missing-custom-field case.
- **Frontend on-demand resolution** in both `BaileysGroupChatTab.vue` and
  `BaileysChat.vue`: when a rendered reply has `reply_to_message_id` but
  `messageByMsgId[...]` misses, fetch the target once via the new endpoint, cache
  it into `replyTargets`, and let the existing computed resolve it. Guard against
  refetch loops (track in-flight/failed ids in a `Set`).
- **Cleaner fallback** in `WhatsAppBubble.vue`: when the target is confirmed
  absent (fetch returned null), show a muted italic "Replied to an earlier
  message" instead of the blunt "Original message not available".

### Acceptance

- A reply whose target is in the DB but outside the loaded page/targets resolves
  its preview after the on-demand fetch (no "not available" flash persisting).
- A reply whose target is genuinely absent shows the muted fallback, not a blank
  box.
- No refetch loop (each missing id fetched at most once per conversation view).
- User reproduces one real failing case; we capture the message row and confirm
  which cause (a/b/c) it was.

---

## Item 2 — Multi-file attachments

### Current state

`BaileysReplyBox.vue` holds a single `attachment: File | null` and reads
`files?.[0]` in `onFileSelected`, `onDrop`, and `onPaste`. The `<input>` has no
`multiple`. `send()` uploads the one file then sends one WA Message.

### Change

- Replace `attachment`/`attachmentPreview` with `attachments: Attachment[]`
  where `Attachment = { file: File; previewUrl: string; id: string }`.
  Use `URL.createObjectURL` for image previews (revoke on clear/unmount);
  non-images get a filename chip.
- Add `multiple` to the file `<input>`; `accept` unchanged.
- `onFileSelected`, `onDrop`, `onPaste` iterate **all** files and append.
- Preview area renders one removable chip/thumb per attachment; each removable
  independently. A "clear all" remains.
- `send()` sends **one WA Message per file, sequentially**, preserving order:
  - Caption (typed text) attaches to the **first** file's message only; the rest
    are sent bare. Matches WhatsApp album behaviour ("shared caption").
  - `reply_to_message_id` (if replying) attaches to the first message only.
  - A text-only send (no files) is unchanged.
  - Per-file upload failure: stop, restore the *unsent* remaining files +
    caption to the composer, surface the banner. Already-sent files stay sent.

### Acceptance

- Selecting/dropping/pasting N files shows N preview chips; each removable.
- Sending delivers N ordered WA Messages; caption only on the first.
- Single-file and text-only behaviour unchanged.
- Object URLs revoked (no leak) on clear/send/unmount.

---

## Item 3 — Optimistic send + client-side image downscaling

### Optimistic bubble

- On `send()`, before the upload starts, the reply box emits an **optimistic
  message** per outgoing item to the parent: `{ name: temp-<uuid>, type:
  "Outgoing", content_type, message, attach: previewUrl, status: "Sending",
  _optimistic: true, creation: now }`.
- Parent (`BaileysGroupChatTab.vue` / `BaileysChat.vue`) inserts optimistic rows
  into `loadedMessages` so they render immediately at the bottom with a spinner /
  "sending…" affordance (reuse `WhatsAppBubble` status styling; add a `Sending`
  state).
- Reconciliation: when `messages.reload()` (triggered by the send's realtime
  event) brings the real rows, drop all `_optimistic` rows for that conversation.
  `mergeMessages` keys on `message_id || name`; optimistic temp names never
  collide with real ones, so we clear them explicitly on successful reload.
- On send failure (pre-send hard error), the optimistic row is removed and the
  existing banner/restore path runs. (A *send* failure that persists as a
  "Failed" WA Message already renders via the normal reload with Retry.)

### Client-side image downscaling

- Before upload, if the file is an image over a threshold, downscale via an
  offscreen `<canvas>`: max long edge ~1600px, JPEG quality ~0.8, skip if the
  result isn't meaningfully smaller (mirrors the server thumbnail cost-benefit
  guard). Non-images and already-small images pass through untouched.
- Downscaling happens per file inside the existing upload step; the optimistic
  preview can use the original object URL (visual parity).

### Acceptance

- Sending a file shows a pending bubble instantly (before upload completes).
- The pending bubble is replaced by the real message on reload — no duplicate.
- A large photo uploads visibly faster; a small image/PDF is unchanged.
- Failure removes the pending bubble and shows the banner (pre-send) or a Failed
  bubble with Retry (send).

---

## Files touched

- `helpdesk/integrations/wa.py` — new `get_wa_message_by_message_id` endpoint (Item 1).
- `desk/src/components/whatsapp/BaileysReplyBox.vue` — multi-file, optimistic
  emit, image downscaling (Items 2 & 3).
- `desk/src/components/whatsapp/BaileysGroupChatTab.vue` — on-demand target
  fetch, optimistic insert/reconcile (Items 1 & 3).
- `desk/src/components/whatsapp/BaileysChat.vue` — same as above (Items 1 & 3).
- `desk/src/components/whatsapp/WhatsAppBubble.vue` — `Sending` status state,
  cleaner reply fallback (Items 1 & 3).

## Verification

- Backend: unit test for `get_wa_message_by_message_id` (found / absent / wrong-jid).
- Frontend: `bench build --app helpdesk`, then manual matrix — multi-file send,
  caption placement, optimistic bubble replace, downscale on a large photo,
  reply-target on-demand fetch, and the Item 1 live repro.
- No regression to WABA path or text-only send.

## Risks

- Optimistic reconciliation double-render if reload timing races — mitigated by
  explicitly clearing `_optimistic` rows on successful reload.
- Object-URL leaks — mitigated by revoking on clear/send/unmount.
- On-demand fetch loop — mitigated by an attempted-ids `Set`.
- Sequential multi-send latency for many files — acceptable; matches WhatsApp.
