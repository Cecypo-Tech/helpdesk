<template>
  <div class="flex flex-col h-full overflow-hidden">

    <!-- ── Filter bar ── -->
    <div class="flex items-center gap-2 px-4 py-2 border-b border-outline-gray-1 bg-surface-white flex-shrink-0">

      <!-- Month navigation -->
      <button
        class="flex items-center justify-center h-7 w-7 rounded hover:bg-surface-gray-2 text-ink-gray-6"
        @click="prevMonth"
      >
        <LucideChevronLeft class="h-4 w-4" />
      </button>
      <span class="text-sm font-semibold text-ink-gray-8 w-32 text-center select-none">
        {{ monthLabel }}
      </span>
      <button
        class="flex items-center justify-center h-7 w-7 rounded hover:bg-surface-gray-2 text-ink-gray-6"
        @click="nextMonth"
      >
        <LucideChevronRight class="h-4 w-4" />
      </button>

      <div class="w-px h-5 bg-outline-gray-2 mx-1" />

      <!-- Search -->
      <div class="relative flex items-center">
        <LucideSearch class="absolute left-2 h-3.5 w-3.5 text-ink-gray-4 pointer-events-none" />
        <input
          v-model="filterSearch"
          type="text"
          class="pl-7 pr-6 py-1.5 text-sm rounded border border-outline-gray-2 bg-surface-white text-ink-gray-8 placeholder:text-ink-gray-4 focus:outline-none focus:border-outline-gray-4 w-44"
          :placeholder="__('Search tasks…')"
          @input="onSearchInput"
        />
        <button
          v-if="filterSearch"
          class="absolute right-1.5 text-ink-gray-4 hover:text-ink-gray-7"
          @click="filterSearch = ''; searchResultNames = null"
        >
          <LucideX class="h-3 w-3" />
        </button>
        <LucideLoader v-if="searchLoading" class="absolute right-1.5 h-3 w-3 text-ink-gray-4 animate-spin" />
      </div>

      <!-- Assignee filter -->
      <div class="relative flex items-center">
        <LucideUser class="absolute left-2 h-3.5 w-3.5 text-ink-gray-4 pointer-events-none z-10" />
        <select
          v-model="filterAssignee"
          class="pl-7 pr-6 py-1.5 text-sm rounded border border-outline-gray-2 bg-surface-white text-ink-gray-8 focus:outline-none focus:border-outline-gray-4 appearance-none w-40"
        >
          <option value="">{{ __('All agents') }}</option>
          <option v-for="agent in uniqueAssignees" :key="agent" :value="agent">{{ agent }}</option>
        </select>
        <button
          v-if="filterAssignee"
          class="absolute right-1.5 text-ink-gray-4 hover:text-ink-gray-7 z-10"
          @click="filterAssignee = ''"
        >
          <LucideX class="h-3 w-3" />
        </button>
      </div>

      <!-- Clear all -->
      <button
        v-if="hasFilters"
        class="text-xs text-ink-gray-4 hover:text-ink-gray-7 ml-1"
        @click="clearFilters"
      >
        {{ __('Clear all') }}
      </button>
    </div>

    <!-- ── Main area: grid + panel ── -->
    <div class="flex flex-1 overflow-hidden">

      <!-- ── Calendar grid ── -->
      <div class="flex-1 flex flex-col overflow-auto p-4">

        <!-- Day-of-week header -->
        <div class="grid grid-cols-7 mb-1">
          <div
            v-for="day in weekdays"
            :key="day"
            class="text-center text-xs font-semibold text-ink-gray-4 uppercase tracking-wide py-1"
          >{{ day }}</div>
        </div>

        <!-- Week rows -->
        <div class="flex-1 grid grid-rows-6 gap-1">
          <div
            v-for="(week, wi) in calendarWeeks"
            :key="wi"
            class="grid grid-cols-7 gap-1"
          >
            <div
              v-for="cell in week"
              :key="cell.dateStr"
              class="min-h-24 rounded-lg border flex flex-col overflow-hidden"
              :class="[
                cell.isCurrentMonth
                  ? 'bg-surface-white border-outline-gray-2'
                  : 'bg-surface-gray-1 border-outline-gray-1',
                cell.isToday ? 'ring-2 ring-ink-blue-3' : '',
              ]"
            >
              <!-- Date number -->
              <div class="flex items-center justify-between px-2 pt-1.5 pb-0.5 flex-shrink-0">
                <span
                  class="text-xs font-medium leading-none"
                  :class="[
                    cell.isToday
                      ? 'h-5 w-5 flex items-center justify-center rounded-full bg-ink-blue-3 text-white'
                      : cell.isCurrentMonth ? 'text-ink-gray-7' : 'text-ink-gray-3',
                  ]"
                >{{ cell.day }}</span>
              </div>

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
            </div>
          </div>
        </div>
      </div>

      <!-- ── Collapse toggle ── -->
      <button
        class="flex-shrink-0 w-6 flex items-center justify-center border-l border-outline-gray-1 text-ink-gray-4 hover:text-ink-gray-7 hover:bg-surface-gray-1 transition-colors"
        :title="panelCollapsed ? __('Expand panel') : __('Collapse panel')"
        @click="toggleCollapse"
      >
        <LucideChevronLeft v-if="!panelCollapsed" class="h-4 w-4" />
        <LucideChevronRight v-else class="h-4 w-4" />
      </button>

      <!-- ── Detail panel ── -->
      <div
        v-show="!panelCollapsed"
        class="flex-shrink-0 w-96 border-l border-outline-gray-1 flex flex-col overflow-hidden"
      >
        <div
          v-if="!selectedTaskId"
          class="flex flex-col items-center justify-center h-full text-ink-gray-3 gap-2"
        >
          <LucideSquareDashed class="h-10 w-10 opacity-40" />
          <p class="text-sm">{{ __('Select a task to view details') }}</p>
        </div>
        <KanbanTaskPanel
          v-else
          :key="selectedTaskId"
          :task-id="selectedTaskId"
          :all-tags="allTags"
          @close="selectedTaskId = null"
          @saved="tasks.reload()"
        />
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { call, createListResource, dayjs } from "frappe-ui";
import LucideChevronLeft from "~icons/lucide/chevron-left";
import LucideChevronRight from "~icons/lucide/chevron-right";
import LucideLoader from "~icons/lucide/loader";
import LucideSearch from "~icons/lucide/search";
import LucideSquareDashed from "~icons/lucide/square-dashed";
import LucideUser from "~icons/lucide/user";
import LucideX from "~icons/lucide/x";
import { computed, onActivated, onMounted, ref } from "vue";
import KanbanTaskPanel from "./KanbanTaskPanel.vue";

// ── Panel state ──────────────────────────────────────────────
const COLLAPSE_KEY = "hd_task_calendar_panel_collapsed";
const selectedTaskId = ref<string | null>(null);
const panelCollapsed = ref(false);

onMounted(() => {
  panelCollapsed.value = window.innerWidth < 640 || localStorage.getItem(COLLAPSE_KEY) === "true";
  tasks.reload();
  loadAllTags();
});
onActivated(() => tasks.reload());

function toggleCollapse() {
  panelCollapsed.value = !panelCollapsed.value;
  localStorage.setItem(COLLAPSE_KEY, String(panelCollapsed.value));
}

// ── Month navigation ─────────────────────────────────────────
const today = dayjs();
const viewYear = ref(today.year());
const viewMonth = ref(today.month()); // 0-indexed

const monthLabel = computed(() =>
  dayjs().year(viewYear.value).month(viewMonth.value).format("MMMM YYYY")
);

function prevMonth() {
  if (viewMonth.value === 0) { viewMonth.value = 11; viewYear.value--; }
  else viewMonth.value--;
}
function nextMonth() {
  if (viewMonth.value === 11) { viewMonth.value = 0; viewYear.value++; }
  else viewMonth.value++;
}

// ── Calendar grid ────────────────────────────────────────────
const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

interface CalendarCell {
  dateStr: string;   // "YYYY-MM-DD"
  day: number;
  isCurrentMonth: boolean;
  isToday: boolean;
}

const calendarWeeks = computed<CalendarCell[][]>(() => {
  const first = dayjs().year(viewYear.value).month(viewMonth.value).startOf("month");
  // Align to Monday: (day() is 0=Sun, convert to Mon=0)
  const startOffset = (first.day() + 6) % 7;
  const gridStart = first.subtract(startOffset, "day");
  const todayStr = today.format("YYYY-MM-DD");

  const weeks: CalendarCell[][] = [];
  let cursor = gridStart;
  for (let w = 0; w < 6; w++) {
    const week: CalendarCell[] = [];
    for (let d = 0; d < 7; d++) {
      week.push({
        dateStr: cursor.format("YYYY-MM-DD"),
        day: cursor.date(),
        isCurrentMonth: cursor.month() === viewMonth.value,
        isToday: cursor.format("YYYY-MM-DD") === todayStr,
      });
      cursor = cursor.add(1, "day");
    }
    weeks.push(week);
  }
  return weeks;
});

// ── Task data ────────────────────────────────────────────────
const tasks = createListResource({
  doctype: "HD Task",
  fields: ["name", "title", "status", "priority", "due_date", "assigned_to", "ticket", "_user_tags"],
  filters: [["due_date", "is", "set"]],
  orderBy: "due_date asc",
  pageLength: 9999,
  auto: true,
});

// Build a map: dateStr → task[]
const tasksByDate = computed(() => {
  const map: Record<string, any[]> = {};
  for (const task of (tasks.data ?? [])) {
    if (!task.due_date) continue;
    if (searchResultNames.value !== null && !searchResultNames.value.has(task.name)) continue;
    if (filterAssignee.value && task.assigned_to !== filterAssignee.value) continue;
    const d = task.due_date.split(" ")[0]; // strip time portion if present
    if (!map[d]) map[d] = [];
    map[d].push(task);
  }
  return map;
});

const MAX_VISIBLE = 3;
function visibleTasksForDate(dateStr: string) {
  return (tasksByDate.value[dateStr] ?? []).slice(0, MAX_VISIBLE);
}
function overflowCount(dateStr: string) {
  const count = (tasksByDate.value[dateStr] ?? []).length;
  return Math.max(0, count - MAX_VISIBLE);
}

// ── Tags ─────────────────────────────────────────────────────
const allTags = ref<string[]>([]);
async function loadAllTags() {
  try {
    allTags.value = await call("helpdesk.helpdesk.doctype.hd_task.hd_task.get_all_task_tags") ?? [];
  } catch { /* non-critical */ }
}

// ── Filters ──────────────────────────────────────────────────
const filterSearch = ref("");
const filterAssignee = ref("");
const searchResultNames = ref<Set<string> | null>(null);
const searchLoading = ref(false);
let searchTimer: ReturnType<typeof setTimeout> | null = null;

const hasFilters = computed(() => !!(filterSearch.value || filterAssignee.value));

const uniqueAssignees = computed<string[]>(() => {
  const set = new Set<string>();
  for (const t of (tasks.data ?? [])) {
    if (t.assigned_to) set.add(t.assigned_to);
  }
  return [...set].sort();
});

function onSearchInput() {
  if (searchTimer) clearTimeout(searchTimer);
  if (!filterSearch.value.trim()) { searchResultNames.value = null; return; }
  searchLoading.value = true;
  searchTimer = setTimeout(async () => {
    try {
      const names: string[] = await call(
        "helpdesk.helpdesk.doctype.hd_task.hd_task.search_tasks",
        { query: filterSearch.value.trim() }
      );
      searchResultNames.value = new Set(names);
    } catch {
      searchResultNames.value = null;
    } finally {
      searchLoading.value = false;
    }
  }, 300);
}

function clearFilters() {
  filterSearch.value = "";
  filterAssignee.value = "";
  searchResultNames.value = null;
}

// ── Status chip colours ───────────────────────────────────────
const STATUS_CHIP: Record<string, string> = {
  "Backlog":     "bg-gray-100 text-gray-600",
  "Todo":        "bg-blue-100 text-blue-700",
  "In Progress": "bg-orange-100 text-orange-700",
  "Done":        "bg-green-100 text-green-700",
};
function statusChipClass(status: string) {
  return STATUS_CHIP[status] ?? "bg-surface-gray-2 text-ink-gray-6";
}
</script>
