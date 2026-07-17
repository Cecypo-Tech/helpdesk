# WA Line — fix reactions (clear/replace), edit, delete — implementation plan

Date: 2026-07-17
Companion: `2026-07-17-wa-reactions-edit-delete-investigation.md`
Branch: `wa-line-reactions-edit-delete` (off `develop`)

## Decisions (locked with user)

1. **Delete = own outgoing only, for everyone.** `DELETE /chat/deleteMessageForEveryone`.
   No local-only "delete for me". Customer deletes still render via webhook.
2. **Full parity for `BaileysChat.vue`** — wire edit + retry + delete, matching the ticket tab.
3. WABA (`WhatsAppBusinessChat.vue`) gets the **reaction fold fix only** — Meta's API has no
   edit/unsend here; `allowEdit`/`allowDelete` stay off.

## Reaction semantics (the model we're implementing)

WhatsApp = **one reaction per sender per message; latest wins; empty string clears**.
Sender key: `type === "Outgoing" ? "__me__" : (sender_jid || jid)`.
Collapsing all Outgoing to one key is correct — every agent reacts through the same WA
line, so WhatsApp itself sees a single sender.

Storage stays **append-only** (each reaction/clear is its own row, as the webhook
delivers them); correctness comes from the fold. Verified safe: `get_whatsapp_messages`
fetches *all* reaction rows targeting the page's messages (`reply_to_message_id.isin(page_ids)`,
`.orderby(creation)`, no limit), so the fold always sees the full history per target.

---

## Task 1 — Edit: correct route + method (backend)

`wa.py:1477` — `_evo_session.put(_url("message/updateMessage", ...))`
→ `_evo_session.post(_url("chat/updateMessage", ...))`

Payload is already correct (`number`, `key{id,fromMe,remoteJid}`, `text`) per
`updateMessageSchema`. POST also correctly drops out of the retry allowlist (non-idempotent).

- **Test**: mock `_evo_session`; assert POST to `.../chat/updateMessage/{instance}` and payload shape.
- **Verify**: edit a sent text message in the ticket tab → text updates in WhatsApp + "· edited".

## Task 2 — Reactions: allow clearing (backend)

`wa.py:1395` — guard becomes `if not jid or not target_message_id:` (drop `or not emoji`).
`emoji=""` is a valid clear (`reactionMessageSchema` has no `isNotEmpty` on `reaction`).
Row still inserted with `message=""` — the fold treats it as a tombstone.
Leave the `_send_fw_reaction` (WABA) branch untouched.

- **Test**: `emoji=""` does not throw; posts `{"reaction": ""}`; inserts a row with `message=""`.

## Task 3 — `is_deleted` field + inbound delete (backend)

1. `wa_message.json`: add `is_deleted` (Check, default 0) after `is_edited`; add to field_order.
2. `_handle_delete` (`wa.py:1138`): set `is_deleted=1` — **stop writing `status="Failed"`**
   (that corrupts delivery analytics and offers Retry on a deleted message).
3. `_handle_delete` currently publishes **nothing** → customer deletes never reach an open
   chat. Publish `helpdesk:whatsapp-message-delete` `{message_id, jid, line}`.
4. `_select_messages` (`wa.py:2446`): add `BM.is_deleted` so the client can render it.
5. `bench migrate`.

- **Test**: `_handle_delete` sets `is_deleted=1`, leaves `status` untouched, publishes the event.

## Task 4 — `delete_wa_message` endpoint (backend, new)

```
@frappe.whitelist()
def delete_wa_message(message_name: str) -> dict
  - settings.enabled else throw
  - doc.direction != "Outgoing"           -> throw "Only your own messages can be deleted."
  - doc.owner != session.user and not System Manager -> throw   (mirrors edit_wa_message)
  - already is_deleted                    -> return early (idempotent)
  - DELETE _url("chat/deleteMessageForEveryone", line.instance_name)
        json={"id": doc.message_id, "fromMe": True, "remoteJid": doc.jid}
  - set is_deleted=1; publish helpdesk:whatsapp-message-delete
```
`deleteMessageSchema` requires `id`, `fromMe`, `remoteJid` (`participant` optional — not
needed for own messages). DELETE is already in the retry allowlist and the call is
idempotent, so retries are safe.

- **Test**: happy path; incoming → throws; other user's message → throws; API failure → no `is_deleted` flip.

## Task 5 — Reaction fold, shared (frontend)

New `desk/src/utils/waReactions.ts` exporting `foldReactions(rows)` so the three
components stop triplicating the logic:

```ts
// one reaction per (target, sender); latest wins; "" clears
export function foldReactions(rows) {
  const latest = {};                     // target -> senderKey -> row
  for (const m of rows) {
    if (m.content_type !== "reaction" || !m.reply_to_message_id) continue;
    const senderKey = m.type === "Outgoing" ? "__me__" : (m.sender_jid || m.jid || "__them__");
    const bucket = (latest[m.reply_to_message_id] ||= {});
    const prev = bucket[senderKey];
    if (!prev || new Date(m.creation) >= new Date(prev.creation)) bucket[senderKey] = m;
  }
  const map = {};
  for (const [target, bySender] of Object.entries(latest)) {
    const list = Object.values(bySender)
      .filter((m) => m.message)          // cleared -> tombstone, drop
      .map((m) => ({ emoji: m.message, type: m.type,
                     sender: m.sender_full_name || m.profile_name
                             || (m.type === "Outgoing" ? "You" : "Customer") }));
    if (list.length) map[target] = list;
  }
  return map;
}
```
Fixes at once: replace-shows-both (R2), stale cleared badge (R3), and the missing
`sender` the bubble already expects for its tooltip (R4).
Swap `reactionsMap` in `BaileysGroupChatTab.vue`, `BaileysChat.vue`, `WhatsAppBusinessChat.vue`.

- **Test**: vitest — replace keeps latest only; `""` clears; two senders both shown;
  same emoji from 2 senders → count 2; ordering by `creation` not array position.

## Task 6 — Bubble: toggle + deleted rendering (frontend)

`WhatsAppBubble.vue`:
- Badge: `cursor-default` → `cursor-pointer`, add `@click.stop="toggleReaction(r)"`;
  `toggleReaction(r)` emits `react(r.hasOwn ? "" : r.emoji, message_id)` (R5).
- `pickEmoji(emoji)`: if own reaction is already that emoji → emit `""` (WhatsApp toggles).
- Add `allowDelete` prop + `(e: "delete", messageName: string)` emit; delete action in the
  hover menu, outgoing + not-already-deleted only.
- When `message.is_deleted`: render 🚫 *This message was deleted* (italic, ink-gray-5);
  hide media/text, reactions, retry, edit, and the react affordance.

## Task 7 — Wire parents (frontend)

- `BaileysGroupChatTab.vue`: `:allowDelete`, `@delete="handleDelete"` + `deleteResource`.
- `BaileysChat.vue` (parity): add `:allowEdit`, `:allowDelete`, `@edit`, `@retry`, `@delete`
  + `editResource` / `retryResource` / `deleteResource`.
- Both: subscribe `helpdesk:whatsapp-message-delete` → `messages.reload()`; unsubscribe on
  unmount (match the existing `helpdesk:whatsapp-message-edit` pattern).

---

## Verification

**Automated**
```bash
cd /home/kushal/frappe-bench
bench --site dev.localhost migrate                      # is_deleted field
bench --site dev.localhost run-tests --app helpdesk --module helpdesk.integrations.tests.test_wa_reactions
bench --site dev.localhost run-tests --app helpdesk --module helpdesk.integrations.tests.test_wa_edit_delete
bench --site dev.localhost run-tests --app helpdesk --module helpdesk.integrations.tests.test_wa_message_by_id
bench --site dev.localhost run-tests --app helpdesk --module helpdesk.integrations.tests.test_wa_messages_pagination
bench --site dev.localhost run-tests --app helpdesk --module helpdesk.integrations.tests.test_wa_thumbnails
cd apps/helpdesk/desk && yarn vitest run src/utils/__tests__/waReactions.spec.ts
pkill -f "frappe.app"; bench serve --port 8002 &        # reload wa.py
bench build --app helpdesk                              # reload Vue
```
Regression guard: the 33 existing WA tests must still pass (pagination asserts reactions
don't consume page slots — the fold change must not disturb that).

**Live matrix** (real WA line, ticket tab + standalone page)

| # | Action | Expected |
|---|---|---|
| 1 | React 👍 | badge 👍, count 1, blue "own" ring |
| 2 | React ❤️ on same msg | badge shows **❤️ only** (not 👍❤️) |
| 3 | Click own badge | reaction disappears in helpdesk **and on the phone** |
| 4 | Pick same emoji twice | second pick clears |
| 5 | Customer reacts, then removes | badge appears, then disappears (no stale) |
| 6 | Two senders same emoji | count 2, tooltip lists both names |
| 7 | Edit own text message | updates on phone + "· edited" + history |
| 8 | Edit from standalone page | same (new capability) |
| 9 | Delete own message | gone on phone; "This message was deleted"; no Retry |
| 10 | Customer deletes | bubble flips to deleted **live**, no reload |
| 11 | Delete someone else's / incoming | no delete option offered |

## Risks

- **Doctype change** (`is_deleted`) needs `bench migrate` on every site; on Frappe Cloud it
  runs at deploy. Rendering guards on `m.is_deleted` (undefined → falsy) so an un-migrated
  site degrades to current behaviour rather than erroring.
- **`status="Failed"` rows already written** by the old `_handle_delete` are indistinguishable
  from genuine send failures. Not back-filling; flagged as a follow-up.
- Fold change touches the WABA tab too — covered by matrix rows 1–6 on a WABA ticket.

## Out of scope (follow-ups)

- Back-fill historical `status="Failed"` deletes.
- `WA Line.connected_user` is `None` on all lines (`_test-evo`, `KP`, `SV`) — mirrored
  from-phone messages fall back to `Administrator` owner. Unrelated; worth its own look.
- Reaction rows are append-only and ~19% of the table; a compaction job could prune
  superseded rows.
