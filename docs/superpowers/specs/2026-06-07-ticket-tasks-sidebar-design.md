# Ticket Tasks Sidebar — Design Spec

**Date:** 2026-06-07
**Status:** Approved

## Context

WABA tickets (those without a `baileys_jid`) currently have no way to attach or view tasks. WA Line chats already have a task panel, but tickets created through the WhatsApp Business API are ticket-centric and do not have a per-JID conversation anchor. Tasks need to be scoped to the **HD Customer** so that all tickets for the same company share a unified task view.

## Goals

1. Add a `customer` field to `HD Task` so tasks can be associated with a customer across tickets.
2. Show a **Tasks tab** in the ticket right sidebar with an open-task count badge.
3. The tab renders a task list (all tasks for the ticket's customer) with inline task creation.
4. Clicking a task opens the `KanbanTaskPanel` detail view in-place.
5. Show the agent's Frappe **username** (not email) in the assignee field of the task list rows.

## Data Model

### `HD Task` — new field

| fieldname | fieldtype | label   | options     |
|-----------|-----------|---------|-------------|
| customer  | Link      | Customer | HD Customer |

Auto-populated on task creation from a ticket: `task.customer = ticket.customer`.

Existing WA Line tasks (linked via `baileys_jid`) are unaffected.

## Backend

### New file: `helpdesk/helpdesk/doctype/hd_task/api.py`

All functions are `@frappe.whitelist()`.

#### `get_tasks_for_customer(customer: str) -> list[dict]`

Returns all HD Tasks where `customer = customer`, ordered by `creation desc`.

Fields returned per task:
- `name`, `title`, `status`, `priority`, `assigned_to`, `due_date`, `ticket`

Enriched with `assigned_to_username`: batch-fetch `User.username` for all `assigned_to` emails in a single query.

```python
assigned_emails = [t.assigned_to for t in tasks if t.assigned_to]
username_map = {
    r.name: r.username
    for r in frappe.get_all("User",
        filters={"name": ["in", assigned_emails]},
        fields=["name", "username"])
}
for t in tasks:
    t["assigned_to_username"] = username_map.get(t.assigned_to, "")
```

#### `create_task_for_ticket(ticket: str, title: str) -> str`

Creates HD Task with:
- `title = title`
- `ticket = ticket`
- `customer = frappe.db.get_value("HD Ticket", ticket, "customer")`

Returns the new task's `name`.

## Frontend

### `TicketSidebar.vue` — replace `TabButtons` with custom tab bar

The existing `TabButtons` component does not support badge slots. Replace the tab bar with a simple custom render that visually matches the current style but adds a count pill to the Tasks tab.

Tab definitions become reactive (computed) so the badge count updates after task operations:

```ts
const taskCount = ref(0)  // refreshed by TicketTasksTab via emit
const tabs = computed(() => [
  { value: "details", label: "Details" },
  { value: "contact", label: "Contact" },
  { value: "tasks",   label: "Tasks", count: taskCount.value },
])
```

Badge pill: `bg-blue-600 text-white rounded-full text-[9px] font-bold px-1.5 py-0.5` — only rendered when `count > 0`.

`TicketTasksTab` is mounted (not lazy) so it can fetch and emit its count even when not the active tab. Count updates propagate up via `@countChange="taskCount = $event"`.

### New component: `TicketTasksTab.vue`

**Props:** none (injects `TicketSymbol`)

**Internal state:**
```ts
const view = ref<'list' | 'detail'>('list')
const selectedTaskId = ref<string>('')
```

**List view:**
- Calls `get_tasks_for_customer(ticket.doc.customer)` on mount and after mutations.
- Emits `countChange` with non-Done task count on each load.
- Renders task rows:
  - Status dot (colour matches KanbanView convention: Backlog=gray, Todo=blue, In Progress=orange, Done=green)
  - Title (line-through if Done)
  - Assignee username in `text-ink-gray-5` (empty string if unassigned)
  - Due date in `text-red-500` if overdue
  - Chevron icon on right
- `+ New task` inline form at the bottom of the list: text input, Enter/Create/Cancel. Calls `create_task_for_ticket(ticket.doc.name, title)`, reloads list on success.
- Empty state: "No tasks for this customer yet."
- If `ticket.doc.customer` is empty: "No customer linked to this ticket."

**Detail view:**
- Renders existing `KanbanTaskPanel` with `taskId = selectedTaskId`.
- `← Tasks` back button at top returns to list view.
- On `@saved`: reloads the task list and re-emits count.

### No changes to `KanbanTaskPanel.vue`

The panel is reused as-is. Its `Link` component for `assigned_to` shows `agent_name` via `HD Agent.title_field` — acceptable for the edit widget. The display-only list rows in `TicketTasksTab` use `assigned_to_username` from the API.

## Assignee Username

The task list rows display `assigned_to_username` (Frappe `User.username`, e.g. `sarah.k`) rather than the email key stored in `HD Task.assigned_to`. This is a read display only; the edit widget in `KanbanTaskPanel` still operates on the HD Agent link value.

## Refresh Behaviour

| Event | Action |
|-------|--------|
| Tab mounts | Fetch tasks, emit count |
| Task created | Reload tasks, re-emit count |
| Task saved in detail view | Reload tasks, re-emit count |
| Ticket changes (route change) | Component remounts, refetches |

## Out of Scope

- Editing the `customer` field directly from the task detail panel in this sidebar (the full `KanbanTaskPanel` page supports it).
- Showing tasks for WA Line tickets in this sidebar (those use `WaChatTasksPanel` already).
- Badge on the ticket list / ticket header (can be added later).
