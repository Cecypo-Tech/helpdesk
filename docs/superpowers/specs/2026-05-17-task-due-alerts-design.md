# Task Due-Date Awareness — Design Spec

**Date:** 2026-05-17  
**Status:** Approved  

## Problem

Agents have no in-app signal when a task is overdue or due today. The task list and kanban board lack quick filters to surface these tasks. WhatsApp reminders exist (scheduler-based) but only fire once and only while the agent's phone is reachable.

## Solution Summary

Three coordinated additions:

1. **Slide-in alert panel** — appears bottom-right while the app is open, lists the logged-in agent's overdue/due-today tasks with per-task snooze and mark-done actions.
2. **Quick filter chips** — "Overdue" and "Due Today" chips on the Kanban and List view filter bars for on-demand filtering.
3. **Backend API** — one new whitelisted endpoint to fetch due tasks for the current user.

---

## Section 1 — Backend API

**File:** `helpdesk/helpdesk/doctype/hd_task/hd_task.py`

New whitelisted function:

```python
@frappe.whitelist()
def get_my_due_tasks() -> list[dict]:
    """Return tasks assigned to the current user that are overdue or due today, status != Done."""
```

**Query criteria:**
- `assigned_to == frappe.session.user`
- `due_date <= CURDATE()`
- `status != 'Done'`

**Returns fields:** `name`, `title`, `due_date`, `due_time`, `status`, `ticket`

**Mark done:** reuses existing `set_task_field(task_name, fieldname="status", value="Done")` — no new endpoint needed.

No schema changes required.

---

## Section 2 — Snooze Logic

**File:** `desk/src/composables/useTaskDueAlerts.ts` (new)

### localStorage schema

```
Key:   hd_task_snooze__{userEmail}__{taskName}
Value: ISO 8601 timestamp (snooze expiry)
```

Scoped by user email so shared browsers don't interfere between accounts.

### Snooze durations

| Button | Expiry |
|--------|--------|
| 15 min | `now + 15 minutes` |
| 1 hr   | `now + 1 hour` |
| Tmrw 9am | tomorrow at `09:00` local time |

### Timer loop

- Calls `get_my_due_tasks()` on mount, then every **5 minutes**
- After fetching, filters out tasks whose snooze key exists in `localStorage` with a future timestamp
- If any tasks remain → shows the panel
- Expired snooze entries are ignored automatically (no cleanup needed — timestamp in the past = snoozed window elapsed)

### Actions

- **Snooze** → writes expiry to `localStorage`, removes task from visible list immediately
- **Mark Done** → calls `set_task_field(status: Done)` via `frappe.call`, removes task from visible list immediately
- **Dismiss panel (✕)** → hides panel until next 5-min tick; does not snooze individual tasks

---

## Section 3 — Quick Filter Chips

### Kanban (`KanbanView.vue`)

Two toggle chips added to the existing filter bar (alongside search / assignee / tag):

| Chip | Filter logic |
|------|-------------|
| **Overdue** | `due_date < today AND status != Done` |
| **Due Today** | `due_date == today` |

- Client-side filtering via `getCardsForStatus()` — no extra API call (all 999 tasks already loaded)
- Chips are mutually exclusive (only one active at a time)
- Stack with existing search/assignee/tag filters
- Active chip has highlighted/filled style to indicate it's on

### Done column — 3-day window

The **Done** column header changes to **"Done (last 3 days)"** and only shows tasks where `modified >= today - 3 days`. This prevents the column from growing unbounded. Tasks completed more than 3 days ago are still in the database and accessible via the List view; they simply don't appear in the Kanban board.

### List view (`Tasks.vue`)

Chip bar rendered above `<ListViewBuilder>`. When a chip is active, pushes standard Frappe filters into `listViewRef.value?.list`:

- **Overdue:** `[["due_date", "<", today], ["status", "!=", "Done"]]`
- **Due Today:** `[["due_date", "=", today]]`

Clearing a chip removes those injected filters. Does not interfere with saved views or column sorts.

---

## Section 4 — TaskDueAlertPanel Component

**File:** `desk/src/components/TaskDueAlertPanel.vue` (new)  
**Mounted in:** `desk/src/components/layouts/DesktopLayout.vue` (always present, all pages)

### Visual

- Fixed position: `bottom-right`, high `z-index` (above all page content)
- Slide-in animation from bottom-right on first appearance
- Dark header bar: `⏰ N task(s) need attention` + dismiss ✕
- Task list: scrollable if > ~4 tasks

### Per-task row

```
[● colour dot] [Title]                        
[● Overdue by N days | Due today]  [· #TICKET]
[15 min] [1 hr] [Tmrw 9am] [✓ Done]
```

- Red dot + red text = overdue
- Amber dot + amber text = due today
- Ticket reference shown if task has a linked ticket
- Snooze/Done buttons inline per task

### Auto-hide

Panel hides itself when the visible task list is empty (all snoozed or marked done). No empty state is rendered.

---

## Files Changed / Created

| File | Change |
|------|--------|
| `helpdesk/helpdesk/doctype/hd_task/hd_task.py` | Add `get_my_due_tasks()` |
| `desk/src/composables/useTaskDueAlerts.ts` | New — timer loop, snooze localStorage logic |
| `desk/src/components/TaskDueAlertPanel.vue` | New — slide-in panel component |
| `desk/src/components/layouts/DesktopLayout.vue` | Mount `<TaskDueAlertPanel>` |
| `desk/src/pages/tasks/KanbanView.vue` | Add Overdue / Due Today filter chips |
| `desk/src/pages/tasks/Tasks.vue` | Add Overdue / Due Today chip bar above ListViewBuilder |

---

## Out of Scope

- Multi-assignee per task (current single `assigned_to` field is sufficient)
- Browser push / OS notifications (WhatsApp reminders already cover background alerts)
- Syncing snooze state across devices/browsers (localStorage is per-browser by design)
- Admin view of other agents' due tasks
