# WA Line — reactions / edit / delete: finish report

Date: 2026-07-17
Branch: `wa-line-reactions-edit-delete` (off `develop`)
Companions: `…-investigation.md`, `…-plan.md`

## What was broken, and what fixed it

| Bug | Root cause | Fix |
|---|---|---|
| Edit never worked | `PUT /message/updateMessage` — route doesn't exist on Evolution v2 (404 on PUT *and* POST) | `POST /chat/updateMessage` (payload was already correct) |
| Reaction couldn't be cleared | Guard rejected `emoji=""` as a missing argument | Guard now requires only `jid` + `target_message_id`; `""` is a valid clear |
| Replacing a reaction showed both emojis | Fold pushed every reaction row into one array keyed by target only | `foldReactions()` keys by `(target, sender)`, latest wins |
| Cleared reaction left a stale badge | `""` row skipped by `&& m.message`; superseded row still rendered | `""` is a tombstone that supersedes the emoji before it |
| Sender tooltip always empty | Bubble expected `sender`; parents supplied `{emoji, type}` only | Fold populates `sender` (direction-aware — see trap below) |
| No way to clear from the UI | Badge was `cursor-default`, no handler | Badge is a button; own badge toggles off, re-picking your emoji clears |
| Delete not implemented | No endpoint at all | `delete_wa_message` → `DELETE /chat/deleteMessageForEveryone` |
| Customer delete showed as failed + Retry | `_handle_delete` wrote `status="Failed"` (no `is_deleted` field existed) | New `is_deleted` Check; `status` untouched; Retry blocked front and back |
| Customer delete never reached the UI | `_handle_delete` published no event | New `helpdesk:whatsapp-message-delete`; bubble flips live |
| Standalone chat had no edit/retry/delete | Only `@react` was wired | Full parity in `BaileysChat.vue` |

## Evidence the fixes are right

Every route was verified against the live server (Evolution **v2.3.7**) *and* the
upstream source, not inferred from docs:

- `POST /message/sendReaction` — exists; `reactionMessageSchema` declares
  `reaction: {type:'string'}` with **no** isNotEmpty → `""` is explicitly valid.
- `POST /chat/updateMessage` — exists (`chat.router.ts`); `message/updateMessage` 404s.
- `DELETE /chat/deleteMessageForEveryone` — exists; requires `id`, `fromMe`, `remoteJid`.

The edit regression test was confirmed to **fail against the old code** with
"edit must use POST, not PUT" before the fix was restored — it isn't a vacuous test.

## Traps found during implementation (not in the original plan)

1. **`sender_full_name` joins on `owner`**, which is `Administrator` for every
   inbound row. Naively preferring it would have labelled every customer
   "Administrator". The fold branches on direction instead. Pinned by a test.
2. **WABA shares the bubble**, so the new clear-toggle reaches `_send_fw_reaction`.
   Checked: `message` is not mandatory on `WhatsApp Message` and Meta treats
   `emoji: ""` as a removal — so clearing works on both paths with no gate needed.
3. **No vitest existed** (only Playwright). Added `vitest@0.34` (Vite 4-compatible;
   vitest 1+ requires Vite 5) + `yarn test:unit`.
4. **`window.confirm` had no precedent.** Reworked to the app's own
   `ConfirmDialog.vue`, owned by the parent (one dialog per chat, not per bubble).

## Verification

**Automated — all green**

| Suite | Result |
|---|---|
| `test_wa_reactions` (new) | 6 passed |
| `test_wa_edit_delete` (new) | 14 passed |
| `test_wa_message_by_id` | 4 passed |
| `test_wa_messages_pagination` | 12 passed |
| `test_wa_thumbnails` | 17 passed |
| `waReactions.spec.ts` (new) | 18 passed |
| `bench build --app helpdesk` | 2876 modules, no errors |
| `ruff` on new files | clean |

53 backend tests (33 pre-existing regressions still green) + 18 frontend.
`bench migrate` applied; `is_deleted` column confirmed present.

**Not yet done — live matrix against a real WA line** (rows 1–11 in the plan).
Nothing here has been exercised against a real phone; that is the remaining risk.

## Review pass

- **Blocker**: none.
- **Major**: none outstanding. (The `sender_full_name` trap and the `window.confirm`
  off-pattern were both found and fixed during review, before commit.)
- **Minor**: reaction rows stay append-only, so each toggle adds a row. Correct,
  but the table keeps growing (~19% reactions already).
- **Nit**: `handleEditUpdate` in `BaileysGroupChatTab` patches `messages.data`
  while `handleStatusUpdate`/`handleDeleteUpdate` patch `loadedMessages` — a
  pre-existing inconsistency, left alone rather than widened here.

## Follow-ups (out of scope, flagged)

1. **`wa.py:3722` — `F821 Undefined name 'json'`** in `send_template_to_ticket`.
   Pre-existing on develop (confirmed against baseline), and a genuine latent
   `NameError` at runtime. Worth its own fix.
2. **Historical `status="Failed"` rows** written by the old `_handle_delete` are
   indistinguishable from real send failures. Not back-filled.
3. **`WA Line.connected_user` is `None`** on all lines (`_test-evo`, `KP`, `SV`),
   so mirrored from-phone messages fall back to an `Administrator` owner.
4. Reaction-row compaction job to prune superseded rows.
