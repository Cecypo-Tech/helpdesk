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
- When collapsed, the panel shrinks to `w-10` and shows only the toggle button and a rotated task title (or nothing if no task selected).
- Collapse state is persisted in `localStorage` under the key `hd_task_kanban_panel_collapsed`.
- On **small screens** (`window.innerWidth < 640` on mount), the panel starts collapsed automatically.
- The panel can be open (expanded) without a selected task — it shows an empty state ("Select a task to view details").

---

## Detail Panel (`KanbanTaskPanel.vue`)

A new standalone component. Props: `taskId: string`. Emits: `close`, `saved`.

### Data Loading

Uses `createDocumentResource` from `frappe-ui`:

```ts
const task = createDocumentResource({
  doctype: "HD Task",
  name: props.taskId,
  auto: true,
  onSuccess(doc) { /* populate local form */ }
})
```

### Fields Displayed (all editable)

| Field | Component | Save trigger |
|-------|-----------|--------------|
| Title | plain `<input type="text">` | `blur` |
| Status | `<select>` (Backlog / Todo / In Progress / Done) | `change` |
| Priority | `Link` (→ HD Ticket Priority) | `change` |
| Assigned To | `Link` (→ HD Agent) | `change` |
| Due Date | `<input type="date">` | `blur` |
| Linked Ticket | `Link` (→ HD Ticket) | `change` |
| Team | `Link` (→ HD Team) | `change` |
| Description | `TextEditor` from frappe-ui | `blur` |
| Subtasks | inline checklist (see below) | on any change |

A **"Open full page →"** link at the bottom navigates to `TaskAgent` (the existing `TaskDetail.vue`).

### Auto-Save Behaviour

**Scalar fields** (title, status, priority, assigned_to, due_date, ticket, team):

```ts
call("frappe.client.set_value", {
  doctype: "HD Task",
  name: props.taskId,
  fieldname: field,
  value: value,
})
```

**Description** — same `set_value` call on blur.

**Subtasks** — child table, saved as a whole doc:

```ts
call("frappe.client.save", {
  doc: {
    doctype: "HD Task",
    name: props.taskId,
    subtasks: form.subtasks.map(s => ({ ... })),
  }
})
```

**UX feedback**: No toast on success. A subtle `✓ Saved` text flashes for 1.5 seconds in the panel header area after each successful save. On error: `toast.error(...)`.

**Status change side-effect**: When status is saved, the component emits `saved` so `KanbanView` can reload the list resource and move the card to its new column.

### Subtasks Section

- Checkbox list. Each row: checkbox (toggles Done↔Todo), title text input, status select, due date input.
- **Add subtask** button at bottom.
- Remove button (trash icon) visible on hover.
- Progress bar (`X / Y done`) above the list.
- Saves the full doc after any mutation (add, remove, toggle, edit).

### Overdue Indicator

Due date displayed in `text-red-500` when `new Date(due_date) < new Date()`.

---

## Drag and Drop

Implemented with **native HTML5 DnD** — no external library.

### Card (draggable source)

```html
<div draggable="true"
     @dragstart="onDragStart(card)"
     @dragend="onDragEnd">
```

On `dragstart`: store `draggedCard = { name, status }` in a ref; set `card.dragging = true` (adds `opacity-40` class).

On `dragend`: clear `draggedCard`, clear `card.dragging`.

### Column (drop target)

```html
<div @dragover.prevent="onDragOver(col.status)"
     @dragleave="onDragLeave"
     @drop.prevent="onDrop(col.status)">
```

On `dragover`: set `hoveredColumn = col.status` (adds a highlight ring to the column).

On `dragleave`: clear `hoveredColumn`.

On `drop`:
1. If `draggedCard.status === targetStatus` → no-op.
2. **Optimistic update**: find the card in `tasks.data`, set `card.status = targetStatus`.
3. Call `call("frappe.client.set_value", { doctype: "HD Task", name: draggedCard.name, fieldname: "status", value: targetStatus })`.
4. On success: call `tasks.list.reload()` to sync with server.
5. On error: revert `card.status` to original, show `toast.error("Failed to move task")`.

---

## Component Structure

```
KanbanView.vue          — refactored: split layout, drag-and-drop, panel show/hide, collapse toggle
KanbanTaskPanel.vue     — new: full-detail side panel with auto-save
```

No changes to backend. No new routes. No new DocTypes.

---

## Files Modified

| File | Change |
|------|--------|
| `desk/src/pages/tasks/KanbanView.vue` | Add split layout, DnD logic, panel open/close, collapse state |
| `desk/src/pages/tasks/KanbanTaskPanel.vue` | **Create** — full detail panel component |

---

## Out of Scope

- Board-level filters from the panel
- Multi-card drag (move multiple at once)
- Touch drag-and-drop (native HTML5 DnD has limited touch support; acceptable for now)
- Description and subtasks on the List view page (remain on `TaskDetail.vue`)
