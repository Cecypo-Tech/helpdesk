# Ticket Tasks Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a customer-scoped Tasks tab to the ticket right sidebar so agents can create and manage tasks linked to the ticket's HD Customer across all tickets for that company.

**Architecture:** Add a `customer` Link field to `HD Task`. New whitelisted functions in `hd_task.py` (`get_tasks_for_customer`, `create_task_for_ticket`) back a new `TicketTasksTab.vue` component. `TicketSidebar.vue` gains a custom tab bar (replaces `TabButtons`) with a live open-count badge on the Tasks tab.

**Tech Stack:** Python/Frappe ORM (backend), Vue 3 Composition API + frappe-ui (frontend), Tailwind semantic tokens.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `helpdesk/helpdesk/doctype/hd_task/hd_task.json` | Modify | Add `customer` field + update `field_order` |
| `helpdesk/helpdesk/doctype/hd_task/hd_task.py` | Modify | Add `get_tasks_for_customer`, `create_task_for_ticket` |
| `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py` | Modify | Tests for the two new API functions |
| `desk/src/components/ticket-agent/TicketTasksTab.vue` | Create | Task list + inline create + KanbanTaskPanel detail view |
| `desk/src/components/ticket-agent/TicketSidebar.vue` | Modify | Replace `TabButtons` with custom tab bar; mount `TicketTasksTab` |

---

### Task 1: Add `customer` field to HD Task doctype

**Files:**
- Modify: `helpdesk/helpdesk/doctype/hd_task/hd_task.json`

- [ ] **Step 1: Add `customer` to `field_order`**

In `hd_task.json`, replace the `field_order` array:

```json
"field_order": [
 "title",
 "status",
 "priority",
 "assigned_to",
 "due_date",
 "due_time",
 "ticket",
 "customer",
 "team",
 "description",
 "subtasks_section",
 "subtasks",
 "wpa_notified"
],
```

- [ ] **Step 2: Add the field definition**

In the `"fields"` array, after the `ticket` field block (ends with `"options": "HD Ticket"`), insert:

```json
 {
  "fieldname": "customer",
  "fieldtype": "Link",
  "label": "Customer",
  "options": "HD Customer"
 },
```

- [ ] **Step 3: Run bench migrate**

```bash
cd /home/kushal/frappe-bench
bench --site dev.localhost migrate
```

Expected: migration completes without error; `tabHD Task` now has a `customer` column.

- [ ] **Step 4: Commit**

```bash
git add helpdesk/helpdesk/doctype/hd_task/hd_task.json
git commit -m "feat(tasks): add customer Link field to HD Task"
```

---

### Task 2: Backend — `get_tasks_for_customer`

**Files:**
- Modify: `helpdesk/helpdesk/doctype/hd_task/hd_task.py`
- Test: `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py`

- [ ] **Step 1: Write the failing test**

Add this test class method to `TestHDTask` in `test_hd_task.py`:

```python
def test_get_tasks_for_customer_returns_correct_tasks(self):
    from helpdesk.helpdesk.doctype.hd_task.hd_task import get_tasks_for_customer

    # Create a customer
    cust = frappe.get_doc({
        "doctype": "HD Customer",
        "customer_name": "_Test Tasks Customer",
    }).insert(ignore_permissions=True)
    self.addCleanup(lambda: frappe.delete_doc("HD Customer", cust.name, force=True))

    # Two tasks for this customer
    t1 = frappe.get_doc({"doctype": "HD Task", "title": "Alpha", "customer": cust.name, "status": "Todo"}).insert(ignore_permissions=True)
    t2 = frappe.get_doc({"doctype": "HD Task", "title": "Beta",  "customer": cust.name, "status": "Done"}).insert(ignore_permissions=True)
    # One task for a different customer — must NOT appear
    t3 = frappe.get_doc({"doctype": "HD Task", "title": "Gamma", "status": "Todo"}).insert(ignore_permissions=True)
    self.addCleanup(lambda: frappe.delete_doc("HD Task", t1.name, force=True))
    self.addCleanup(lambda: frappe.delete_doc("HD Task", t2.name, force=True))
    self.addCleanup(lambda: frappe.delete_doc("HD Task", t3.name, force=True))

    result = get_tasks_for_customer(cust.name)
    names = [t["name"] for t in result]

    self.assertIn(t1.name, names)
    self.assertIn(t2.name, names)
    self.assertNotIn(t3.name, names)
    # Every row must carry the username enrichment key
    for t in result:
        self.assertIn("assigned_to_username", t)

def test_get_tasks_for_customer_empty_string_returns_empty(self):
    from helpdesk.helpdesk.doctype.hd_task.hd_task import get_tasks_for_customer
    self.assertEqual(get_tasks_for_customer(""), [])
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /home/kushal/frappe-bench
bench run-tests --app helpdesk --module helpdesk.helpdesk.doctype.hd_task.test_hd_task \
  --test test_get_tasks_for_customer_returns_correct_tasks 2>&1 | tail -20
```

Expected: `AttributeError` or `ImportError` — function not yet defined.

- [ ] **Step 3: Implement `get_tasks_for_customer` in `hd_task.py`**

Add at the end of `helpdesk/helpdesk/doctype/hd_task/hd_task.py`:

```python
@frappe.whitelist()
def get_tasks_for_customer(customer: str) -> list[dict]:
	"""Return all HD Tasks for the given HD Customer, enriched with assignee username."""
	if not customer:
		return []
	fields = ["name", "title", "status", "priority", "assigned_to", "due_date", "ticket", "creation"]
	tasks = frappe.get_all(
		"HD Task",
		filters={"customer": customer},
		fields=fields,
		order_by="creation desc",
	)
	assigned_emails = list({t.assigned_to for t in tasks if t.assigned_to})
	username_map: dict[str, str] = {}
	if assigned_emails:
		for row in frappe.get_all(
			"User",
			filters={"name": ["in", assigned_emails]},
			fields=["name", "username"],
		):
			username_map[row.name] = row.username or ""
	for t in tasks:
		t["assigned_to_username"] = username_map.get(t.assigned_to or "", "")
	return tasks
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd /home/kushal/frappe-bench
bench run-tests --app helpdesk --module helpdesk.helpdesk.doctype.hd_task.test_hd_task \
  --test test_get_tasks_for_customer_returns_correct_tasks 2>&1 | tail -10
bench run-tests --app helpdesk --module helpdesk.helpdesk.doctype.hd_task.test_hd_task \
  --test test_get_tasks_for_customer_empty_string_returns_empty 2>&1 | tail -10
```

Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add helpdesk/helpdesk/doctype/hd_task/hd_task.py \
        helpdesk/helpdesk/doctype/hd_task/test_hd_task.py
git commit -m "feat(tasks): add get_tasks_for_customer API with username enrichment"
```

---

### Task 3: Backend — `create_task_for_ticket`

**Files:**
- Modify: `helpdesk/helpdesk/doctype/hd_task/hd_task.py`
- Test: `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py`

- [ ] **Step 1: Write the failing test**

Add to `TestHDTask` in `test_hd_task.py`:

```python
def test_create_task_for_ticket_sets_customer_and_ticket(self):
    from unittest.mock import patch
    from helpdesk.helpdesk.doctype.hd_task.hd_task import create_task_for_ticket

    cust = frappe.get_doc({
        "doctype": "HD Customer",
        "customer_name": "_Test Create Task Customer",
    }).insert(ignore_permissions=True)
    self.addCleanup(lambda: frappe.delete_doc("HD Customer", cust.name, force=True))

    # Patch frappe.db.get_value so we don't need a real HD Ticket in the DB
    with patch("frappe.db.get_value", return_value=cust.name):
        task_name = create_task_for_ticket("FAKE-TICKET-999", "Fix the widget")

    task = frappe.get_doc("HD Task", task_name)
    self.addCleanup(lambda: frappe.delete_doc("HD Task", task_name, force=True))

    self.assertEqual(task.title, "Fix the widget")
    self.assertEqual(task.ticket, "FAKE-TICKET-999")
    self.assertEqual(task.customer, cust.name)
    self.assertEqual(task.status, "Todo")
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /home/kushal/frappe-bench
bench run-tests --app helpdesk --module helpdesk.helpdesk.doctype.hd_task.test_hd_task \
  --test test_create_task_for_ticket_sets_customer_and_ticket 2>&1 | tail -15
```

Expected: `ImportError` or `AttributeError`.

- [ ] **Step 3: Implement `create_task_for_ticket` in `hd_task.py`**

Append directly after `get_tasks_for_customer`:

```python
@frappe.whitelist()
def create_task_for_ticket(ticket: str, title: str) -> str:
	"""Create an HD Task linked to the ticket and auto-populate the customer."""
	customer = frappe.db.get_value("HD Ticket", ticket, "customer")
	task = frappe.get_doc({
		"doctype": "HD Task",
		"title": title,
		"ticket": ticket,
		"customer": customer or None,
		"status": "Todo",
	})
	task.insert(ignore_permissions=True)
	return task.name
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
cd /home/kushal/frappe-bench
bench run-tests --app helpdesk --module helpdesk.helpdesk.doctype.hd_task.test_hd_task \
  --test test_create_task_for_ticket_sets_customer_and_ticket 2>&1 | tail -10
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add helpdesk/helpdesk/doctype/hd_task/hd_task.py \
        helpdesk/helpdesk/doctype/hd_task/test_hd_task.py
git commit -m "feat(tasks): add create_task_for_ticket API"
```

---

### Task 4: Frontend — `TicketTasksTab.vue`

**Files:**
- Create: `desk/src/components/ticket-agent/TicketTasksTab.vue`

No automated frontend tests exist in this project — verify by loading a ticket in the browser after Task 5.

- [ ] **Step 1: Create `TicketTasksTab.vue`**

```vue
<template>
  <!-- List view -->
  <div v-if="view === 'list'" class="flex h-full flex-col">
    <div v-if="loading" class="flex justify-center py-8">
      <LoadingIndicator :scale="4" class="text-ink-gray-4" />
    </div>

    <div v-else-if="!customer" class="py-10 text-center text-xs text-ink-gray-5">
      No customer linked to this ticket.
    </div>

    <div v-else-if="!tasks.length" class="py-10 text-center text-xs text-ink-gray-5">
      No tasks for this customer yet.
    </div>

    <div v-else class="flex-1 divide-y divide-outline-gray-1 overflow-y-auto">
      <button
        v-for="task in tasks"
        :key="task.name"
        class="flex w-full items-start gap-2.5 px-4 py-2.5 text-left hover:bg-surface-gray-1"
        @click="openTask(task.name)"
      >
        <span class="mt-1 h-2 w-2 shrink-0 rounded-full" :class="statusDot(task.status)" />
        <div class="min-w-0 flex-1">
          <div
            class="truncate text-xs font-medium text-ink-gray-9"
            :class="{ 'line-through opacity-50': task.status === 'Done' }"
          >{{ task.title }}</div>
          <div class="mt-0.5 flex flex-wrap items-center gap-1.5">
            <span class="text-[10px] text-ink-gray-5">{{ task.status }}</span>
            <span v-if="task.assigned_to_username" class="text-[10px] text-ink-gray-4">@{{ task.assigned_to_username }}</span>
            <span
              v-if="task.due_date"
              class="text-[10px]"
              :class="isOverdue(task.due_date) ? 'font-medium text-red-500' : 'text-ink-gray-4'"
            >{{ task.due_date }}</span>
          </div>
        </div>
        <svg class="mt-0.5 h-3 w-3 shrink-0 text-ink-gray-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <polyline points="9 18 15 12 9 6"/>
        </svg>
      </button>
    </div>

    <!-- New task area -->
    <div class="shrink-0 border-t border-outline-gray-2 px-4 py-2.5">
      <div v-if="showNewTask" class="flex flex-col gap-2">
        <input
          ref="newTaskInput"
          v-model="newTaskTitle"
          type="text"
          placeholder="Task title..."
          class="w-full rounded border border-outline-gray-3 px-2 py-1.5 text-xs text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none"
          @keydown.enter="createTask"
          @keydown.esc="showNewTask = false"
        />
        <div class="flex gap-1.5">
          <button
            class="rounded-lg bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            :disabled="!newTaskTitle.trim() || creating"
            @click="createTask"
          >{{ creating ? 'Creating…' : 'Create' }}</button>
          <button
            class="rounded-lg border border-outline-gray-3 px-2.5 py-1 text-xs text-ink-gray-6 hover:bg-surface-gray-1"
            @click="showNewTask = false"
          >Cancel</button>
        </div>
      </div>
      <button
        v-else
        class="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700"
        @click="openNewTask"
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
        </svg>
        New task
      </button>
    </div>
  </div>

  <!-- Detail view -->
  <div v-else class="flex h-full flex-col">
    <div class="flex shrink-0 items-center gap-2 border-b border-outline-gray-2 px-4 py-2">
      <button
        class="flex items-center gap-1 text-xs text-ink-gray-5 hover:text-ink-gray-8"
        @click="view = 'list'"
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
        Tasks
      </button>
    </div>
    <div class="min-h-0 flex-1 overflow-hidden">
      <KanbanTaskPanel
        :taskId="selectedTaskId"
        :allTags="[]"
        @close="view = 'list'"
        @saved="onTaskSaved"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { call, LoadingIndicator, toast } from "frappe-ui";
import { computed, inject, nextTick, ref, watch } from "vue";
import { TicketSymbol } from "@/types";
import KanbanTaskPanel from "@/pages/tasks/KanbanTaskPanel.vue";

const emit = defineEmits<{ countChange: [count: number] }>();

const ticket = inject(TicketSymbol);
const customer = computed(() => ticket?.value?.doc?.customer ?? "");

type View = "list" | "detail";
const view = ref<View>("list");
const selectedTaskId = ref("");

interface Task {
  name: string;
  title: string;
  status: string;
  priority: string;
  assigned_to: string;
  assigned_to_username: string;
  due_date: string;
  ticket: string;
}

const tasks = ref<Task[]>([]);
const loading = ref(false);

async function loadTasks() {
  if (!customer.value) {
    tasks.value = [];
    emit("countChange", 0);
    return;
  }
  loading.value = true;
  try {
    tasks.value = (await call(
      "helpdesk.helpdesk.doctype.hd_task.hd_task.get_tasks_for_customer",
      { customer: customer.value },
    )) as Task[];
  } catch {
    tasks.value = [];
  } finally {
    loading.value = false;
  }
  emit("countChange", tasks.value.filter((t) => t.status !== "Done").length);
}

watch(customer, loadTasks, { immediate: true });

function openTask(name: string) {
  selectedTaskId.value = name;
  view.value = "detail";
}

function onTaskSaved() {
  loadTasks();
  view.value = "list";
}

function statusDot(status: string): string {
  const map: Record<string, string> = {
    Backlog: "bg-ink-gray-3",
    Todo: "bg-blue-400",
    "In Progress": "bg-orange-400",
    Done: "bg-green-500",
  };
  return map[status] ?? "bg-ink-gray-3";
}

function isOverdue(date: string): boolean {
  return new Date(date) < new Date(new Date().toDateString());
}

// ── New task ──────────────────────────────────────────────────────────────────

const showNewTask = ref(false);
const newTaskTitle = ref("");
const creating = ref(false);
const newTaskInput = ref<HTMLInputElement | null>(null);

function openNewTask() {
  showNewTask.value = true;
  newTaskTitle.value = "";
  nextTick(() => newTaskInput.value?.focus());
}

async function createTask() {
  const ticketName = ticket?.value?.doc?.name;
  if (!newTaskTitle.value.trim() || !ticketName) return;
  creating.value = true;
  try {
    const name = (await call(
      "helpdesk.helpdesk.doctype.hd_task.hd_task.create_task_for_ticket",
      { ticket: ticketName, title: newTaskTitle.value.trim() },
    )) as string;
    showNewTask.value = false;
    newTaskTitle.value = "";
    await loadTasks();
    toast.success("Task created");
    openTask(name);
  } catch {
    toast.error("Failed to create task");
  } finally {
    creating.value = false;
  }
}
</script>
```

- [ ] **Step 2: Commit**

```bash
git add desk/src/components/ticket-agent/TicketTasksTab.vue
git commit -m "feat(tickets): add TicketTasksTab component"
```

---

### Task 5: Frontend — Wire Tasks tab into `TicketSidebar.vue`

**Files:**
- Modify: `desk/src/components/ticket-agent/TicketSidebar.vue`

- [ ] **Step 1: Replace the full file content**

```vue
<template>
  <Resizer class="flex flex-col border-l" side="right">
    <!-- Tab bar — custom render so Tasks tab can carry a count badge -->
    <div class="flex shrink-0 border-b border-outline-gray-2 px-4 pt-3">
      <button
        v-for="tab in tabs"
        :key="tab.value"
        class="flex items-center gap-1.5 border-b-2 px-3 pb-2 text-xs font-medium transition-colors"
        :class="
          currentTab === tab.value
            ? 'border-blue-600 text-ink-gray-9'
            : 'border-transparent text-ink-gray-5 hover:text-ink-gray-8'
        "
        @click="currentTab = tab.value"
      >
        {{ tab.label }}
        <span
          v-if="tab.count"
          class="rounded-full bg-blue-600 px-1.5 py-0.5 text-[9px] font-bold leading-none text-white"
        >{{ tab.count }}</span>
      </button>
    </div>

    <div class="max-h-full flex-1 overflow-hidden">
      <TicketDetailsTab v-if="currentTab === 'details'" />
      <TicketContactTab v-else-if="currentTab === 'contact'" />
      <!-- v-show keeps TicketTasksTab mounted so it can emit its count
           even while Details or Contact is the active tab -->
      <TicketTasksTab
        v-show="currentTab === 'tasks'"
        @countChange="taskCount = $event"
      />
    </div>
  </Resizer>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import Resizer from "../Resizer.vue";
import TicketContactTab from "./TicketContactTab.vue";
import TicketDetailsTab from "./TicketDetailsTab.vue";
import TicketTasksTab from "./TicketTasksTab.vue";

const currentTab = ref("details");
const taskCount = ref(0);

const tabs = computed(() => [
  { label: "Details", value: "details", count: 0 },
  { label: "Contact", value: "contact", count: 0 },
  { label: "Tasks",   value: "tasks",   count: taskCount.value },
]);
</script>
```

- [ ] **Step 2: Build and verify in browser**

```bash
cd /home/kushal/frappe-bench
bench build --app helpdesk 2>&1 | grep -E "error|Error|built in" | tail -5
```

Expected: `✓ built in Xs` — no errors.

Open a ticket in the browser (`http://dev.localhost:8002/helpdesk/tickets/<id>`). Confirm:
- Right sidebar shows "Details | Contact | Tasks" tab bar
- Switching to Tasks shows the list (empty state if no customer)
- If the ticket has an HD Customer with existing tasks, they appear with status dot + username
- `+ New task` creates a task and immediately opens it in the detail panel
- Saving in the detail panel returns to the list and the badge count updates
- The blue badge on the Tasks tab shows open task count (hidden when 0)

- [ ] **Step 3: Commit**

```bash
git add desk/src/components/ticket-agent/TicketSidebar.vue
git commit -m "feat(tickets): add Tasks tab with customer-scoped task list to ticket sidebar"
```

---

### Task 6: Run full test suite and push

- [ ] **Step 1: Run all HD Task tests**

```bash
cd /home/kushal/frappe-bench
bench run-tests --app helpdesk --module helpdesk.helpdesk.doctype.hd_task.test_hd_task 2>&1 | tail -20
```

Expected: all existing tests pass (the Email Domain `before_tests` error is a pre-existing infrastructure issue unrelated to this feature — check that the new tests specifically pass).

- [ ] **Step 2: Push to upstream**

```bash
git push upstream develop
```
