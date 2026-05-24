# WhatsApp Message Edit — Design Spec

**Date:** 2026-05-24  
**Status:** Approved  
**Scope:** `helpdesk` fork — Evolution API integration + Vue chat UI

---

## Overview

Add bidirectional WhatsApp message edit support:

1. **Incoming** — when a WhatsApp contact edits a message, detect it via the Evolution API webhook and update the existing `Baileys Message` record in place, preserving history.
2. **Outgoing** — agents can edit their own sent text messages from the Helpdesk chat UI; the edit is pushed to WhatsApp via the Evolution API.

Both directions share the same local update path (append old text to history, update message, set `is_edited = 1`) and the same realtime event (`helpdesk:whatsapp-message-edit`) so the Vue chat tab updates without a full reload.

---

## Data Model

### `Baileys Message` — two new fields

| Field | Type | Default | Notes |
|---|---|---|---|
| `is_edited` | Check | 0 | Set to 1 once any edit has been applied |
| `edit_history` | Table | — | Links to `Baileys Message Edit History` |

### New child DocType: `Baileys Message Edit History`

Parent: `Baileys Message`, parentfield: `edit_history`

| Field | Type | Notes |
|---|---|---|
| `old_message` | Text | Message text before this edit |
| `edited_at` | Datetime | When the edit was received/applied |
| `edited_by` | Data | `"incoming"` for customer edits; agent full name for outgoing edits |

One row is appended per edit. History is never deleted or overwritten.

---

## Backend

### File: `helpdesk/integrations/evolution.py`

#### 1. Incoming edit detection in `_handle_upsert`

Evolution API sends edited messages as a `messages.upsert` event with the **same `message_id`** as the original. Two payload shapes must be handled:

**Shape 1** (most common — `editedMessage` wrapper):
```
raw_msg["editedMessage"]["message"]["protocolMessage"]["editedMessage"]["conversation"]
```

**Shape 2** (direct `protocolMessage` with type 14):
```
raw_msg["protocolMessage"]["type"] == 14
raw_msg["protocolMessage"]["editedMessage"]["conversation"]
```

**Logic** (inserted before the existing dedup check):

```python
# Check for edit before dedup
new_text, is_edit = _extract_edit(raw_msg)
if is_edit:
    existing = frappe.db.get_value("Baileys Message", {"message_id": message_id}, "name")
    if existing:
        _apply_edit(existing, new_text, edited_by="incoming", jid=jid, line=line)
        return {"status": "ok", "edited": True}
    # fall through if original not yet stored (edge case)
```

Helper `_extract_edit(raw_msg) -> (str, bool)`: returns `(new_text, True)` if either shape is detected, else `("", False)`.

Helper `_apply_edit(msg_name, new_text, edited_by, jid, line)`:
- Load existing `Baileys Message`
- Append row to `edit_history`: `{old_message: doc.message, edited_at: now(), edited_by: edited_by}`
- Set `doc.message = new_text`, `doc.is_edited = 1`, save with `ignore_permissions=True`
- Publish `helpdesk:whatsapp-message-edit`:
  ```python
  frappe.publish_realtime(
      "helpdesk:whatsapp-message-edit",
      message={"message_id": message_id, "new_text": new_text, "name": msg_name,
               "jid": jid, "line": line.name},
      after_commit=True,
  )
  ```

#### 2. New whitelisted function: `edit_evolution_message`

```python
@frappe.whitelist()
def edit_evolution_message(message_name: str, new_text: str) -> dict:
```

Steps:
1. Fetch `Baileys Message` by `message_name`.
2. Validate: `direction == "Outgoing"`, `content_type == "text"`. Throw otherwise.
3. Validate `new_text` is non-empty.
4. Get `Evolution Line` via `doc.line`.
5. Call Evolution API:
   ```
   PUT /message/updateMessage/{instance_name}
   {
     "number": "<doc.jid>",
     "key": { "id": "<doc.message_id>", "fromMe": true, "remoteJid": "<doc.jid>" },
     "text": "<new_text>"
   }
   ```
6. On HTTP error: raise — no local mutation (keeps DB consistent with WhatsApp).
7. On success: call `_apply_edit(doc.name, new_text, edited_by=agent_full_name, jid=doc.jid, line=line)`.
8. Return `{"status": "ok", "name": doc.name}`.

---

## Frontend

### `desk/src/components/whatsapp/WhatsAppChatTab.vue`

Add a third socket subscription alongside the existing two:

```ts
$socket.on("helpdesk:whatsapp-message-edit", handleMessageEdit)
// (and off in onBeforeUnmount)
```

Handler — mutates `messages.data` in place (same pattern as `handleStatusUpdate`):

```ts
function handleMessageEdit(data: { message_id: string; new_text: string; name: string }) {
  const list = messages.data
  if (!list) return
  const msg = list.find((m: any) => m.message_id === data.message_id)
  if (msg) {
    msg.message = data.new_text
    msg.is_edited = 1
  }
}
```

No reload, no scroll jump.

The tab also handles the `edit` event emitted by `WhatsAppBubble` for outgoing edits:

```ts
async function applyEdit(messageName: string, newText: string) {
  await call("helpdesk.integrations.evolution.edit_evolution_message", {
    message_name: messageName,
    new_text: newText,
  })
  // realtime event will update the bubble; no manual mutation needed
}
```

Pass `@edit="applyEdit"` to `<WhatsAppBubble>`.

### `desk/src/components/whatsapp/WhatsAppBubble.vue`

#### New emit
```ts
(e: "edit", messageName: string, newText: string): void
```

#### Edit button (action bar)

Shown only when `isOutgoing && message.content_type === 'text'`. Pencil icon, same size/style as existing Copy/React buttons. Positioned between Copy and React. Clicking sets `editing.value = true` and `editText.value = message.message`.

#### Inline editor

When `editing` is true, replace the `<div v-html="formattedMessage" />` with:

```html
<textarea
  v-model="editText"
  ref="editTextareaRef"
  class="w-full resize-none rounded border border-outline-gray-3 bg-surface-white px-2 py-1 text-sm text-ink-gray-9 focus:outline-none"
  rows="3"
  @keydown.ctrl.enter="saveEdit"
  @keydown.esc="cancelEdit"
/>
<div class="mt-1 flex justify-end gap-1.5">
  <button @click="cancelEdit" class="text-xs text-ink-gray-5 hover:text-ink-gray-8">Cancel</button>
  <button @click="saveEdit" :disabled="editSaving" class="text-xs font-medium text-blue-600 hover:underline disabled:opacity-50">
    {{ editSaving ? 'Saving…' : 'Save' }}
  </button>
</div>
```

Auto-focus the textarea via `nextTick` when `editing` becomes true.

`saveEdit()`: sets `editSaving = true`, emits `edit(message.name, editText.value)`, awaits, sets `editing = false`. On error: shows `toast.error(...)`, keeps editor open.

`cancelEdit()`: sets `editing = false`, resets `editText`.

#### "Edited" badge + history tooltip

In the footer timestamp row, after the time `<span>`, add:

```html
<span
  v-if="message.is_edited"
  class="relative text-[10px] italic text-ink-gray-4 cursor-default"
  @mouseenter="showEditHistory = true"
  @mouseleave="showEditHistory = false"
>
  · edited
  <div
    v-if="showEditHistory && editHistoryList.length"
    class="absolute bottom-full right-0 mb-1 z-30 w-56 rounded border border-outline-gray-2 bg-surface-white shadow-md text-[11px] text-ink-gray-7 overflow-y-auto"
    style="max-height: 140px"
  >
    <div v-for="h in editHistoryList" :key="h.edited_at" class="border-b border-outline-gray-1 px-2 py-1.5 last:border-0">
      <div class="mb-0.5 text-ink-gray-4">{{ formatEditTime(h.edited_at) }} · {{ h.edited_by }}</div>
      <div class="whitespace-pre-wrap break-words">{{ h.old_message }}</div>
    </div>
  </div>
</span>
```

`editHistoryList` is a computed from `message.edit_history || []`, sorted newest-first by `edited_at`.

The history data requires two backend changes in `get_whatsapp_messages` (in `evolution.py`):
1. Add `BM.is_edited` to the `frappe.qb` `select()` clause.
2. After the main query, collect `name` values of all edited messages and do a second query: `frappe.db.get_all("Baileys Message Edit History", filters={"parent": ["in", edited_names]}, fields=["parent", "old_message", "edited_at", "edited_by"], order_by="edited_at desc")`. Attach the results to their parent row as `m["edit_history"]`.

---

## Realtime Event Contract

| Event | Payload | Direction |
|---|---|---|
| `helpdesk:whatsapp-message-edit` | `{message_id, new_text, name, jid, line}` | server → all users |

Broadcast to `"all"` room, consistent with existing WhatsApp events.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Evolution API rejects outgoing edit | Throw in `edit_evolution_message`; no local mutation; frontend shows toast error, keeps editor open |
| Edit webhook arrives before original message | Fall through to normal upsert path (creates new record — extremely rare) |
| `message_id` absent on outgoing message | Edit button hidden (`message.message_id` falsy check) |
| Customer edits a media message | `_extract_edit` returns `("", False)` — falls through to dedup and is dropped (WhatsApp doesn't allow editing media captions via the edit protocol) |

---

## Migrations Required

1. `bench --site site16.local migrate` — applies new fields and child DocType
2. `bench build --app helpdesk` — rebuilds Vue assets
