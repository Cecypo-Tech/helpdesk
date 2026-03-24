# Kanban Detail Panel & Drag-and-Drop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a collapsable split-panel task detail view and native HTML5 drag-and-drop status changes to the Kanban board.

**Architecture:** `KanbanTaskPanel.vue` is a new self-contained component that loads a task via `createDocumentResource` and auto-saves each field independently. `KanbanView.vue` is refactored to a split layout (columns + panel), owns the `selectedTaskId` state, and handles all DnD logic internally without emitting events to the parent.

**Tech Stack:** Vue 3 Composition API, TypeScript, frappe-ui (`createDocumentResource`, `createListResource`, `call`, `TextEditor`, `toast`), local `Link` component, native HTML5 DnD API, Tailwind CSS semantic tokens, localStorage for collapse state.

**Spec:** `docs/superpowers/specs/2026-03-24-kanban-detail-panel-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `desk/src/pages/tasks/KanbanTaskPanel.vue` | **Create** | Full detail panel: all fields, auto-save, subtasks |
| `desk/src/pages/tasks/KanbanView.vue` | **Rewrite** | Split layout, DnD, panel state, collapse toggle |
| `desk/src/pages/tasks/Tasks.vue` | **Modify** | Remove `@card-click` handler from `<KanbanView>` |

---

## Task 1: Create `KanbanTaskPanel.vue`

**Files:**
- Create: `desk/src/pages/tasks/KanbanTaskPanel.vue`

- [ ] **Step 1.1 — Create the file with full content**

```vue
<template>
  <div class="flex flex-col h-full bg-surface-white overflow-hidden">

    <!-- Header -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-outline-gray-1 flex-shrink-0">
      <span class="text-xs text-ink-gray-4 transition-opacity duration-300">
        {{ savedIndicator ? __('✓ Saved') : '' }}
      </span>
      <div class="flex items-center gap-2">
        <a
          :href="`/helpdesk/tasks/${taskId}`"
          target="_blank"
          class="text-xs text-ink-gray-5 hover:text-ink-blue-4"
          @click.prevent="router.push({ name: 'TaskAgent', params: { taskId } })"
        >
          {{ __('Open full page →') }}
        </a>
        <button
          class="text-ink-gray-4 hover:text-ink-gray-7"
          @click="$emit('close')"
        >
          <LucideX class="h-4 w-4" />
        </button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="task.loading" class="flex items-center justify-center h-32">
      <LoadingIndicator class="h-5 w-5 text-ink-gray-4" />
    </div>

    <!-- Content -->
    <div v-else-if="task.doc" class="flex flex-col gap-4 p-4 overflow-y-auto flex-1">

      <!-- Title -->
      <input
        v-model="form.title"
        type="text"
        class="w-full text-base font-semibold text-ink-gray-9 bg-transparent border-0 outline-none focus:ring-1 focus:ring-outline-gray-3 rounded px-1 -mx-1"
        :placeholder="__('Task title')"
        @blur="saveField('title', form.title)"
      />

      <!-- Status + Priority -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Status') }}</label>
          <select
            v-model="form.status"
            class="text-sm rounded border border-outline-gray-2 bg-surface-white px-2 py-1.5 text-ink-gray-8 focus:outline-none focus:border-outline-gray-4"
            @change="saveField('status', form.status)"
          >
            <option v-for="s in statusOptions" :key="s" :value="s">{{ s }}</option>
          </select>
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Priority') }}</label>
          <Link
            v-model="form.priority"
            doctype="HD Ticket Priority"
            :placeholder="__('—')"
            class="form-control"
            @change="saveField('priority', form.priority)"
          />
        </div>
      </div>

      <!-- Assigned To + Due Date -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Assigned To') }}</label>
          <Link
            v-model="form.assigned_to"
            doctype="HD Agent"
            :placeholder="__('—')"
            class="form-control"
            @change="saveField('assigned_to', form.assigned_to)"
          />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Due Date') }}</label>
          <input
            v-model="form.due_date"
            type="date"
            class="text-sm rounded border border-outline-gray-2 bg-surface-white px-2 py-1.5 focus:outline-none focus:border-outline-gray-4"
            :class="isOverdue(form.due_date) ? 'text-red-500' : 'text-ink-gray-8'"
            @blur="saveField('due_date', form.due_date)"
          />
        </div>
      </div>

      <!-- Ticket + Team -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Ticket') }}</label>
          <Link
            v-model="form.ticket"
            doctype="HD Ticket"
            :placeholder="__('—')"
            class="form-control"
            @change="saveField('ticket', form.ticket)"
          />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Team') }}</label>
          <Link
            v-model="form.team"
            doctype="HD Team"
            :placeholder="__('—')"
            class="form-control"
            @change="saveField('team', form.team)"
          />
        </div>
      </div>

      <!-- Description -->
      <div class="flex flex-col gap-1">
        <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Description') }}</label>
        <TextEditor
          v-model:content="form.description"
          :editable="true"
          editor-class="min-h-[5rem] prose-f p-2 rounded border border-outline-gray-2 focus-within:border-outline-gray-4 text-sm"
          :placeholder="__('Add a description...')"
          @change="debouncedSaveDescription"
        />
      </div>

      <!-- Subtasks -->
      <div class="flex flex-col gap-2">
        <div class="flex items-center justify-between">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">
            {{ __('Subtasks') }}
          </label>
          <span class="text-xs text-ink-gray-4">{{ doneCount }} / {{ form.subtasks.length }} {{ __('done') }}</span>
        </div>

        <!-- Progress bar -->
        <div v-if="form.subtasks.length" class="h-1 w-full rounded-full bg-surface-gray-2 overflow-hidden">
          <div
            class="h-full rounded-full bg-green-500 transition-all duration-300"
            :style="{ width: progressPct + '%' }"
          />
        </div>

        <!-- Subtask rows -->
        <div
          v-for="(sub, idx) in form.subtasks"
          :key="sub.name || idx"
          class="flex items-center gap-2 rounded p-1 hover:bg-surface-gray-1 group"
        >
          <input
            type="checkbox"
            class="h-3.5 w-3.5 cursor-pointer accent-green-500 flex-shrink-0"
            :checked="sub.status === 'Done'"
            @change="toggleSubtask(idx)"
          />
          <input
            v-model="sub.title"
            type="text"
            class="flex-1 bg-transparent text-sm text-ink-gray-8 outline-none placeholder:text-ink-gray-4"
            :class="sub.status === 'Done' ? 'line-through text-ink-gray-4' : ''"
            :placeholder="__('Subtask title')"
            @blur="saveSubtasks"
          />
          <button
            class="invisible group-hover:visible text-ink-gray-4 hover:text-red-400"
            @click="removeSubtask(idx)"
          >
            <LucideX class="h-3 w-3" />
          </button>
        </div>

        <button
          class="flex items-center gap-1 text-xs text-ink-gray-5 hover:text-ink-gray-8 w-fit"
          @click="addSubtask"
        >
          <LucidePlus class="h-3.5 w-3.5" />
          {{ __('Add subtask') }}
        </button>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import Link from "@/components/frappe-ui/Link.vue";
import { __ } from "@/translation";
import {
  call,
  createDocumentResource,
  LoadingIndicator,
  TextEditor,
  toast,
} from "frappe-ui";
import LucidePlus from "~icons/lucide/plus";
import LucideX from "~icons/lucide/x";
import { computed, reactive, ref } from "vue";
import { useRouter } from "vue-router";

const props = defineProps<{ taskId: string }>();
const emit = defineEmits<{ close: []; saved: [] }>();
const router = useRouter();

const savedIndicator = ref(false);
let savedTimer: ReturnType<typeof setTimeout> | null = null;
let descTimer: ReturnType<typeof setTimeout> | null = null;

const statusOptions = ["Backlog", "Todo", "In Progress", "Done"];

interface Subtask {
  name?: string;
  title: string;
  status: string;
  due_date: string;
}

const form = reactive({
  title: "",
  status: "Backlog",
  priority: "",
  assigned_to: "",
  due_date: "",
  ticket: "",
  team: "",
  description: "",
  subtasks: [] as Subtask[],
});

const task = createDocumentResource({
  doctype: "HD Task",
  name: props.taskId,
  auto: true,
  onSuccess(doc: any) {
    form.title = doc.title ?? "";
    form.status = doc.status ?? "Backlog";
    form.priority = doc.priority ?? "";
    form.assigned_to = doc.assigned_to ?? "";
    form.due_date = doc.due_date ?? "";
    form.ticket = doc.ticket ?? "";
    form.team = doc.team ?? "";
    form.description = doc.description ?? "";
    form.subtasks = (doc.subtasks ?? []).map((s: any) => ({
      name: s.name,
      title: s.title ?? "",
      status: s.status ?? "Backlog",
      due_date: s.due_date ?? "",
    }));
  },
  onError() {
    toast.error(__("Task not found"));
    emit("close");
  },
});

const doneCount = computed(() => form.subtasks.filter((s) => s.status === "Done").length);
const progressPct = computed(() =>
  form.subtasks.length ? Math.round((doneCount.value / form.subtasks.length) * 100) : 0
);

function flashSaved() {
  savedIndicator.value = true;
  if (savedTimer) clearTimeout(savedTimer);
  savedTimer = setTimeout(() => { savedIndicator.value = false; }, 1500);
}

async function saveField(fieldname: string, value: any) {
  try {
    await call("frappe.client.set_value", {
      doctype: "HD Task",
      name: props.taskId,
      fieldname,
      value: value || null,
    });
    flashSaved();
    emit("saved");
  } catch {
    toast.error(__("Failed to save"));
  }
}

function debouncedSaveDescription() {
  if (descTimer) clearTimeout(descTimer);
  descTimer = setTimeout(() => saveField("description", form.description), 800);
}

async function saveSubtasks() {
  try {
    await call("frappe.client.save", {
      doc: {
        doctype: "HD Task",
        name: props.taskId,
        subtasks: form.subtasks.map((s) => ({
          doctype: "HD Task Subtask",
          name: s.name || null,
          title: s.title,
          status: s.status,
          due_date: s.due_date || null,
        })),
      },
    });
    // Reload to get server-assigned names for new subtasks
    task.reload();
    flashSaved();
    emit("saved");
  } catch {
    toast.error(__("Failed to save subtasks"));
  }
}

function addSubtask() {
  form.subtasks.push({ title: "", status: "Backlog", due_date: "" });
}

function removeSubtask(idx: number) {
  form.subtasks.splice(idx, 1);
  saveSubtasks();
}

function toggleSubtask(idx: number) {
  const sub = form.subtasks[idx];
  sub.status = sub.status === "Done" ? "Todo" : "Done";
  saveSubtasks();
}

function isOverdue(d: string) {
  if (!d) return false;
  return new Date(d) < new Date();
}
</script>
```

- [ ] **Step 1.2 — Build to confirm no TypeScript/import errors**

```bash
cd /home/frappeuser/bench16 && bench build --app helpdesk 2>&1 | grep -E "(error|Error|✓ built|Done)"
```

Expected: `✓ built` and `Done` with no `error` lines.

---

## Task 2: Rewrite `KanbanView.vue`

**Files:**
- Modify: `desk/src/pages/tasks/KanbanView.vue` (full rewrite)

- [ ] **Step 2.1 — Replace file with full rewrite**

```vue
<template>
  <div class="flex h-full overflow-hidden">

    <!-- ── Kanban columns ── -->
    <div class="flex-1 flex overflow-x-auto gap-3 p-4">
      <div
        v-for="col in columns"
        :key="col.status"
        class="flex flex-col w-72 flex-shrink-0 rounded-lg bg-surface-gray-1 transition-all"
        :class="hoveredColumn === col.status ? 'ring-2 ring-ink-blue-3 ring-offset-1' : ''"
        @dragover.prevent="onDragOver(col.status)"
        @dragleave="onDragLeave"
        @drop.prevent="onDrop(col.status)"
      >
        <!-- Column header -->
        <div class="flex items-center justify-between px-3 py-2.5 border-b border-outline-gray-1">
          <div class="flex items-center gap-2">
            <span class="h-2.5 w-2.5 rounded-full flex-shrink-0" :class="col.dotClass" />
            <span class="text-sm font-semibold text-ink-gray-8">{{ col.status }}</span>
            <span class="text-xs text-ink-gray-4 font-normal">
              {{ getCardsForStatus(col.status).length }}
            </span>
          </div>
          <button
            class="flex items-center justify-center h-5 w-5 rounded text-ink-gray-4 hover:text-ink-gray-8 hover:bg-surface-gray-2 transition-colors"
            :title="__('Add task')"
            @click="createTask(col.status)"
          >
            <LucidePlus class="h-3.5 w-3.5" />
          </button>
        </div>

        <!-- Cards -->
        <div class="flex flex-col gap-2 p-2 overflow-y-auto flex-1">
          <div
            v-for="card in getCardsForStatus(col.status)"
            :key="card.name"
            draggable="true"
            class="bg-surface-white rounded-md border border-outline-gray-1 p-3 cursor-pointer hover:border-outline-gray-3 hover:shadow-sm transition-all select-none"
            :class="[
              card.name === selectedTaskId ? 'ring-2 ring-ink-blue-3' : '',
              draggedCard?.name === card.name ? 'opacity-40' : '',
            ]"
            @click="selectedTaskId = card.name"
            @dragstart="onDragStart(card)"
            @dragend="onDragEnd"
          >
            <p class="text-sm font-medium text-ink-gray-9 leading-snug mb-2">{{ card.title }}</p>
            <div class="flex flex-wrap items-center gap-1.5">
              <span
                v-if="card.priority"
                class="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium"
                :class="priorityClass(card.priority)"
              >{{ card.priority }}</span>
              <span
                v-if="card.due_date"
                class="inline-flex items-center gap-1 text-xs"
                :class="isOverdue(card.due_date) ? 'text-red-500' : 'text-ink-gray-5'"
              >
                <LucideCalendar class="h-3 w-3" />
                {{ formatDate(card.due_date) }}
              </span>
              <span
                v-if="card.assigned_to"
                class="inline-flex items-center gap-1 text-xs text-ink-gray-5 ml-auto"
              >
                <LucideUser class="h-3 w-3" />
                {{ card.assigned_to }}
              </span>
            </div>
            <div v-if="card.ticket" class="mt-1.5 text-xs text-ink-gray-4">#{{ card.ticket }}</div>
          </div>

          <!-- Empty state -->
          <div
            v-if="getCardsForStatus(col.status).length === 0 && !tasks.list?.loading"
            class="flex flex-col items-center justify-center py-8 text-ink-gray-3"
          >
            <LucideSquareDashed class="h-8 w-8 mb-2 opacity-40" />
            <p class="text-xs">{{ __("No tasks") }}</p>
          </div>

          <!-- Loading skeleton -->
          <div v-if="tasks.list?.loading" class="flex flex-col gap-2">
            <div v-for="i in 3" :key="i" class="h-16 rounded-md bg-surface-gray-2 animate-pulse" />
          </div>
        </div>
      </div>
    </div>

    <!-- ── Collapse toggle button ── -->
    <button
      class="flex-shrink-0 w-6 flex items-center justify-center border-l border-outline-gray-1 text-ink-gray-4 hover:text-ink-gray-7 hover:bg-surface-gray-1 transition-colors"
      :title="panelCollapsed ? __('Expand panel') : __('Collapse panel')"
      @click="toggleCollapse"
    >
      <component :is="panelCollapsed ? LucideChevronLeft : LucideChevronRight" class="h-4 w-4" />
    </button>

    <!-- ── Detail panel ── -->
    <div
      v-show="!panelCollapsed"
      class="flex-shrink-0 w-96 border-l border-outline-gray-1 flex flex-col overflow-hidden"
    >
      <!-- No task selected -->
      <div
        v-if="!selectedTaskId"
        class="flex flex-col items-center justify-center h-full text-ink-gray-3 gap-2"
      >
        <LucideSquareDashed class="h-10 w-10 opacity-40" />
        <p class="text-sm">{{ __("Select a task to view details") }}</p>
      </div>

      <!-- Panel component — :key forces recreation when task changes -->
      <KanbanTaskPanel
        v-else
        :key="selectedTaskId"
        :task-id="selectedTaskId"
        @close="selectedTaskId = null"
        @saved="tasks.reload()"
      />
    </div>

  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { call, createListResource, toast } from "frappe-ui";
import LucideCalendar from "~icons/lucide/calendar";
import LucideChevronLeft from "~icons/lucide/chevron-left";
import LucideChevronRight from "~icons/lucide/chevron-right";
import LucidePlus from "~icons/lucide/plus";
import LucideSquareDashed from "~icons/lucide/square-dashed";
import LucideUser from "~icons/lucide/user";
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import KanbanTaskPanel from "./KanbanTaskPanel.vue";

const router = useRouter();

// ── Panel state ──────────────────────────────────────────────
const COLLAPSE_KEY = "hd_task_kanban_panel_collapsed";
const selectedTaskId = ref<string | null>(null);
const panelCollapsed = ref(false);

onMounted(() => {
  // Collapse on small screens
  if (window.innerWidth < 640) {
    panelCollapsed.value = true;
  } else {
    panelCollapsed.value = localStorage.getItem(COLLAPSE_KEY) === "true";
  }
});

function toggleCollapse() {
  panelCollapsed.value = !panelCollapsed.value;
  localStorage.setItem(COLLAPSE_KEY, String(panelCollapsed.value));
}

// ── Columns ──────────────────────────────────────────────────
const columns = [
  { status: "Backlog", dotClass: "bg-gray-400" },
  { status: "Todo", dotClass: "bg-blue-400" },
  { status: "In Progress", dotClass: "bg-orange-400" },
  { status: "Done", dotClass: "bg-green-400" },
];

// ── Task list ────────────────────────────────────────────────
const tasks = createListResource({
  doctype: "HD Task",
  fields: ["name", "title", "status", "priority", "due_date", "assigned_to", "ticket"],
  filters: [],
  orderBy: "modified desc",
  pageLength: 999,
  auto: true,
});

function getCardsForStatus(status: string) {
  return (tasks.data ?? []).filter((t: any) => t.status === status);
}

// ── Drag and drop ────────────────────────────────────────────
interface DraggedCard { name: string; status: string }
const draggedCard = ref<DraggedCard | null>(null);
const hoveredColumn = ref<string | null>(null);

function onDragStart(card: any) {
  draggedCard.value = { name: card.name, status: card.status };
}

function onDragEnd() {
  draggedCard.value = null;
  hoveredColumn.value = null;
}

function onDragOver(status: string) {
  hoveredColumn.value = status;
}

function onDragLeave() {
  hoveredColumn.value = null;
}

async function onDrop(targetStatus: string) {
  hoveredColumn.value = null;
  const card = draggedCard.value;
  draggedCard.value = null;

  if (!card || card.status === targetStatus) return;

  // Optimistic update
  const live = (tasks.data ?? []).find((t: any) => t.name === card.name);
  if (live) live.status = targetStatus;

  try {
    await call("frappe.client.set_value", {
      doctype: "HD Task",
      name: card.name,
      fieldname: "status",
      value: targetStatus,
    });
    tasks.reload();
  } catch {
    // Rollback
    if (live) live.status = card.status;
    toast.error(__("Failed to move task"));
  }
}

// ── Helpers ──────────────────────────────────────────────────
function createTask(status: string) {
  router.push({ name: "TaskAgentNew", query: { status } });
}

function priorityClass(priority: string) {
  const map: Record<string, string> = {
    Urgent: "bg-red-100 text-red-700",
    High: "bg-orange-100 text-orange-700",
    Medium: "bg-yellow-100 text-yellow-700",
    Low: "bg-green-100 text-green-700",
  };
  return map[priority] ?? "bg-gray-100 text-gray-600";
}

function formatDate(d: string) {
  if (!d) return "";
  return new Date(d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function isOverdue(d: string) {
  if (!d) return false;
  return new Date(d) < new Date();
}
</script>
```

- [ ] **Step 2.2 — Build to confirm no errors**

```bash
cd /home/frappeuser/bench16 && bench build --app helpdesk 2>&1 | grep -E "(error|Error|✓ built|Done)"
```

Expected: `✓ built` and `Done` with no `error` lines.

---

## Task 3: Update `Tasks.vue` — remove `@card-click`

**Files:**
- Modify: `desk/src/pages/tasks/Tasks.vue`

- [ ] **Step 3.1 — Remove the `@card-click` handler from the `<KanbanView>` element**

In `desk/src/pages/tasks/Tasks.vue`, the `<KanbanView>` block currently reads:

```html
    <KanbanView
      v-if="isKanbanView"
      @card-click="(name) => $router.push({ name: 'TaskAgent', params: { taskId: name } })"
    />
```

Change it to:

```html
    <KanbanView
      v-if="isKanbanView"
    />
```

- [ ] **Step 3.2 — Build to confirm no errors**

```bash
cd /home/frappeuser/bench16 && bench build --app helpdesk 2>&1 | grep -E "(error|Error|✓ built|Done)"
```

Expected: `✓ built` and `Done` with no `error` lines.

- [ ] **Step 3.3 — Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/pages/tasks/KanbanTaskPanel.vue \
        desk/src/pages/tasks/KanbanView.vue \
        desk/src/pages/tasks/Tasks.vue
git commit -m "feat: kanban detail panel, collapsable split layout, drag-and-drop status"
```

---

## Task 4: Manual Verification

Hard-refresh the browser after the build completes.

- [ ] **4.1 — Panel opens on card click**
  - Navigate to Tasks → select Kanban view
  - Click any task card
  - Expected: right panel opens with task fields populated

- [ ] **4.2 — Panel collapse/expand**
  - Click the `‹` toggle between the columns and panel
  - Expected: panel collapses to just the toggle button; click again to expand
  - Reload the page — expected: collapse state persists

- [ ] **4.3 — Auto-save (scalar field)**
  - Open a card in the panel, change the Status dropdown
  - Expected: `✓ Saved` flashes in the panel header; card moves to the new column immediately after `tasks.reload()` completes

- [ ] **4.4 — Auto-save (title)**
  - Edit the title field, click somewhere else (blur)
  - Expected: `✓ Saved` flashes; full-page reload of the task confirms the new title

- [ ] **4.5 — Subtasks**
  - Click `+ Add subtask`, type a title, blur
  - Expected: saves; check the checkbox → status changes to Done, progress bar updates

- [ ] **4.6 — Drag and drop**
  - Drag a card from one column (e.g. Backlog) and drop it on another (e.g. In Progress)
  - Expected: card moves immediately (optimistic), stays in the new column after reload

- [ ] **4.7 — DnD same column (no-op)**
  - Drag a card and drop it on its own column
  - Expected: no API call, no movement, no error

- [ ] **4.8 — Switch tasks in panel**
  - Open task A, then click task B while panel is open
  - Expected: panel reloads with task B's data; task A's fields are gone

- [ ] **4.9 — Close panel**
  - Click the ✕ in the panel header
  - Expected: panel goes to empty state ("Select a task to view details"), no selected card highlight

- [ ] **4.10 — Open full page link**
  - Click "Open full page →" in the panel
  - Expected: navigates to `TaskDetail.vue` for that task
