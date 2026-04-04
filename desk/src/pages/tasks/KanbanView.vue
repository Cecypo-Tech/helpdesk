<template>
  <div class="flex flex-col h-full overflow-hidden">

    <!-- ── Filter bar ── (content added in Task 5) -->
    <div id="kanban-filter-bar-placeholder" />

    <!-- ── Kanban columns ── -->
    <div class="flex-1 flex overflow-x-auto gap-3 p-4">
      <div
        v-for="col in columns"
        :key="col.status"
        class="flex flex-col w-72 flex-shrink-0 rounded-lg bg-surface-gray-1 border border-outline-gray-2 transition-all"
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
            <!-- Title -->
            <p class="text-sm font-medium text-ink-gray-9 leading-snug mb-2">{{ card.title }}</p>

            <!-- Tags row -->
            <div v-if="cardTags(card).length" class="flex flex-wrap gap-1 mb-2">
              <span
                v-for="tag in cardTags(card).slice(0, 2)"
                :key="tag"
                class="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium"
                :class="tagColor(tag)"
              >{{ tag }}</span>
              <span
                v-if="cardTags(card).length > 2"
                class="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-surface-gray-2 text-ink-gray-5"
              >+{{ cardTags(card).length - 2 }}</span>
            </div>

            <!-- Bottom row: priority | due date | avatar -->
            <div class="flex items-center gap-2">
              <!-- Priority bars -->
              <span
                v-if="card.priority"
                :title="card.priority"
                class="flex items-end gap-[2px] flex-shrink-0"
              >
                <span class="w-[3px] rounded-sm" :class="[priorityBarH(card.priority, 0), priorityBarColor(card.priority)]" />
                <span class="w-[3px] rounded-sm" :class="[priorityBarH(card.priority, 1), priorityBarColor(card.priority)]" />
                <span class="w-[3px] rounded-sm" :class="[priorityBarH(card.priority, 2), priorityBarColor(card.priority)]" />
              </span>

              <!-- Due date -->
              <span
                v-if="card.due_date"
                class="flex items-center gap-1 text-xs"
                :class="relativeDue(card.due_date).cls"
              >
                <LucideCalendar class="h-3 w-3 flex-shrink-0" />
                {{ relativeDue(card.due_date).label }}
              </span>

              <!-- Spacer -->
              <span class="flex-1" />

              <!-- Assignee avatar -->
              <span
                v-if="card.assigned_to"
                :title="card.assigned_to"
                class="h-6 w-6 rounded-full flex items-center justify-center text-[10px] font-semibold flex-shrink-0"
                :class="[avatarColor(card.assigned_to).bg, avatarColor(card.assigned_to).text]"
              >{{ avatarInitials(card.assigned_to) }}</span>
            </div>

            <!-- Ticket ref -->
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
        <p class="text-sm">{{ __("Select a task to view details") }}</p>
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
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { call, createListResource, dayjs, toast } from "frappe-ui";
import LucideCalendar from "~icons/lucide/calendar";
import LucideChevronLeft from "~icons/lucide/chevron-left";
import LucideChevronRight from "~icons/lucide/chevron-right";
import LucidePlus from "~icons/lucide/plus";
import LucideSquareDashed from "~icons/lucide/square-dashed";
import { onActivated, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import KanbanTaskPanel from "./KanbanTaskPanel.vue";

const router = useRouter();

// ── Panel state ──────────────────────────────────────────────
const COLLAPSE_KEY = "hd_task_kanban_panel_collapsed";
const selectedTaskId = ref<string | null>(null);
const panelCollapsed = ref(false);

onMounted(() => {
  if (window.innerWidth < 640) {
    panelCollapsed.value = true;
  } else {
    panelCollapsed.value = localStorage.getItem(COLLAPSE_KEY) === "true";
  }
  tasks.reload();
  loadAllTags();
});

onActivated(() => tasks.reload());

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
  fields: ["name", "title", "status", "priority", "due_date", "assigned_to", "ticket", "_user_tags"],
  filters: [],
  orderBy: "modified desc",
  pageLength: 999,
  auto: true,
});

function getCardsForStatus(status: string) {
  return (tasks.data ?? []).filter((t: any) => t.status === status);
}

// ── All tags (for panel autocomplete) ────────────────────────
const allTags = ref<string[]>([]);

async function loadAllTags() {
  try {
    const result = await call("helpdesk.helpdesk.doctype.hd_task.hd_task.get_all_task_tags");
    allTags.value = result ?? [];
  } catch {
    // non-critical
  }
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

  const live = (tasks.data ?? []).find((t: any) => t.name === card.name);
  if (live) live.status = targetStatus;

  try {
    await call(
      "helpdesk.helpdesk.doctype.hd_task.hd_task.set_task_field",
      { task_name: card.name, fieldname: "status", value: targetStatus }
    );
    tasks.reload();
  } catch {
    if (live) live.status = card.status;
    toast.error(__("Failed to move task"));
  }
}

// ── Helpers ──────────────────────────────────────────────────
function createTask(status: string) {
  router.push({ name: "TaskAgentNew", query: { status } });
}

function formatDate(d: string) {
  if (!d) return "";
  return dayjs(d).format((window as any).date_format?.toUpperCase() || "DD-MM-YYYY");
}

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

// ── Relative due date ─────────────────────────────────────────
function relativeDue(d: string): { label: string; cls: string } {
  if (!d) return { label: "", cls: "" };
  const today = dayjs().startOf("day");
  const due = dayjs(d).startOf("day");
  const diff = due.diff(today, "day");
  if (diff < -1) return { label: `${Math.abs(diff)}d ago`, cls: "text-red-500" };
  if (diff === -1) return { label: "Yesterday", cls: "text-red-500" };
  if (diff === 0)  return { label: "Today", cls: "text-ink-gray-6" };
  if (diff === 1)  return { label: "Tomorrow", cls: "text-blue-500" };
  if (diff <= 7)   return { label: `In ${diff}d`, cls: "text-ink-gray-5" };
  return { label: formatDate(d), cls: "text-ink-gray-4" };
}

// ── Tags ──────────────────────────────────────────────────────
const TAG_CLASSES = [
  "bg-blue-100 text-blue-700",
  "bg-green-100 text-green-700",
  "bg-purple-100 text-purple-700",
  "bg-orange-100 text-orange-700",
  "bg-pink-100 text-pink-700",
  "bg-teal-100 text-teal-700",
];

function tagColor(tag: string): string {
  return TAG_CLASSES[hashStr(tag) % TAG_CLASSES.length];
}

function cardTags(card: any): string[] {
  if (!card._user_tags) return [];
  return card._user_tags.split(",").map((t: string) => t.trim()).filter(Boolean);
}
</script>
