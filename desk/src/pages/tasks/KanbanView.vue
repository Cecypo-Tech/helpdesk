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
      <!-- No task selected -->
      <div
        v-if="!selectedTaskId"
        class="flex flex-col items-center justify-center h-full text-ink-gray-3 gap-2"
      >
        <LucideSquareDashed class="h-10 w-10 opacity-40" />
        <p class="text-sm">{{ __("Select a task to view details") }}</p>
      </div>

      <!-- Panel — :key forces recreation when task changes -->
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
import { call, createListResource, dayjs, toast } from "frappe-ui";
import LucideCalendar from "~icons/lucide/calendar";
import LucideChevronLeft from "~icons/lucide/chevron-left";
import LucideChevronRight from "~icons/lucide/chevron-right";
import LucidePlus from "~icons/lucide/plus";
import LucideSquareDashed from "~icons/lucide/square-dashed";
import LucideUser from "~icons/lucide/user";
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
  // Safety-net: ensure data loads even if auto:true on createListResource
  // doesn't fire reliably on SPA navigation.
  tasks.reload();
});

// Handle re-activation if this component is wrapped in <KeepAlive>.
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
  return dayjs(d).format((window as any).date_format?.toUpperCase() || "DD-MM-YYYY");
}

function isOverdue(d: string) {
  if (!d) return false;
  return new Date(d) < new Date();
}
</script>
