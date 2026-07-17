# Implementation Plan — WA Line Reply-Quote, Multi-File, Optimistic Send

Derived from `docs/superpowers/specs/2026-07-17-wa-line-reply-quote-multifile-upload-design.md`.
Branch: `wa-line-reply-multifile-optimistic`.

## Task 1 — Backend: on-demand reply-target endpoint (Item 1)

Files: `helpdesk/integrations/wa.py`, `helpdesk/tests/` (or existing wa test module).

Steps:
1. Add `@frappe.whitelist() def get_wa_message_by_message_id(message_id, jid)`:
   - Guard empty args → return `None`.
   - Reuse `_select_messages()` scoped to `jid`, add `.where(BM.message_id == message_id)`, `.limit(1)`.
   - Run, `_dedupe_wa_rows`, `_finalize_wa_rows`, return first row or `None`.
   - Wrap the jid/query in try/except (missing custom field parity).
2. Unit test: insert a WA Message, assert found by (message_id, jid); assert `None`
   for wrong jid and for unknown id. Follow existing setUp/tearDown isolation.

Verify: `bench --site dev.localhost run-tests --module <wa test module>` (new test passes).

## Task 2 — Frontend: multi-file attachments (Item 2)

File: `desk/src/components/whatsapp/BaileysReplyBox.vue`.

Steps:
1. Replace `attachment: File|null` + `attachmentPreview` with
   `attachments: ref<Attachment[]>` where `Attachment = { id, file, previewUrl }`.
   Image → `URL.createObjectURL`; non-image → `previewUrl = ""`.
2. `addFiles(files: FileList|File[])` appends; used by `onFileSelected`, `onDrop`,
   `onPaste` (all iterate every file). Add `multiple` to the `<input>`.
3. Template: render one removable chip/thumb per attachment; `removeAttachment(id)`
   revokes its object URL. Keep a "clear all". Update `contentType` to be per-file.
4. Disable-send condition uses `attachments.length`.
5. Object URLs revoked on remove, on successful send, and `onBeforeUnmount`.

Verify (after Task 3, single build): pick/drop/paste N files → N chips; remove works.

## Task 3 — Frontend: optimistic bubble + image downscale (Item 3)

Files: `BaileysReplyBox.vue`, `BaileysGroupChatTab.vue`, `BaileysChat.vue`,
`WhatsAppBubble.vue`.

Steps:
1. `WhatsAppBubble.vue`: add a `Sending` status affordance (spinner / muted clock)
   in the existing status区 region; treat `message.status === "Sending"`.
2. `BaileysReplyBox.vue` `send()`:
   - Add `downscaleImage(file): Promise<File>` — canvas, max long edge 1600, JPEG
     q0.8, skip if not meaningfully smaller or not an image.
   - Refactor send to iterate `attachments` (from Task 2). For each item: emit an
     optimistic message `{ name: "temp-"+uuid, type:"Outgoing", content_type,
     message: (first?caption:""), attach: previewUrl, status:"Sending",
     _optimistic:true, creation: nowISO }` via a new `optimistic` emit BEFORE
     upload; then downscale → upload → `sendReply.submit(...)`.
   - Caption + reply_to only on the first item; rest bare.
   - On pre-send failure: emit `optimistic-remove` for that temp id, restore unsent
     files+caption, banner.
3. Parents (`BaileysGroupChatTab.vue`, `BaileysChat.vue`):
   - Handle `@optimistic` → push row into `loadedMessages` (re-sort), scroll bottom.
   - Handle `@optimistic-remove` → drop by temp name.
   - In the messages-reload success path, drop any remaining `_optimistic` rows
     (real rows have arrived).
   - `mergeMessages` already keys on `message_id || name`; ensure optimistic rows
     (no message_id, temp name) don't collide and are cleared on reload.

Verify: build; send image → instant pending bubble replaced by real one, no dup;
large photo uploads faster; failure removes pending bubble.

## Task 4 — Frontend: on-demand reply-target resolution + fallback (Item 1)

Files: `BaileysGroupChatTab.vue`, `BaileysChat.vue`, `WhatsAppBubble.vue`.

Steps:
1. Both parents: `resolveMissingReplyTarget(messageId)` — if not in
   `messageByMsgId` and not already attempted, call
   `helpdesk.integrations.wa.get_wa_message_by_message_id` with the conversation
   jid; on hit push into `replyTargets`; track attempted ids in a `Set` to prevent
   loops (also mark on miss).
2. Trigger it: watch the rendered message list / call in the reply-block render
   path when `is_reply && reply_to_message_id` misses the map. Simplest: a watcher
   over `messageList` that scans for unresolved reply targets and fetches them.
3. `WhatsAppBubble.vue`: when target confirmed absent, show muted italic
   "Replied to an earlier message" instead of "Original message not available".
   Distinguish "still loading" (no fallback flash) from "confirmed absent".

Verify: build; a reply whose target is outside the page resolves after fetch;
a genuinely-absent target shows the muted fallback; no refetch loop (network tab).

## Task 5 — Build, verify, finish

1. `bench build --app helpdesk`.
2. Backend test module passes.
3. Manual matrix per acceptance criteria in the spec.
4. Review pass (Blocker/Major/Minor/Nit), then finishing-a-development-branch.

## Notes / risks

- Items 2 & 3 both edit `send()` — implement Task 2 first, then layer Task 3.
- Items 1 & 3 both edit the two parents + `WhatsAppBubble` — do Task 3 then Task 4
  to avoid churn; single `bench build` after Task 4.
- Not using a worktree (bench app must stay at `apps/helpdesk`).
