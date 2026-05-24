# Calendar View — Drag & Drop + Rich Chips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the task CalendarView with richer task chips (priority bars + assignee avatar) and native HTML5 drag-and-drop to reschedule tasks by dragging between day cells.

**Architecture:** All changes are confined to `CalendarView.vue`. Task 1 adds helper functions and constants to the script. Task 2 replaces the chip template with a 2-row mini-card. Task 3 adds drag state, handlers, and wires the chip and cell templates for D&D.

**Tech Stack:** Vue 3 Composition API, frappe-ui (`call`, `createListResource`, `toast`), native HTML5 Drag & Drop API, Tailwind CSS (semantic frappe-ui tokens).

---

## File Map

| File | Action |
|------|--------|
| `desk/src/pages/tasks/CalendarView.vue` | Modify — all changes live here |

No new files. No backend changes. No new npm dependencies.

---

### Task 1: Add helper functions, constants, and toast import

**Files:**
- Modify: `desk/src/pages/tasks/CalendarView.vue`

These utilities are identical to `KanbanView.vue` — copied, not abstracted (a shared util is out of scope).

- [ ] **Step 1: Add `toast` to the frappe-ui import**

Find this line near the top of `<script setup>`:

```ts
import { call, createListResource, dayjs } from "frappe-ui";
```

Replace it with:

```ts
import { call, createListResource, dayjs, toast } from "frappe-ui";
```

- [ ] **Step 2: Replace `STATUS_CHIP` / `statusChipClass` with `STATUS_BORDER` / `statusBorderClass`**

Find and delete these lines in the script:

```ts
const STATUS_CHIP: Record<string, string> = {
  "Backlog":     "bg-gray-100 text-gray-600",
  "Todo":        "bg-blue-100 text-blue-700",
  "In Progress": "bg-orange-100 text-orange-700",
  "Done":        "bg-green-100 text-green-700",
};
function statusChipClass(status: string) {
  return STATUS_CHIP[status] ?? "bg-surface-gray-2 text-ink-gray-6";
}
```

Replace with:

```ts
const STATUS_BORDER: Record<string, string> = {
  "Backlog":     "border-l-gray-400",
  "Todo":        "border-l-blue-400",
  "In Progress": "border-l-orange-400",
  "Done":        "border-l-green-400",
};
function statusBorderClass(status: string): string {
  return STATUS_BORDER[status] ?? "border-l-gray-300";
}
```

- [ ] **Step 3: Drop MAX_VISIBLE from 3 to 2**

Find:

```ts
const MAX_VISIBLE = 3;
```

Replace with:

```ts
const MAX_VISIBLE = 2;
```

- [ ] **Step 4: Add avatar, priority, and hash helpers at the end of the script (before the closing `</script>`)**

```ts
// ── Avatar ───────────────────────────────────────────────────
const AVATAR_COLORS = [
  { bg: "bg-blue-100", text: "text-blue-700" },
  { bg: "bg-green-100", text: "text-green-700" },
  { bg: "bg-purple-100", text: "text-purple-700" },
  { bg: "bg-orange-100", text: "text-orange-700" },
  { bg: "bg-pink-100", text: "text-pink-700" },
  { bg: "bg-teal-100", text: "text-teal-700" },
  { bg: "bg-indigo-100", text: "text-indigo-700" },
  { bg: "bg-red-100", text: "text-red-700" },
];

function hashStr(s: string): number {
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) & 0xffff;
  return h;
}

function avatarInitials(name: string): string {
  if (!name) return "?";
  const parts = name.split(/[@.\s]/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

function avatarColor(name: string): { bg: string; text: string } {
  return AVATAR_COLORS[hashStr(name) % AVATAR_COLORS.length];
}

// ── Priority bars ────────────────────────────────────────────
const PRIORITY_HEIGHTS: Record<string, [string, string, string]> = {
  Low:    ["h-[4px]", "h-[4px]", "h-[4px]"],
  Medium: ["h-[4px]", "h-[8px]", "h-[8px]"],
  High:   ["h-[4px]", "h-[8px]", "h-[12px]"],
  Urgent: ["h-[4px]", "h-[8px]", "h-[12px]"],
};

function priorityBarH(priority: string, idx: number): string {
  return (PRIORITY_HEIGHTS[priority] ?? PRIORITY_HEIGHTS["Low"])[idx];
}

function priorityBarColor(priority: string): string {
  const map: Record<string, string> = {
    Urgent: "bg-red-500",
    High:   "bg-orange-500",
    Medium: "bg-amber-400",
    Low:    "bg-green-400",
  };
  return map[priority] ?? "bg-gray-300";
}
```

- [ ] **Step 5: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/pages/tasks/CalendarView.vue
git commit -m "refactor: calendar chip helpers + toast import prep"
```

---

### Task 2: Replace single-line chip with rich 2-row mini-card

**Files:**
- Modify: `desk/src/pages/tasks/CalendarView.vue` (template only)

- [ ] **Step 1: Replace the task chips block in the template**

Find this entire block inside the `<!-- Task chips -->` div:

```html
<!-- Task chips -->
<div class="flex flex-col gap-0.5 px-1 pb-1 overflow-y-auto flex-1">
  <button
    v-for="task in visibleTasksForDate(cell.dateStr)"
    :key="task.name"
    class="w-full text-left px-1.5 py-0.5 rounded text-[11px] font-medium truncate leading-4 transition-colors"
    :class="[
      selectedTaskId === task.name
        ? 'bg-ink-blue-3 text-white'
        : statusChipClass(task.status),
    ]"
    @click="selectedTaskId = task.name"
  >
    {{ task.title }}
  </button>
  <span
    v-if="overflowCount(cell.dateStr) > 0"
    class="text-[10px] text-ink-gray-4 px-1"
  >
    +{{ overflowCount(cell.dateStr) }} {{ __('more') }}
  </span>
</div>
```

Replace with:

```html
<!-- Task chips -->
<div class="flex flex-col gap-1 px-1 pb-1 overflow-y-auto flex-1">
  <div
    v-for="task in visibleTasksForDate(cell.dateStr)"
    :key="task.name"
    class="w-full rounded border border-outline-gray-1 border-l-2 bg-surface-white px-1.5 py-1 cursor-pointer select-none transition-all hover:shadow-sm"
    :class="[
      statusBorderClass(task.status),
      selectedTaskId === task.name ? 'ring-2 ring-ink-blue-3' : '',
    ]"
    @click="selectedTaskId = task.name"
  >
    <!-- Row 1: title -->
    <p class="text-[11px] font-medium text-ink-gray-8 truncate leading-4">{{ task.title }}</p>
    <!-- Row 2: priority bars + assignee avatar -->
    <div class="flex items-center mt-0.5">
      <span
        v-if="task.priority"
        :title="task.priority"
        class="flex items-end gap-[2px] flex-shrink-0"
      >
        <span class="w-[3px] rounded-sm" :class="[priorityBarH(task.priority, 0), priorityBarColor(task.priority)]" />
        <span class="w-[3px] rounded-sm" :class="[priorityBarH(task.priority, 1), priorityBarColor(task.priority)]" />
        <span class="w-[3px] rounded-sm" :class="[priorityBarH(task.priority, 2), priorityBarColor(task.priority)]" />
      </span>
      <span class="flex-1" />
      <span
        v-if="task.assigned_to"
        :title="task.assigned_to"
        class="h-4 w-4 rounded-full flex items-center justify-center text-[9px] font-semibold flex-shrink-0"
        :class="[avatarColor(task.assigned_to).bg, avatarColor(task.assigned_to).text]"
      >{{ avatarInitials(task.assigned_to) }}</span>
    </div>
  </div>
  <span
    v-if="overflowCount(cell.dateStr) > 0"
    class="text-[10px] text-ink-gray-4 px-1"
  >
    +{{ overflowCount(cell.dateStr) }} {{ __('more') }}
  </span>
</div>
```

- [ ] **Step 2: Build and verify chips render correctly**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | tail -5
```

Expected: build completes with no errors. Then open `http://localhost:8002/helpdesk/tasks?view=calendar` in the browser and confirm:
- Each task chip shows title + priority bars + avatar initial
- Left border is coloured by status (grey = Backlog, blue = Todo, orange = In Progress, green = Done)
- Clicking a chip opens the side panel
- At most 2 chips visible per cell with `+N more` overflow label

- [ ] **Step 3: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/pages/tasks/CalendarView.vue
git commit -m "feat: rich 2-row task chips on calendar view"
```

---

### Task 3: Add drag & drop — state, handlers, and template wiring

**Files:**
- Modify: `desk/src/pages/tasks/CalendarView.vue` (script + template)

- [ ] **Step 1: Add drag state refs to the script**

After the `// ── Panel state ──` block (after `panelCollapsed` is declared), add:

```ts
// ── Drag & drop state ────────────────────────────────────────
interface DragState { name: string; fromDate: string }
const draggedTask = ref<DragState | null>(null);
const dragOverDate = ref<string | null>(null);
```

- [ ] **Step 2: Add drag & drop handler functions to the script**

Add these functions after `toggleCollapse()`:

```ts
// ── Drag & drop handlers ──────────────────────────────────────
function onDragStart(event: DragEvent, task: any, fromDate: string) {
  draggedTask.value = { name: task.name, fromDate };
  event.dataTransfer!.effectAllowed = "move";
  event.dataTransfer!.setData("text/plain", JSON.stringify({ name: task.name, fromDate }));
}

function onDragEnd() {
  draggedTask.value = null;
  dragOverDate.value = null;
}

function onDragOver(dateStr: string) {
  dragOverDate.value = dateStr;
}

function onDragLeave() {
  dragOverDate.value = null;
}

async function onDrop(toDate: string) {
  const dragged = draggedTask.value;
  draggedTask.value = null;
  dragOverDate.value = null;
  if (!dragged || dragged.fromDate === toDate) return;

  // Optimistic update
  const live = (tasks.data ?? []).find((t: any) => t.name === dragged.name);
  if (live) live.due_date = toDate;
  selectedTaskId.value = dragged.name;

  try {
    await call(
      "helpdesk.helpdesk.doctype.hd_task.hd_task.set_task_field",
      { task_name: dragged.name, fieldname: "due_date", value: toDate }
    );
    tasks.reload();
  } catch {
    tasks.reload();
    toast.error(__("Failed to reschedule task"));
  }
}
```

- [ ] **Step 3: Wire drag events onto the chip — update chip div opening tag**

Find the chip `<div>` opening tag added in Task 2:

```html
    class="w-full rounded border border-outline-gray-1 border-l-2 bg-surface-white px-1.5 py-1 cursor-pointer select-none transition-all hover:shadow-sm"
    :class="[
      statusBorderClass(task.status),
      selectedTaskId === task.name ? 'ring-2 ring-ink-blue-3' : '',
    ]"
    @click="selectedTaskId = task.name"
```

Replace with:

```html
    draggable="true"
    class="w-full rounded border border-outline-gray-1 border-l-2 bg-surface-white px-1.5 py-1 cursor-grab select-none transition-all hover:shadow-sm"
    :class="[
      statusBorderClass(task.status),
      selectedTaskId === task.name ? 'ring-2 ring-ink-blue-3' : '',
      draggedTask?.name === task.name ? 'opacity-40' : '',
    ]"
    @click="selectedTaskId = task.name"
    @dragstart="onDragStart($event, task, cell.dateStr)"
    @dragend="onDragEnd"
```

- [ ] **Step 4: Wire drag events onto the day cell — update cell div opening tag**

Find the day cell `<div>` opening tag (it has `min-h-24 rounded-lg border flex flex-col`):

```html
            class="min-h-24 rounded-lg border flex flex-col overflow-hidden"
            :class="[
              cell.isCurrentMonth
                ? 'bg-surface-white border-outline-gray-2'
                : 'bg-surface-gray-1 border-outline-gray-1',
              cell.isToday ? 'ring-2 ring-ink-blue-3' : '',
            ]"
```

Replace with:

```html
            class="min-h-24 rounded-lg border flex flex-col overflow-hidden transition-all"
            :class="[
              dragOverDate === cell.dateStr
                ? 'bg-surface-blue-1 border-outline-gray-2'
                : cell.isCurrentMonth
                  ? 'bg-surface-white border-outline-gray-2'
                  : 'bg-surface-gray-1 border-outline-gray-1',
              cell.isToday && dragOverDate !== cell.dateStr ? 'ring-2 ring-ink-blue-3' : '',
              dragOverDate === cell.dateStr ? 'ring-2 ring-ink-blue-3' : '',
            ]"
            @dragover.prevent="onDragOver(cell.dateStr)"
            @dragleave="onDragLeave"
            @drop.prevent="onDrop(cell.dateStr)"
```

- [ ] **Step 5: Build**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | tail -5
```

Expected: build completes with no TypeScript errors.

- [ ] **Step 6: Manual verification**

Open `http://localhost:8002/helpdesk/tasks?view=calendar` and test:

1. **Basic drag:** Grab a task chip, drag it to a different day cell — it should turn `opacity-40` while dragging
2. **Drop zone highlight:** The hovered cell gets a blue ring + light blue background
3. **Successful drop:** Task moves to the new date (chip disappears from old cell, appears on new date). Side panel opens showing the moved task with the updated due date
4. **Same-date drop:** Drag a chip onto its own cell — nothing happens, no error
5. **Out-of-month cell:** Drag to a greyed-out edge cell (e.g. last few days of previous month visible at top-left) — drop is accepted, task moves to that date
6. **Error handling:** If you lose network and drop, a toast error appears and tasks reload to server state

- [ ] **Step 7: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/pages/tasks/CalendarView.vue
git commit -m "feat: drag and drop task rescheduling on calendar view"
```
