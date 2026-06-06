# Manager Task Digest & Team Health Dashboard — Design

**Date:** 2026-06-06
**Status:** Approved design, pending implementation plan
**Module:** Helpdesk → HD Task

## Problem

The team struggles to keep tasks from falling through the cracks. The specific,
validated pain is **visibility/accountability at the manager level**: tasks go
overdue (or are about to) and nobody notices until it's too late. The HD Task
module is already feature-capable (statuses, assignee, priority, due
date/time, Kanban/List/Calendar views, per-assignee WhatsApp reminders) — so
this is not a missing-feature problem. It is a "manager can't see slips early
enough" problem.

## Goals

- Give managers a **daily digest** of tasks that are overdue or due soon,
  delivered over **both WhatsApp and in-app notification**.
- Give managers a **pull dashboard** ("Team Health") to scan team task status on
  demand, grouped by assignee.
- Reuse existing reminder/notification/list infrastructure; add minimal new
  surface area.

## Non-Goals (YAGNI)

- No "stalled in progress" or "unassigned" *alert* triggers (only Overdue and
  Due Soon trigger the digest). Unassigned-but-due is shown on the dashboard as
  a count only.
- No per-team-lead scoping. One configurable recipient list covers all tasks.
- No real-time / per-task manager alerts. Daily digest only.
- No digest send-log / audit DocType (deferred; see Future Work).
- No Playwright e2e test in this scope (deferred; see Future Work).

## Decisions (from brainstorming)

| Question | Decision |
|----------|----------|
| Core pain | Things slip through cracks (visibility/accountability) |
| Whose view | Manager oversight |
| Mechanism | Both proactive alerts **and** a dashboard |
| Slip rules | **Overdue** and **Due soon** only |
| Cadence | **Daily digest** |
| Channel | **Both** WhatsApp **and** in-app |
| Recipients | **Configurable recipient list** (agents and/or extra phone numbers) |

## Architecture

Three pieces, all in the `HD Task` doctype area plus one scheduler hook:

1. **Config** — new singleton `HD Task Settings` + child table `HD Task Digest
   Recipient`.
2. **Engine** — `send_manager_task_digest()` daily scheduler function in
   `hd_task.py`, plus a shared query helper used by both the digest and the
   dashboard API.
3. **Dashboard** — a manager-only "Team Health" view added to the existing Tasks
   page, backed by `get_team_task_health()`.

### Reused infrastructure (not rebuilt)

- `daily` scheduler slot in `hooks.py`
- `send_wa_reply()` (outbound WhatsApp) from `wa.py`
- `_get_agent_phone()` from `hd_task.py`
- Existing in-app notification system + bell store
- `ListViewBuilder` and the existing quick-filter bar / view-switcher in
  `Tasks.vue`
- `isManager` from `useAuthStore`
- `due_time` HH:MM formatting logic currently inline in
  `send_due_task_wpa_notifications` (to be factored into a shared helper)

## Data Model

### New singleton: `HD Task Settings`

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `enable_manager_digest` | Check | 0 | Master on/off for the digest |
| `due_soon_window_hours` | Int | 48 | How far ahead "due soon" looks |
| `digest_recipients` | Table → `HD Task Digest Recipient` | — | Who receives the digest |

### New child table: `HD Task Digest Recipient`

| Field | Type | Purpose |
|-------|------|---------|
| `agent` | Link → HD Agent (optional) | In-app notification target + phone lookup |
| `phone` | Data (optional) | Explicit/extra WhatsApp number when no agent |

Validation: each row must have at least one of `agent` or `phone`.

## Backend

### Shared query helper

A single helper (e.g. `_get_due_and_overdue_tasks(window_hours)`) returns two
lists so the digest and the dashboard never drift:

- **Overdue:** `due_date < today AND status != 'Done'`
- **Due soon:** `today <= due_date <= today + window AND status != 'Done'`

Selected fields: `name, title, assigned_to, due_date, due_time, priority,
ticket`. Results sorted by due date; both lists groupable by `assigned_to`
(with an explicit unassigned bucket).

Uses Frappe `nowdate()` / site timezone, consistent with the existing reminder
job. Queries wrapped defensively per the project's migration-safety convention.

### Digest function: `send_manager_task_digest()`

Wired to `scheduler_events["daily"]` in `hooks.py`. Flow:

1. Load `HD Task Settings`; return early if `enable_manager_digest` is off or
   `digest_recipients` is empty.
2. Call the shared query helper with `due_soon_window_hours`.
3. If both lists empty → send nothing (no "all clear" message).
4. Build one digest payload: counts (overdue, due soon) + the two lists grouped
   by assignee.
5. For each recipient:
   - Resolve phone: `recipient.phone` else `_get_agent_phone(recipient.agent)`.
   - If a phone resolves → send WhatsApp via `send_wa_reply()` with a short
     plain-text summary (counts + top few tasks as `title · assignee · due`,
     plus a `+N more` tail).
   - If `recipient.agent` is set → create an in-app notification
     (*"3 overdue, 2 due soon — review Team Health"*) linking to the dashboard.
6. Each recipient send wrapped in try/except; failures `frappe.log_error`'d with
   recipient context and never abort the loop or the scheduler.

**No `wpa_notified`-style flag.** That flag makes per-task reminders fire once;
the digest is a daily snapshot and *should* re-list an unresolved task each day.
The daily cadence is the only double-send guard.

### Dashboard API: `get_team_task_health()`

Whitelisted read endpoint in `hd_task.py`. Returns the same overdue/due-soon
data (via the shared helper) plus the unassigned-but-due count. Role-gated
server-side to Agent Manager / System Manager (not just UI-hidden).

## Frontend — "Team Health" view

Added to the existing Tasks page (`Tasks.vue`) alongside List/Calendar/Kanban,
visible only when `isManager` is true.

- **Top strip:** three clickable counts — Overdue, Due Soon, Unassigned-but-due
  — each filters the list below.
- **Body:** task list **grouped by assignee** (unassigned bucket last), each
  group's items sorted by due date. Reuses `ListViewBuilder` data so columns,
  row-click → `TaskDetail`, and styling stay consistent.
- **Route:** `/helpdesk/tasks?view=team-health`; in-app notification deep-links
  here.

Rationale for grouping by assignee: the core problem is accountability — a
manager wants "whose plate is this slipping on," not a flat list.

## Edge Cases & Error Handling

- Disabled / no recipients / nothing due → silent no-op.
- Recipient with agent but no resolvable phone and no `phone` override → in-app
  notification only; WhatsApp skipped; warning logged.
- WhatsApp not configured on the site → per-recipient WhatsApp send fails
  gracefully; in-app still delivered; scheduler never crashes.
- `due_time` from MySQL is a timedelta → formatted HH:MM via the shared helper
  factored out of `send_due_task_wpa_notifications`.
- Permissions enforced server-side on `get_team_task_health()`.
- Settings load and task queries wrapped defensively (mirrors the existing
  `baileys_jid` try/except convention for un-migrated sites).

## Testing

Unit tests in `test_hd_task.py`:

- Query helper partitions overdue vs due-soon correctly around the window
  boundary: cases for due today, due at `window-1h`, due at `window+1h`, overdue
  by 1 day, and `status='Done'` excluded.
- Digest no-ops when disabled, when no recipients, and when nothing is due.
- Recipient with no resolvable phone → in-app only, no crash.
- Grouping by assignee includes an explicit unassigned bucket.

Send-layer tests:

- Mock `send_wa_reply` and the notification call; assert one call per recipient
  with the expected summary, and that one recipient raising does not stop the
  others.

Manual smoke:

- Seed a couple overdue/due-soon tasks, run via `bench execute
  helpdesk.helpdesk.doctype.hd_task.hd_task.send_manager_task_digest`, confirm
  WhatsApp message, notification bell, and the Team Health tab all render.

## Future Work (out of scope)

- Playwright e2e: assert the manager Team Health tab lists overdue tasks.
- `HD Task Digest Log` DocType for send auditing/retry (Approach B).
- Real-time per-task manager alerts and/or per-team-lead scoping.
- "Stalled in progress" and "unassigned" as digest triggers.
