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
