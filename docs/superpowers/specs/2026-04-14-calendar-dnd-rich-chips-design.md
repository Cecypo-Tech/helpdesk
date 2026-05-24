# Calendar View — Drag & Drop + Rich Chips

**Date:** 2026-04-14
**Scope:** `desk/src/pages/tasks/CalendarView.vue` only
**Dependencies:** None new (no new npm packages, no backend changes)

---

## Overview

Two improvements to the task calendar view:

1. **Rich chips** — replace the single-line status-coloured button with a 2-row mini-card that mirrors the Kanban card's information density.
2. **Drag & drop** — allow tasks to be rescheduled by dragging a chip from one day cell to another.

---

## 1. Rich Chips

### Current state
Each task is a single `<button>` with a full-background status colour and a truncated title. Tags, priority, and assignee are not visible.

### New chip design
A 2-row mini-card per task:

| Row | Content |
|-----|---------|
| 1 | Title — `text-[11px] font-medium truncate` |
| 2 | Priority bars (3-bar icon) · Assignee avatar (letter in coloured circle, 16 × 16 px) — right-aligned |

**Status colour** moves from full background → 2 px left border stripe (`border-l-2` + colour class), freeing the body for readability.

**Tags** are omitted from the chip (cells are too narrow). They remain visible in the side panel.

**MAX_VISIBLE** drops from 3 → 2 per cell to give each chip enough vertical room.

### Helper functions (copied from KanbanView)
- `hashStr(s)` — deterministic string hash for colour assignment
- `avatarInitials(name)` — first two initials from email/name
- `avatarColor(name)` — maps hash → `{ bg, text }` Tailwind classes
- `priorityBarH(priority, idx)` — bar height class per index
- `priorityBarColor(priority)` — bar colour class

> Note: these are duplicated for now. A shared `desk/src/pages/tasks/taskUtils.ts` would be the right home eventually, but that refactor is out of scope.

### Status border colours
| Status | Border class |
|--------|-------------|
| Backlog | `border-l-gray-400` |
| Todo | `border-l-blue-400` |
| In Progress | `border-l-orange-400` |
| Done | `border-l-green-400` |

Selected chip: `ring-2 ring-ink-blue-3` replaces the current full `bg-ink-blue-3 text-white` approach.

---

## 2. Drag & Drop

### Approach
Native HTML5 Drag & Drop API — no new dependencies.

### Drag source (task chip)
- `draggable="true"` attribute
- `@dragstart` → write `task.name` and `cell.dateStr` into `event.dataTransfer` as JSON; set source chip to `opacity-40`
- `@dragend` → clear drag state (opacity restored)

### Drop target (day cell)
- `@dragover.prevent` → enables drop; adds highlight classes `ring-2 ring-ink-blue-3 bg-surface-blue-1`
- `@dragleave` → removes highlight
- `@drop` → reads task name + source date; no-op if same date; otherwise runs the update flow

### Update flow on drop
1. Remove highlight from cell
2. Optimistically update `tasks.data` in place (mutate `task.due_date`)
3. Set `selectedTaskId` to the dropped task (opens side panel)
4. Call `set_task_field(task_name, "due_date", newDateStr)` via `frappe-ui/call`
5. On success: `tasks.reload()` (syncs server state)
6. On error: `tasks.reload()` (reverts optimistic change) + `toast.error`

### Drop zone rules
- All visible cells are valid drop targets — including out-of-current-month cells at grid edges
- No automatic month navigation after a cross-month drop
- Same-date drop: silent no-op

### Visual feedback summary
| State | Visual |
|-------|--------|
| Chip being dragged | `opacity-40` |
| Cell being hovered | `ring-2 ring-ink-blue-3 bg-surface-blue-1` |
| Selected chip | `ring-2 ring-ink-blue-3` |

---

## Implementation scope

| File | Change |
|------|--------|
| `desk/src/pages/tasks/CalendarView.vue` | All changes (~80 lines added/changed) |

No backend changes required. `set_task_field` already accepts `due_date` in `ALLOWED_FIELDS`.

---

## Out of scope
- Tag filter on calendar view (Kanban has it; calendar does not — not requested)
- Shared task utility file (`taskUtils.ts`)
- Drag from calendar to Kanban or vice-versa
- Touch drag support
