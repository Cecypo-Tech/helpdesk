<template>
  <div class="flex flex-col h-full bg-surface-white overflow-hidden">

    <!-- Header -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-outline-gray-1 flex-shrink-0">
      <span class="text-xs text-green-600 transition-opacity duration-300 min-w-0">
        {{ savedIndicator ? __('✓ Saved') : '' }}
      </span>
      <div class="flex items-center gap-3 flex-shrink-0">
        <button
          class="text-xs text-ink-gray-5 hover:text-ink-blue-4"
          @click="router.push({ name: 'TaskAgent', params: { taskId } })"
        >
          {{ __('Open full page →') }}
        </button>
        <!-- Copy button -->
        <button
          class="text-ink-gray-4 hover:text-ink-gray-7"
          :title="__('Copy task summary')"
          @click="copyToClipboard"
        >
          <LucideClipboard class="h-4 w-4" />
        </button>
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
            :value="form.priority"
            doctype="HD Ticket Priority"
            :placeholder="__('—')"
            class="form-control"
            @change="(val) => { form.priority = val; saveField('priority', val || null); }"
          />
        </div>
      </div>

      <!-- Assigned To + Due Date -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Assigned To') }}</label>
          <Link
            :value="form.assigned_to"
            doctype="HD Agent"
            :placeholder="__('—')"
            class="form-control"
            @change="(val) => { form.assigned_to = val; saveField('assigned_to', val || null); }"
          />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Due Date') }}</label>
          <DatePicker
            v-model="form.due_date"
            :format="dateFormat"
            :clearable="true"
            :placeholder="__('—')"
            :input-class="isOverdue(form.due_date, form.due_time) ? '!text-red-500' : ''"
            @change="(val) => saveField('due_date', val || null)"
          />
        </div>
      </div>

      <!-- Due Time -->
      <div class="grid grid-cols-2 gap-3" v-if="form.due_date">
        <div />
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Due Time') }}</label>
          <input
            v-model="form.due_time"
            type="time"
            class="text-sm rounded border border-outline-gray-2 bg-surface-white px-2 py-1.5 text-ink-gray-8 focus:outline-none focus:border-outline-gray-4"
            @change="saveField('due_time', form.due_time || null)"
          />
        </div>
      </div>

      <!-- Ticket + Team -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="flex items-center gap-1 text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">
            {{ __('Ticket') }}
            <button
              v-if="form.ticket"
              class="text-ink-blue-4 hover:text-ink-blue-5 normal-case"
              :title="__('Open ticket')"
              @click="router.push({ name: 'TicketAgent', params: { ticketId: form.ticket } })"
            >
              <LucideExternalLink class="h-3 w-3" />
            </button>
          </label>
          <Link
            :value="form.ticket"
            doctype="HD Ticket"
            :placeholder="__('—')"
            class="form-control"
            @change="(val) => { form.ticket = val; saveField('ticket', val || null); }"
          />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Team') }}</label>
          <Link
            :value="form.team"
            doctype="HD Team"
            :placeholder="__('—')"
            class="form-control"
            @change="(val) => { form.team = val; saveField('team', val || null); }"
          />
        </div>
      </div>

      <!-- Tags -->
      <div class="flex flex-col gap-1">
        <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Tags') }}</label>
        <TagInput
          :model-value="form.user_tags"
          :all-tags="allTags"
          @update:model-value="(val) => { form.user_tags = val; saveField('_user_tags', val || null); }"
        />
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

        <div v-if="form.subtasks.length" class="h-1 w-full rounded-full bg-surface-gray-2 overflow-hidden">
          <div
            class="h-full rounded-full bg-green-500 transition-all duration-300"
            :style="{ width: progressPct + '%' }"
          />
        </div>

        <div
          v-for="(sub, idx) in form.subtasks"
          :key="sub.name || idx"
          class="flex items-center gap-2 rounded p-1 hover:bg-surface-gray-1 group"
        >
          <Checkbox
            :model-value="sub.status === 'Done'"
            class="flex-shrink-0"
            @update:model-value="toggleSubtask(idx)"
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

    <!-- Footer: created by / on -->
    <div
      v-if="task.doc"
      class="flex-shrink-0 flex items-center gap-3 px-4 py-2 border-t border-outline-gray-1 text-xs text-ink-gray-4"
    >
      <span>{{ __('Created by') }} <span class="font-medium text-ink-gray-6">{{ task.doc.owner }}</span></span>
      <span>{{ formatCreation(task.doc.creation) }}</span>
    </div>

  </div>
</template>

<script setup lang="ts">
import TagInput from "@/components/TagInput.vue";
import Link from "@/components/frappe-ui/Link.vue";
import { __ } from "@/translation";
import {
  call,
  Checkbox,
  createDocumentResource,
  DatePicker,
  dayjs,
  LoadingIndicator,
  TextEditor,
  toast,
} from "frappe-ui";
import LucideClipboard from "~icons/lucide/clipboard";
import LucideExternalLink from "~icons/lucide/external-link";
import LucidePlus from "~icons/lucide/plus";
import LucideX from "~icons/lucide/x";
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

const dateFormat = (window as any).date_format?.toUpperCase() || "DD-MM-YYYY";

const props = defineProps<{
  taskId: string;
  allTags: string[];
}>();
const emit = defineEmits<{ close: []; saved: [] }>();
const router = useRouter();

const savedIndicator = ref(false);
const isFormLoaded = ref(false);
const currentModified = ref<string | null>(null);
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
  due_time: "",
  ticket: "",
  team: "",
  description: "",
  user_tags: "",   // mirrors _user_tags (underscore-prefixed fields can't be reactive keys)
  subtasks: [] as Subtask[],
});

const task = createDocumentResource({
  doctype: "HD Task",
  name: props.taskId,
  auto: true,
  onError() {
    toast.error(__("Task not found"));
    emit("close");
  },
});

watch(
  () => task.doc,
  (doc: any) => {
    if (!doc) return;
    isFormLoaded.value = false;
    currentModified.value = doc.modified ?? null;
    form.title = doc.title ?? "";
    form.status = doc.status ?? "Backlog";
    form.priority = doc.priority ?? "";
    form.assigned_to = doc.assigned_to ?? "";
    form.due_date = doc.due_date ?? "";
    form.due_time = doc.due_time ?? "";
    form.ticket = doc.ticket ?? "";
    form.team = doc.team ?? "";
    form.description = doc.description ?? "";
    form.user_tags = doc._user_tags ?? "";
    form.subtasks = (doc.subtasks ?? []).map((s: any) => ({
      name: s.name,
      title: s.title ?? "",
      status: s.status ?? "Backlog",
      due_date: s.due_date ?? "",
    }));
    nextTick(() => { isFormLoaded.value = true; });
  },
  { immediate: true }
);

onMounted(() => task.reload());

const doneCount = computed(() => form.subtasks.filter((s) => s.status === "Done").length);
const progressPct = computed(() =>
  form.subtasks.length ? Math.round((doneCount.value / form.subtasks.length) * 100) : 0
);

function errorMessage(e: any, fallback: string): string {
  if (e?.exc_type === "TimestampMismatchError") {
    return __("Document was modified elsewhere — close and reopen the panel to refresh.");
  }
  return e?.message || e?.exc || fallback;
}

function flashSaved() {
  savedIndicator.value = true;
  if (savedTimer) clearTimeout(savedTimer);
  savedTimer = setTimeout(() => { savedIndicator.value = false; }, 1500);
}

async function saveField(fieldname: string, value: any) {
  try {
    const result = await call(
      "helpdesk.helpdesk.doctype.hd_task.hd_task.set_task_field",
      { task_name: props.taskId, fieldname, value: value || null }
    );
    if (result?.modified) currentModified.value = result.modified;
    flashSaved();
    emit("saved");
  } catch (e: any) {
    toast.error(errorMessage(e, __("Failed to save")));
  }
}

function debouncedSaveDescription(val?: string) {
  if (!isFormLoaded.value) return;
  if (val !== undefined) form.description = val;
  if (descTimer) clearTimeout(descTimer);
  descTimer = setTimeout(() => saveField("description", form.description), 800);
}

async function saveSubtasks() {
  try {
    const result = await call(
      "helpdesk.helpdesk.doctype.hd_task.hd_task.save_task_subtasks",
      {
        task_name: props.taskId,
        subtasks: JSON.stringify(
          form.subtasks.map((s) => ({
            name: s.name || null,
            title: s.title,
            status: s.status,
            due_date: s.due_date || null,
          }))
        ),
      }
    );
    if (result?.modified) currentModified.value = result.modified;
    task.reload();
    flashSaved();
    emit("saved");
  } catch (e: any) {
    toast.error(errorMessage(e, __("Failed to save subtasks")));
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

function isOverdue(d: string, t?: string) {
  if (!d) return false;
  const due = t ? new Date(`${d}T${t}`) : new Date(d);
  return due < new Date();
}

function formatCreation(ts: string): string {
  if (!ts) return "";
  return dayjs(ts).format("DD MMM YYYY");
}

function copyToClipboard() {
  const lines: string[] = [
    `Task: ${form.title}`,
    `Status: ${form.status}`,
  ];
  if (form.user_tags) lines.push(`Tags: ${form.user_tags}`);
  if (form.subtasks.length) {
    lines.push(`Progress: ${doneCount.value} of ${form.subtasks.length} subtasks completed`);
    lines.push("");
    lines.push("Subtasks:");
    for (const s of form.subtasks) {
      const check = s.status === "Done" ? "[x]" : "[ ]";
      lines.push(`${check} ${s.title} (${s.status})`);
    }
  }
  navigator.clipboard
    .writeText(lines.join("\n"))
    .then(() => toast.success(__("Copied to clipboard")))
    .catch(() => toast.error(__("Could not copy to clipboard")));
}
</script>
