# Task Due-Date Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a slide-in due-task alert panel (with snooze), "Overdue"/"Due Today" filter chips on Kanban and List views, and cap the Kanban Done column to the last 3 days.

**Architecture:** A new `get_my_due_tasks()` backend API feeds a `useTaskDueAlerts` composable that polls every 5 minutes. The composable drives a fixed-position `TaskDueAlertPanel.vue` mounted in `DesktopLayout.vue`. Snooze state is stored in `localStorage` keyed per user+task. Filter chips are client-side in KanbanView and server-side in Tasks (List view).

**Tech Stack:** Python/Frappe (backend), Vue 3 Composition API, frappe-ui `call`/`createListResource`, `dayjs`, Tailwind/frappe-ui semantic tokens, `localStorage`.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `helpdesk/helpdesk/doctype/hd_task/hd_task.py` | Modify | Add `get_my_due_tasks()` API |
| `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py` | Modify | Tests for `get_my_due_tasks()` |
| `desk/src/composables/useTaskDueAlerts.ts` | Create | Timer loop, API call, snooze localStorage logic |
| `desk/src/components/TaskDueAlertPanel.vue` | Create | Slide-in panel component |
| `desk/src/components/layouts/DesktopLayout.vue` | Modify | Mount `<TaskDueAlertPanel>` |
| `desk/src/pages/tasks/KanbanView.vue` | Modify | Overdue/Due Today chips + Done 3-day window |
| `desk/src/pages/tasks/Tasks.vue` | Modify | Overdue/Due Today chips above ListViewBuilder |

---

## Task 1: Backend API — `get_my_due_tasks()`

**Files:**
- Modify: `helpdesk/helpdesk/doctype/hd_task/hd_task.py`
- Modify: `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py`

- [ ] **Step 1: Add the failing tests to `test_hd_task.py`**

Add these four test methods to the existing `TestHDTask` class (after the existing tests):

```python
def test_get_my_due_tasks_returns_overdue(self):
    from helpdesk.helpdesk.doctype.hd_task.hd_task import get_my_due_tasks
    task = frappe.get_doc({
        "doctype": "HD Task",
        "title": "Overdue sentinel",
        "status": "Todo",
    }).insert(ignore_permissions=True)
    frappe.db.set_value("HD Task", task.name, {
        "assigned_to": frappe.session.user,
        "due_date": frappe.utils.add_days(frappe.utils.today(), -1),
    })
    try:
        results = get_my_due_tasks()
        names = [r["name"] for r in results]
        self.assertIn(task.name, names)
    finally:
        frappe.delete_doc("HD Task", task.name, ignore_permissions=True, force=True)

def test_get_my_due_tasks_excludes_done(self):
    from helpdesk.helpdesk.doctype.hd_task.hd_task import get_my_due_tasks
    task = frappe.get_doc({
        "doctype": "HD Task",
        "title": "Done sentinel",
        "status": "Done",
    }).insert(ignore_permissions=True)
    frappe.db.set_value("HD Task", task.name, {
        "assigned_to": frappe.session.user,
        "due_date": frappe.utils.add_days(frappe.utils.today(), -1),
    })
    try:
        results = get_my_due_tasks()
        names = [r["name"] for r in results]
        self.assertNotIn(task.name, names)
    finally:
        frappe.delete_doc("HD Task", task.name, ignore_permissions=True, force=True)

def test_get_my_due_tasks_excludes_future(self):
    from helpdesk.helpdesk.doctype.hd_task.hd_task import get_my_due_tasks
    task = frappe.get_doc({
        "doctype": "HD Task",
        "title": "Future sentinel",
        "status": "Todo",
    }).insert(ignore_permissions=True)
    frappe.db.set_value("HD Task", task.name, {
        "assigned_to": frappe.session.user,
        "due_date": frappe.utils.add_days(frappe.utils.today(), 2),
    })
    try:
        results = get_my_due_tasks()
        names = [r["name"] for r in results]
        self.assertNotIn(task.name, names)
    finally:
        frappe.delete_doc("HD Task", task.name, ignore_permissions=True, force=True)

def test_get_my_due_tasks_excludes_other_user(self):
    from helpdesk.helpdesk.doctype.hd_task.hd_task import get_my_due_tasks
    task = frappe.get_doc({
        "doctype": "HD Task",
        "title": "Other user sentinel",
        "status": "Todo",
    }).insert(ignore_permissions=True)
    frappe.db.set_value("HD Task", task.name, {
        "assigned_to": "someone.else@example.com",
        "due_date": frappe.utils.add_days(frappe.utils.today(), -1),
    })
    try:
        results = get_my_due_tasks()
        names = [r["name"] for r in results]
        self.assertNotIn(task.name, names)
    finally:
        frappe.delete_doc("HD Task", task.name, ignore_permissions=True, force=True)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk --module helpdesk.helpdesk.doctype.hd_task.test_hd_task
```

Expected: 4 new tests fail with `ImportError` or `AttributeError: module has no attribute get_my_due_tasks`.

- [ ] **Step 3: Add `get_my_due_tasks()` to `hd_task.py`**

Add after the existing `search_tasks` function:

```python
@frappe.whitelist()
def get_my_due_tasks() -> list[dict]:
	"""Return tasks assigned to the current user that are overdue or due today, status != Done."""
	return frappe.get_list(
		"HD Task",
		filters=[
			["assigned_to", "=", frappe.session.user],
			["due_date", "<=", frappe.utils.today()],
			["status", "!=", "Done"],
		],
		fields=["name", "title", "due_date", "due_time", "status", "ticket"],
		order_by="due_date asc",
	)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk --module helpdesk.helpdesk.doctype.hd_task.test_hd_task
```

Expected: all tests in `TestHDTask` pass, including the 4 new ones.

- [ ] **Step 5: Restart and smoke-test in the console**

```bash
cd /home/frappeuser/bench16
bench --site site16.local console
```

```python
frappe.session.user = "Administrator"
from helpdesk.helpdesk.doctype.hd_task.hd_task import get_my_due_tasks
print(get_my_due_tasks())  # should return [] or a list of dicts
```

- [ ] **Step 6: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/helpdesk/doctype/hd_task/hd_task.py \
        helpdesk/helpdesk/doctype/hd_task/test_hd_task.py
git commit -m "feat: add get_my_due_tasks API for due-task alert panel"
```

---

## Task 2: Composable — `useTaskDueAlerts.ts`

**Files:**
- Create: `desk/src/composables/useTaskDueAlerts.ts`

- [ ] **Step 1: Create `desk/src/composables/useTaskDueAlerts.ts`**

```typescript
import { call, dayjs } from "frappe-ui"
import { onMounted, onUnmounted, ref } from "vue"

export interface DueTask {
	name: string
	title: string
	due_date: string
	due_time: string | null
	status: string
	ticket: string | null
}

const SNOOZE_PREFIX = "hd_task_snooze__"
const POLL_INTERVAL_MS = 5 * 60 * 1000

export function useTaskDueAlerts() {
	const dueTasks = ref<DueTask[]>([])
	const visible = ref(false)
	let timer: ReturnType<typeof setInterval> | null = null

	function snoozeKey(taskName: string): string {
		const user = (window as any).frappe?.session?.user ?? "user"
		return `${SNOOZE_PREFIX}${user}__${taskName}`
	}

	function isSnoozed(taskName: string): boolean {
		const val = localStorage.getItem(snoozeKey(taskName))
		if (!val) return false
		return dayjs().isBefore(dayjs(val))
	}

	function snooze(taskName: string, minutes: number | "tomorrow9am") {
		let until: string
		if (minutes === "tomorrow9am") {
			until = dayjs().add(1, "day").startOf("day").add(9, "hour").toISOString()
		} else {
			until = dayjs().add(minutes, "minute").toISOString()
		}
		localStorage.setItem(snoozeKey(taskName), until)
		dueTasks.value = dueTasks.value.filter((t) => t.name !== taskName)
		if (dueTasks.value.length === 0) visible.value = false
	}

	async function markDone(taskName: string) {
		await call("helpdesk.helpdesk.doctype.hd_task.hd_task.set_task_field", {
			task_name: taskName,
			fieldname: "status",
			value: "Done",
		})
		dueTasks.value = dueTasks.value.filter((t) => t.name !== taskName)
		if (dueTasks.value.length === 0) visible.value = false
	}

	async function check() {
		const tasks: DueTask[] = await call(
			"helpdesk.helpdesk.doctype.hd_task.hd_task.get_my_due_tasks"
		)
		const active = (tasks ?? []).filter((t) => !isSnoozed(t.name))
		dueTasks.value = active
		if (active.length > 0) visible.value = true
	}

	function dismiss() {
		visible.value = false
	}

	onMounted(() => {
		check()
		timer = setInterval(check, POLL_INTERVAL_MS)
	})

	onUnmounted(() => {
		if (timer) clearInterval(timer)
	})

	return { dueTasks, visible, snooze, markDone, dismiss }
}
```

- [ ] **Step 2: Export from composables index**

Open `desk/src/composables/index.ts` and add:

```typescript
export { useTaskDueAlerts } from "./useTaskDueAlerts"
```

- [ ] **Step 3: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/composables/useTaskDueAlerts.ts \
        desk/src/composables/index.ts
git commit -m "feat: useTaskDueAlerts composable — 5-min poll + localStorage snooze"
```

---

## Task 3: Panel Component — `TaskDueAlertPanel.vue`

**Files:**
- Create: `desk/src/components/TaskDueAlertPanel.vue`

- [ ] **Step 1: Create `desk/src/components/TaskDueAlertPanel.vue`**

```vue
<template>
  <Transition name="slide-up">
    <div
      v-if="visible && dueTasks.length"
      class="fixed bottom-4 right-4 z-50 w-72 rounded-xl shadow-2xl border border-outline-gray-2 overflow-hidden bg-surface-white"
    >
      <!-- Header -->
      <div class="flex items-center justify-between px-3 py-2.5 bg-ink-gray-9">
        <div class="flex items-center gap-2">
          <span class="text-sm">⏰</span>
          <span class="text-xs font-semibold text-surface-white">
            {{ dueTasks.length }} task{{ dueTasks.length > 1 ? "s" : "" }}
            need{{ dueTasks.length === 1 ? "s" : "" }} attention
          </span>
        </div>
        <button
          class="text-ink-gray-5 hover:text-surface-white transition-colors"
          @click="dismiss"
        >
          <LucideX class="h-3.5 w-3.5" />
        </button>
      </div>

      <!-- Task list -->
      <div class="max-h-80 overflow-y-auto divide-y divide-outline-gray-1">
        <div v-for="task in dueTasks" :key="task.name" class="px-3 py-2.5">
          <!-- Title -->
          <p class="text-xs font-semibold text-ink-gray-9 leading-snug mb-1">
            {{ task.title }}
          </p>
          <!-- Due label + ticket ref -->
          <div class="flex items-center gap-1.5 mb-2">
            <span
              class="h-1.5 w-1.5 rounded-full flex-shrink-0"
              :class="dueColor(task.due_date).dot"
            />
            <span class="text-[10px] font-medium" :class="dueColor(task.due_date).text">
              {{ dueLabel(task.due_date) }}
            </span>
            <span v-if="task.ticket" class="text-[10px] text-ink-gray-4">
              · #{{ task.ticket }}
            </span>
          </div>
          <!-- Snooze + Done buttons -->
          <div class="flex gap-1.5 flex-wrap">
            <button
              v-for="opt in snoozeOptions"
              :key="opt.label"
              class="px-2 py-1 text-[10px] font-medium rounded border border-outline-gray-2 bg-surface-gray-1 text-ink-gray-6 hover:bg-surface-gray-2 transition-colors"
              @click="snooze(task.name, opt.value)"
            >
              {{ opt.label }}
            </button>
            <button
              class="px-2 py-1 text-[10px] font-medium rounded border border-green-200 bg-green-50 text-green-700 hover:bg-green-100 transition-colors"
              @click="markDone(task.name)"
            >
              ✓ Done
            </button>
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { useTaskDueAlerts } from "@/composables/useTaskDueAlerts"
import { dayjs } from "frappe-ui"
import LucideX from "~icons/lucide/x"

const { dueTasks, visible, snooze, markDone, dismiss } = useTaskDueAlerts()

const snoozeOptions: Array<{ label: string; value: number | "tomorrow9am" }> = [
  { label: "15 min",    value: 15 },
  { label: "1 hr",      value: 60 },
  { label: "Tmrw 9am",  value: "tomorrow9am" },
]

function dueColor(due_date: string): { dot: string; text: string } {
  const today = dayjs().startOf("day")
  const due = dayjs(due_date).startOf("day")
  if (due.isBefore(today)) return { dot: "bg-red-500",   text: "text-red-500" }
  return                          { dot: "bg-amber-400", text: "text-amber-500" }
}

function dueLabel(due_date: string): string {
  const today = dayjs().startOf("day")
  const due = dayjs(due_date).startOf("day")
  const diff = due.diff(today, "day")
  if (diff === 0)  return "Due today"
  if (diff === -1) return "Overdue by 1 day"
  return `Overdue by ${Math.abs(diff)} days`
}
</script>

<style scoped>
.slide-up-enter-active,
.slide-up-leave-active {
  transition: transform 0.25s ease, opacity 0.25s ease;
}
.slide-up-enter-from,
.slide-up-leave-to {
  transform: translateY(12px);
  opacity: 0;
}
</style>
```

- [ ] **Step 2: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/components/TaskDueAlertPanel.vue
git commit -m "feat: TaskDueAlertPanel — slide-in due-task alert with snooze"
```

---

## Task 4: Mount Panel in `DesktopLayout.vue`

**Files:**
- Modify: `desk/src/components/layouts/DesktopLayout.vue`

- [ ] **Step 1: Update `DesktopLayout.vue`**

Replace the entire file content with:

```vue
<template>
  <div class="flex h-screen w-screen bg-surface-gray-1">
    <Sidebar />
    <div class="flex-1 flex flex-col h-full overflow-auto">
      <AppHeader />
      <slot />
    </div>
    <Notifications />
    <CommandPalette />
    <TaskDueAlertPanel />
  </div>
</template>

<script setup>
import { Notifications, CommandPalette } from "@/components";
import AppHeader from "./AppHeader.vue";
import Sidebar from "./Sidebar.vue";
import TaskDueAlertPanel from "@/components/TaskDueAlertPanel.vue";
</script>
```

- [ ] **Step 2: Build and verify panel appears**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk
```

Open `http://localhost:8002` → log in → navigate to Tasks. Create a task with `due_date` set to yesterday and `assigned_to` set to your own agent record. Wait up to 5 minutes (or temporarily set `POLL_INTERVAL_MS = 10_000` in the composable). The panel should slide in from the bottom-right.

Verify:
- Header shows "1 task needs attention"
- Task title and "Overdue by 1 day" in red are visible
- Clicking "15 min" removes the task from the panel
- Reloading the page within 15 min should not show the panel (snooze still active)
- After snooze expires (or clearing the `hd_task_snooze__*` key from DevTools > Application > Local Storage), panel re-appears on next poll

- [ ] **Step 3: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/components/layouts/DesktopLayout.vue
git commit -m "feat: mount TaskDueAlertPanel in DesktopLayout"
```

---

## Task 5: Kanban — Filter Chips + Done 3-Day Window

**Files:**
- Modify: `desk/src/pages/tasks/KanbanView.vue`

- [ ] **Step 1: Add `modified` to the task list fields**

In `KanbanView.vue`, find the `createListResource` call (around line 290) and add `"modified"` to `fields`:

```typescript
const tasks = createListResource({
  doctype: "HD Task",
  fields: ["name", "title", "status", "priority", "due_date", "assigned_to", "ticket", "_user_tags", "modified"],
  filters: [],
  orderBy: "modified desc",
  pageLength: 999,
  auto: true,
});
```

- [ ] **Step 2: Add filter state refs for Overdue and Due Today**

After the existing filter state block (around line 324, after `const filterTag = ref("")`), add:

```typescript
const filterOverdue = ref(false);
const filterDueToday = ref(false);
```

- [ ] **Step 3: Update `hasFilters` computed and `clearFilters()`**

Replace:
```typescript
const hasFilters = computed(() => !!(filterSearch.value || filterAssignee.value || filterTag.value));
```
With:
```typescript
const hasFilters = computed(() => !!(filterSearch.value || filterAssignee.value || filterTag.value || filterOverdue.value || filterDueToday.value));
```

Replace `clearFilters()`:
```typescript
function clearFilters() {
  filterSearch.value = "";
  filterAssignee.value = "";
  filterTag.value = "";
  filterOverdue.value = false;
  filterDueToday.value = false;
  searchResultNames.value = null;
}
```

- [ ] **Step 4: Update `getCardsForStatus()` for chips + Done window**

Replace the existing `getCardsForStatus` function:

```typescript
function getCardsForStatus(status: string) {
  const today = dayjs().format("YYYY-MM-DD");
  const threeDaysAgo = dayjs().subtract(3, "day").startOf("day");

  return (tasks.data ?? []).filter((t: any) => {
    if (t.status !== status) return false;

    // Done column: only last 3 days (by modified timestamp)
    if (status === "Done" && t.modified) {
      if (dayjs(t.modified).isBefore(threeDaysAgo)) return false;
    }

    // Overdue chip: due_date < today, skip Done column entirely
    if (filterOverdue.value) {
      if (status === "Done") return false;
      if (!t.due_date || t.due_date >= today) return false;
    }

    // Due Today chip
    if (filterDueToday.value) {
      if (!t.due_date || t.due_date !== today) return false;
    }

    if (searchResultNames.value !== null && !searchResultNames.value.has(t.name)) return false;
    if (filterAssignee.value && t.assigned_to !== filterAssignee.value) return false;
    if (filterTag.value) {
      const tags = (t._user_tags ?? "").split(",").map((x: string) => x.trim()).filter(Boolean);
      if (!tags.includes(filterTag.value)) return false;
    }
    return true;
  });
}
```

- [ ] **Step 5: Update the columns array to rename Done**

Find the `columns` array (around line 282) and change the Done entry:

```typescript
const columns = [
  { status: "Backlog",     dotClass: "bg-gray-400"   },
  { status: "Todo",        dotClass: "bg-blue-400"   },
  { status: "In Progress", dotClass: "bg-orange-400" },
  { status: "Done",        dotClass: "bg-green-400", label: "Done (last 3 days)" },
];
```

Then in the column header template, replace the status text span. Find:
```html
<span class="text-sm font-semibold text-ink-gray-8">{{ col.status }}</span>
```
Replace with:
```html
<span class="text-sm font-semibold text-ink-gray-8">{{ col.label ?? col.status }}</span>
```

- [ ] **Step 6: Add chip buttons to the filter bar**

In the filter bar `<div>` (the bar containing the search, assignee, and tag inputs), add the two chips after the tag filter and before the "Clear all" button:

```html
<!-- Overdue chip -->
<button
  class="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded border transition-colors"
  :class="filterOverdue
    ? 'bg-red-50 border-red-300 text-red-600 font-semibold'
    : 'bg-surface-white border-outline-gray-2 text-ink-gray-6 hover:border-outline-gray-4'"
  @click="filterDueToday = false; filterOverdue = !filterOverdue"
>
  <LucideAlertCircle class="h-3 w-3" />
  {{ __('Overdue') }}
</button>

<!-- Due Today chip -->
<button
  class="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded border transition-colors"
  :class="filterDueToday
    ? 'bg-amber-50 border-amber-300 text-amber-600 font-semibold'
    : 'bg-surface-white border-outline-gray-2 text-ink-gray-6 hover:border-outline-gray-4'"
  @click="filterOverdue = false; filterDueToday = !filterDueToday"
>
  <LucideCalendarClock class="h-3 w-3" />
  {{ __('Due Today') }}
</button>
```

- [ ] **Step 7: Import the two new icons**

At the top of the `<script setup>` imports block, add:

```typescript
import LucideAlertCircle from "~icons/lucide/alert-circle";
import LucideCalendarClock from "~icons/lucide/calendar-clock";
```

- [ ] **Step 8: Build and manually verify**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk
```

Open Tasks → Kanban view. Verify:
- Done column header reads "Done (last 3 days)"
- Tasks marked Done more than 3 days ago are not shown
- Clicking "Overdue" highlights the chip red and hides cards without a past due date
- Clicking "Due Today" highlights the chip amber and shows only today's cards
- Chips are mutually exclusive (clicking one clears the other)
- "Clear all" resets both chips

- [ ] **Step 9: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/pages/tasks/KanbanView.vue
git commit -m "feat: kanban Overdue/Due Today chips + Done column 3-day window"
```

---

## Task 6: List View — Filter Chips in `Tasks.vue`

**Files:**
- Modify: `desk/src/pages/tasks/Tasks.vue`

- [ ] **Step 1: Add icon imports and quick filter state**

In `Tasks.vue`, add to the icon imports at the top of `<script setup>`:

```typescript
import LucideAlertCircle from "~icons/lucide/alert-circle";
import LucideCalendarClock from "~icons/lucide/calendar-clock";
```

Add `watch` to the existing Vue import:

```typescript
import { computed, h, onMounted, reactive, ref, watch } from "vue";
```

Add after the `const listViewRef = ref(null)` line:

```typescript
const quickFilter = ref<"overdue" | "due-today" | null>(null);
```

- [ ] **Step 2: Add a `watch` to inject filters into the list resource**

Add directly after the `quickFilter` ref:

```typescript
watch(quickFilter, () => {
  if (!listViewRef.value?.list) return;
  const today = new Date().toISOString().slice(0, 10);
  const filters =
    quickFilter.value === "overdue"
      ? [["due_date", "<", today], ["status", "!=", "Done"]]
      : quickFilter.value === "due-today"
      ? [["due_date", "=", today]]
      : [];
  listViewRef.value.list.params.filters = filters;
  listViewRef.value.list.reload();
});
```

- [ ] **Step 3: Replace the `v-else` ListViewBuilder block with a chip bar + list wrapper**

In the template, find and replace:

```html
    <ListViewBuilder
      v-else
      ref="listViewRef"
      :options="options"
      @empty-state-action="() => $router.push({ name: 'TaskAgentNew' })"
      @row-click="
        (row) => $router.push({ name: 'TaskAgent', params: { taskId: row } })
      "
    />
```

With:

```html
    <template v-else>
      <div class="flex items-center gap-2 px-4 py-2 border-b border-outline-gray-1 bg-surface-white">
        <button
          class="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded border transition-colors"
          :class="quickFilter === 'overdue'
            ? 'bg-red-50 border-red-300 text-red-600 font-semibold'
            : 'bg-surface-white border-outline-gray-2 text-ink-gray-6 hover:border-outline-gray-4'"
          @click="quickFilter = quickFilter === 'overdue' ? null : 'overdue'"
        >
          <LucideAlertCircle class="h-3 w-3" />
          {{ __('Overdue') }}
        </button>
        <button
          class="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded border transition-colors"
          :class="quickFilter === 'due-today'
            ? 'bg-amber-50 border-amber-300 text-amber-600 font-semibold'
            : 'bg-surface-white border-outline-gray-2 text-ink-gray-6 hover:border-outline-gray-4'"
          @click="quickFilter = quickFilter === 'due-today' ? null : 'due-today'"
        >
          <LucideCalendarClock class="h-3 w-3" />
          {{ __('Due Today') }}
        </button>
      </div>
      <ListViewBuilder
        ref="listViewRef"
        :options="options"
        @empty-state-action="() => $router.push({ name: 'TaskAgentNew' })"
        @row-click="
          (row) => $router.push({ name: 'TaskAgent', params: { taskId: row } })
        "
      />
    </template>
```

- [ ] **Step 4: Build and manually verify**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk
```

Open Tasks → List view. Verify:
- Chip bar appears above the list
- Clicking "Overdue" filters to past-due, non-done tasks
- Clicking "Due Today" filters to today's tasks
- Clicking the active chip again clears the filter and reloads all tasks
- Switching to Calendar or Kanban view hides the chip bar

- [ ] **Step 5: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/pages/tasks/Tasks.vue
git commit -m "feat: Overdue/Due Today quick-filter chips on task list view"
```

---

## Done ✓

All six tasks complete. The feature is fully implemented:

| Deliverable | Task |
|-------------|------|
| `get_my_due_tasks()` API + 4 tests | Task 1 |
| `useTaskDueAlerts` composable (poll + snooze) | Task 2 |
| `TaskDueAlertPanel.vue` (slide-in panel) | Task 3 |
| Panel mounted in `DesktopLayout.vue` | Task 4 |
| Kanban chips + Done 3-day window | Task 5 |
| List view chips | Task 6 |
