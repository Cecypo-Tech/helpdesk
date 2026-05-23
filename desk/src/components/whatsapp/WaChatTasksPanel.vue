<template>
  <div class="flex h-full w-[300px] shrink-0 flex-col border-l border-outline-gray-2 bg-surface-white">

    <!-- List header -->
    <div v-if="view === 'list'" class="flex items-center justify-between border-b border-outline-gray-2 px-4 py-2.5">
      <span class="text-sm font-semibold text-ink-gray-9">Tasks & Tickets</span>
      <div class="flex items-center gap-1">
        <button
          class="flex h-6 w-6 items-center justify-center rounded-full text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8"
          title="New task"
          @click="openNewTask"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
        </button>
        <button
          class="flex h-6 items-center gap-1 rounded px-1.5 text-[11px] font-medium text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8"
          title="New ticket"
          @click="openNewTicket"
        >
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><polyline points="14 2 14 8 20 8"/>
          </svg>
          Ticket
        </button>
        <button
          class="flex h-6 w-6 items-center justify-center rounded-full text-ink-gray-4 hover:bg-surface-gray-2 hover:text-ink-gray-7"
          @click="$emit('close')"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- Detail header -->
    <div v-else class="flex items-center gap-2 border-b border-outline-gray-2 px-4 py-2.5">
      <button
        class="flex items-center gap-1 text-xs text-ink-gray-5 hover:text-ink-gray-8"
        @click="view = 'list'"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
        Back
      </button>
      <span class="flex-1 text-sm font-semibold text-ink-gray-9">Task details</span>
      <button
        class="text-ink-gray-4 hover:text-ink-gray-7"
        @click="$emit('close')"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>

    <!-- New task inline form -->
    <div v-if="view === 'list' && showNewTask" class="border-b border-outline-gray-2 bg-surface-gray-1 px-4 py-3">
      <div class="mb-1 text-[11px] font-medium text-ink-gray-5">New task</div>
      <input
        ref="newTaskInputRef"
        v-model="newTaskTitle"
        type="text"
        placeholder="Task title..."
        class="w-full rounded border border-outline-gray-3 bg-surface-white px-2 py-1.5 text-xs text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none"
        @keydown.enter="createTask"
        @keydown.esc="showNewTask = false"
      />
      <div class="mt-2 flex gap-1.5">
        <button
          class="rounded-lg bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          :disabled="!newTaskTitle.trim() || creatingTask"
          @click="createTask"
        >{{ creatingTask ? 'Creating…' : 'Create task' }}</button>
        <button
          class="rounded-lg border border-outline-gray-3 px-2.5 py-1 text-xs text-ink-gray-6 hover:bg-surface-gray-2"
          @click="showNewTask = false"
        >Cancel</button>
      </div>
    </div>

    <!-- New ticket inline form -->
    <div v-if="view === 'list' && showNewTicket" class="border-b border-outline-gray-2 bg-surface-gray-1 px-4 py-3">
      <div class="mb-1 text-[11px] font-medium text-ink-gray-5">New ticket</div>
      <input
        ref="newTicketInputRef"
        v-model="newTicketSubject"
        type="text"
        placeholder="Ticket subject..."
        class="w-full rounded border border-outline-gray-3 bg-surface-white px-2 py-1.5 text-xs text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none"
        @keydown.enter="createTicket"
        @keydown.esc="showNewTicket = false"
      />
      <div class="mt-2 flex gap-1.5">
        <button
          class="rounded-lg bg-green-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-green-700 disabled:opacity-50"
          :disabled="!newTicketSubject.trim() || creatingTicket"
          @click="createTicket"
        >{{ creatingTicket ? 'Creating…' : 'Create ticket' }}</button>
        <button
          class="rounded-lg border border-outline-gray-3 px-2.5 py-1 text-xs text-ink-gray-6 hover:bg-surface-gray-2"
          @click="showNewTicket = false"
        >Cancel</button>
      </div>
    </div>

    <!-- List view -->
    <div v-if="view === 'list'" class="flex-1 overflow-y-auto">
      <div v-if="(tasksResource.loading && !tasks.length) || (ticketsResource.loading && !tickets.length)" class="flex justify-center py-10">
        <LoadingIndicator :scale="4" class="text-ink-gray-4" />
      </div>
      <div
        v-else-if="!tasks.length && !tickets.length"
        class="flex flex-col items-center justify-center py-16 text-center"
      >
        <svg class="mb-3 h-8 w-8 text-ink-gray-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
        </svg>
        <p class="text-xs text-ink-gray-5">No tasks or tickets linked</p>
        <p class="mt-1 text-[11px] text-ink-gray-4">Use + to create one</p>
      </div>

      <template v-else>
        <!-- Tasks section -->
        <div v-if="tasks.length">
          <div class="border-b border-outline-gray-1 bg-surface-gray-1 px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-ink-gray-4">Tasks</div>
          <div class="divide-y divide-outline-gray-1">
            <button
              v-for="task in tasks"
              :key="task.name"
              class="flex w-full items-start gap-2.5 px-4 py-2.5 text-left hover:bg-surface-gray-1"
              :class="{ 'opacity-50': task.status === 'Done' }"
              @click="openTask(task.name)"
            >
              <span
                class="mt-0.5 h-2 w-2 shrink-0 rounded-full"
                :class="statusColor(task.status)"
              />
              <div class="min-w-0 flex-1">
                <div class="truncate text-xs font-medium text-ink-gray-9" :class="{ 'line-through': task.status === 'Done' }">{{ task.title }}</div>
                <div class="mt-0.5 flex items-center gap-1.5">
                  <span class="text-[10px] text-ink-gray-5">{{ task.status }}</span>
                  <span
                    v-if="task.priority"
                    class="rounded px-1 py-0.5 text-[9px] font-medium"
                    :class="priorityClass(task.priority)"
                  >{{ task.priority }}</span>
                  <span v-if="task.due_date" class="text-[10px]" :class="isOverdue(task.due_date) ? 'text-red-500' : 'text-ink-gray-4'">
                    {{ task.due_date }}
                  </span>
                </div>
              </div>
              <svg class="mt-1 h-3 w-3 shrink-0 text-ink-gray-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="9 18 15 12 9 6"/>
              </svg>
            </button>
          </div>
        </div>

        <!-- Tickets section -->
        <div v-if="tickets.length">
          <div class="border-b border-t border-outline-gray-1 bg-surface-gray-1 px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-ink-gray-4">Tickets</div>
          <div class="divide-y divide-outline-gray-1">
            <a
              v-for="ticket in tickets"
              :key="ticket.name"
              :href="`/helpdesk/tickets/${ticket.name}`"
              target="_blank"
              class="flex w-full items-start gap-2.5 px-4 py-2.5 text-left hover:bg-surface-gray-1"
            >
              <span class="mt-0.5 h-2 w-2 shrink-0 rounded-full" :class="ticketStatusColor(ticket.status)" />
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-1.5">
                  <span class="truncate text-xs font-medium text-ink-gray-9">{{ ticket.subject }}</span>
                  <span class="shrink-0 text-[10px] text-ink-gray-4">#{{ ticket.name }}</span>
                </div>
                <div class="mt-0.5 flex items-center gap-1.5">
                  <span class="text-[10px] text-ink-gray-5">{{ ticket.status }}</span>
                  <span
                    v-if="ticket.priority"
                    class="rounded px-1 py-0.5 text-[9px] font-medium"
                    :class="priorityClass(ticket.priority)"
                  >{{ ticket.priority }}</span>
                </div>
              </div>
              <!-- external link icon -->
              <svg class="mt-1 h-3 w-3 shrink-0 text-ink-gray-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                <polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
              </svg>
            </a>
          </div>
        </div>
      </template>
    </div>

    <!-- Task detail (KanbanTaskPanel) -->
    <KanbanTaskPanel
      v-if="view === 'detail' && selectedTaskId"
      :taskId="selectedTaskId"
      :allTags="[]"
      @close="view = 'list'"
      @saved="() => { tasksResource.reload(); emit('tasksChanged'); }"
    />
  </div>
</template>

<script setup lang="ts">
import { call, createResource, LoadingIndicator, toast } from "frappe-ui";
import { computed, nextTick, ref, watch } from "vue";
import KanbanTaskPanel from "@/pages/tasks/KanbanTaskPanel.vue";

const props = defineProps<{
  jid: string | null;
  line?: string;
}>();
const emit = defineEmits<{ close: []; tasksChanged: [] }>();

type View = "list" | "detail";
const view = ref<View>("list");
const selectedTaskId = ref<string>("");

// ── Task list ────────────────────────────────────────────────────────────────

const tasksResource = createResource({
  url: "helpdesk.integrations.evolution.get_tasks_for_jid",
  auto: false,
});

const tasks = computed<any[]>(() => tasksResource.data || []);

// ── Ticket list ───────────────────────────────────────────────────────────────

const ticketsResource = createResource({
  url: "helpdesk.integrations.evolution.get_tickets_for_jid",
  auto: false,
});

const tickets = computed<any[]>(() => ticketsResource.data || []);

watch(() => props.jid, (jid) => {
  if (jid) {
    view.value = "list";
    tasksResource.update({ params: { jid } });
    tasksResource.reload();
    ticketsResource.update({ params: { jid } });
    ticketsResource.reload();
  }
}, { immediate: true });

function openTask(name: string) {
  selectedTaskId.value = name;
  view.value = "detail";
}

function statusColor(status: string) {
  const map: Record<string, string> = {
    Backlog: "bg-ink-gray-3",
    Todo: "bg-blue-400",
    "In Progress": "bg-orange-400",
    Done: "bg-green-500",
  };
  return map[status] || "bg-ink-gray-3";
}

function ticketStatusColor(status: string) {
  const map: Record<string, string> = {
    Open: "bg-blue-400",
    Replied: "bg-orange-400",
    Resolved: "bg-green-500",
    Closed: "bg-ink-gray-3",
  };
  return map[status] || "bg-ink-gray-3";
}

function priorityClass(priority: string) {
  const map: Record<string, string> = {
    Low: "bg-surface-gray-2 text-ink-gray-6",
    Medium: "bg-orange-50 text-orange-700",
    High: "bg-red-50 text-red-700",
    Urgent: "bg-red-100 text-red-800",
  };
  return map[priority] || "bg-surface-gray-2 text-ink-gray-6";
}

function isOverdue(date: string) {
  return new Date(date) < new Date(new Date().toDateString());
}

// ── New task ─────────────────────────────────────────────────────────────────

const showNewTask = ref(false);
const newTaskTitle = ref("");
const creatingTask = ref(false);
const newTaskInputRef = ref<HTMLInputElement | null>(null);

function openNewTask() {
  showNewTicket.value = false;
  showNewTask.value = true;
  newTaskTitle.value = "";
  nextTick(() => newTaskInputRef.value?.focus());
}

async function createTask() {
  if (!newTaskTitle.value.trim() || !props.jid) return;
  creatingTask.value = true;
  try {
    const taskName = await call("helpdesk.integrations.evolution.create_task_from_chat", {
      jid: props.jid,
      line: props.line || "",
      title: newTaskTitle.value.trim(),
    });
    showNewTask.value = false;
    newTaskTitle.value = "";
    await tasksResource.reload();
    emit("tasksChanged");
    toast.success("Task created");
    if (typeof taskName === "string") openTask(taskName);
  } catch {
    toast.error("Failed to create task");
  } finally {
    creatingTask.value = false;
  }
}

// ── New ticket ────────────────────────────────────────────────────────────────

const showNewTicket = ref(false);
const newTicketSubject = ref("");
const creatingTicket = ref(false);
const newTicketInputRef = ref<HTMLInputElement | null>(null);

function openNewTicket() {
  showNewTask.value = false;
  showNewTicket.value = true;
  newTicketSubject.value = "";
  nextTick(() => newTicketInputRef.value?.focus());
}

async function createTicket() {
  if (!newTicketSubject.value.trim() || !props.jid) return;
  creatingTicket.value = true;
  try {
    await call("helpdesk.integrations.evolution.create_ticket_from_chat", {
      jid: props.jid,
      line: props.line || "",
      subject: newTicketSubject.value.trim(),
    });
    showNewTicket.value = false;
    newTicketSubject.value = "";
    await ticketsResource.reload();
    emit("tasksChanged");
    toast.success("Ticket created");
  } catch {
    toast.error("Failed to create ticket");
  } finally {
    creatingTicket.value = false;
  }
}
</script>
