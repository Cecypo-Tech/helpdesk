# Kanban Detail Panel & Drag-and-Drop

**Date:** 2026-03-24
**Status:** Approved

---

## Overview

Add a collapsable split-panel detail view and native drag-and-drop status changes to the existing `KanbanView.vue` component in the Helpdesk Tasks feature.

---

## Layout

The Kanban page (`Tasks.vue` → `KanbanView.vue`) switches from a full-width column layout to a **split layout**:

- **Left** — Kanban columns area (`flex-1`, `overflow-x-auto`). All four status columns remain horizontally scrollable.
- **Right** — Detail panel (`w-96`, fixed width, `border-l`). Visible when a task is selected.

### Collapsable Panel Strip

- A **collapse/expand toggle button** (`‹` / `›`) sits at the top of the panel border.
- When collapsed, the panel shrinks to `w-10` and shows only the toggle button.
- Collapse state is persisted in `localStorage` under the key `hd_task_kanban_panel_collapsed`.
- On **small screens** (`window.innerWidth < 640` on mount), the panel starts collapsed automatically.
- The panel can be open (expanded) without a selected task — it shows an empty state ("Select a task to view details").

### Card Click Integration

`KanbanView` handles panel open/close **internally** — clicking a card sets `selectedTaskId` in the component's local state. `KanbanView` does **not** emit `card-click` to `Tasks.vue` in kanban mode. The `@card-click` emit on `KanbanView` is removed entirely, and `Tasks.vue` drops the `@card-click` handler from the `<KanbanView>` element.

---

## Detail Panel (`KanbanTaskPanel.vue`)

A new standalone component.

**Props**: `taskId: string | null`
**Emits**: `close`, `saved`

### Component Re-Render on Task Switch

In `KanbanView.vue`, bind `KanbanTaskPanel` with `:key="selectedTaskId"`:

```html
<KanbanTaskPanel
  v-if="selectedTaskId"
  :key="selectedTaskId"
  :task-id="selectedTaskId"
  @close="selectedTaskId = null"
  @saved="tasks.reload()"
/>
```

Using `:key` ensures Vue destroys and recreates the component (and its document resource) each time the user selects a different task. This avoids the need for manual `watch` + re-fetch logic.

### Data Loading

Uses `createDocumentResource` from `frappe-ui`. The `auto` flag is only `true` when `taskId` is non-null (the `v-if="selectedTaskId"` on the parent already guarantees this, but the guard makes the intent explicit):

```ts
const task = createDocumentResource({
  doctype: "HD Task",
  name: props.taskId,
  auto: !!props.taskId,
  onSuccess(doc) { /* populate local form */ },
  onError() {
    // Task not found or access denied — close the panel
    emit("close");
    toast.error(__("Task not found"));
  },
})
```

### Fields Displayed (all editable)

| Field | Component | Save trigger |
|-------|-----------|--------------|
| Title | `<input type="text">` | `blur` |
| Status | `<select>` (Backlog / Todo / In Progress / Done) | `change` |
| Priority | `Link` (→ HD Ticket Priority) | `@change` event |
| Assigned To | `Link` (→ HD Agent) | `@change` event |
| Due Date | `<input type="date">` | `blur` |
| Linked Ticket | `Link` (→ HD Ticket) | `@change` event |
| Team | `Link` (→ HD Team) | `@change` event |
| Description | `TextEditor` from frappe-ui | `@change` event (debounced 800ms) |
| Subtasks | inline checklist (see below) | on any mutation |

**Note on `Link` auto-save**: The `Link` component (at `@/components/frappe-ui/Link.vue`) emits a `"change"` event whenever the value is committed. Wire auto-save on `@change`, not on `@update:modelValue`. Example:

```html
<Link v-model="form.priority" doctype="HD Ticket Priority" @change="saveField('priority', form.priority)" />
```

**Note on `TextEditor`**: `TextEditor` does not emit `blur`. Use `@change` with an 800ms debounce to avoid saving on every keystroke.

A **"Open full page →"** link at the bottom navigates to the `TaskAgent` route (full `TaskDetail.vue`).

### Auto-Save Behaviour

**Scalar fields** (title, status, priority, assigned_to, due_date, ticket, team, description) use `frappe.client.set_value` via `call` from frappe-ui:

```ts
import { call } from "frappe-ui";

async function saveField(fieldname: string, value: any) {
  await call("frappe.client.set_value", {
    doctype: "HD Task",
    name: props.taskId,
    fieldname,
    value: value || null,
  });
  emit("saved");
}
```

**Subtasks** — child table, must save the whole doc since Frappe does not support partial child-table saves:

```ts
await call("frappe.client.save", {
  doc: {
    doctype: "HD Task",
    name: props.taskId,
    subtasks: form.subtasks.map(s => ({
      doctype: "HD Task Subtask",   // required for child table validation
      name: s.name || null,
      title: s.title,
      status: s.status,
      due_date: s.due_date || null,
    })),
  },
});
emit("saved");
```

**UX feedback**: No toast on success. A subtle `✓ Saved` text flashes for 1.5 seconds in the panel header after each successful save. On error: `toast.error(...)`.

**Status change side-effect**: Saving status emits `saved`, which causes `KanbanView` to call `tasks.reload()` — this moves the card to its new column. An optimistic local update is applied first so the card moves immediately without waiting for the network.

### Subtasks Section

- Checkbox list. Each row: checkbox (toggles Done↔Todo), title text input, status select, due date input.
- **Add subtask** button at bottom.
- Remove button (trash icon, visible on row hover).
- Progress bar (`X / Y done`) above the list.
- Saves the full doc after any mutation (add, remove, toggle, edit).

### Overdue Indicator

Due date displayed in `text-red-500` when `new Date(due_date) < new Date()`. Known limitation: date-only fields are parsed as UTC midnight, so tasks due "today" appear overdue for users west of UTC+0 until midnight UTC.

---

## Drag and Drop

Implemented with **native HTML5 DnD** — no external library.

### Card (draggable source)

```html
<div draggable="true"
     @dragstart="onDragStart(card)"
     @dragend="onDragEnd">
```

On `dragstart`: store `draggedCard = { name, status }` in a ref; add `opacity-40` to the card element.
On `dragend`: clear `draggedCard`, clear opacity.

### Column (drop target)

```html
<div @dragover.prevent="onDragOver(col.status)"
     @dragleave="onDragLeave"
     @drop.prevent="onDrop(col.status)">
```

On `dragover`: set `hoveredColumn = col.status` (adds a highlight ring to the column header).
On `dragleave`: clear `hoveredColumn`.

On `drop`:
1. If `draggedCard.status === targetStatus` → no-op, return.
2. **Optimistic update**: find the card in `tasks.data`, set `card.status = targetStatus` immediately.
3. Call `call("frappe.client.set_value", { doctype: "HD Task", name: draggedCard.name, fieldname: "status", value: targetStatus })`.
4. On success: call `tasks.reload()` to sync with server.
5. On error: revert `card.status` to `draggedCard.status`, show `toast.error(__("Failed to move task"))`.

**Correct reload call**: Use `tasks.reload()` on the `createListResource` result, not `tasks.list.reload()`.

---

## Component Structure

```
KanbanView.vue          — refactored: split layout, drag-and-drop, panel show/hide, collapse toggle
KanbanTaskPanel.vue     — new: full-detail side panel with auto-save
```

No backend changes. No new routes. No new DocTypes.

---

## Files Modified / Created

| File | Change |
|------|--------|
| `desk/src/pages/tasks/KanbanView.vue` | Refactor: split layout, DnD logic, panel state, collapse state, remove `card-click` emit |
| `desk/src/pages/tasks/KanbanTaskPanel.vue` | **Create**: full detail panel with auto-save |
| `desk/src/pages/tasks/Tasks.vue` | Remove `@card-click` handler from `<KanbanView>` |

---

## Out of Scope

- Board-level filters from the panel
- Multi-card drag (move multiple at once)
- Touch drag-and-drop (native HTML5 DnD has limited touch support; acceptable for now)
- Description and subtasks on the List view page (remain on `TaskDetail.vue`)
