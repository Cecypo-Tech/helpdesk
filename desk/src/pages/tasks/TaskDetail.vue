<template>
  <div class="flex flex-col overflow-y-auto">
    <LayoutHeader>
      <template #left-header>
        <Breadcrumbs
          :items="[
            { label: __('Tasks'), route: { name: 'TasksAgent' } },
            { label: task.doc?.title || taskId, route: { name: 'TaskAgent', params: { taskId } } },
          ]"
        />
      </template>
      <template #right-header>
        <Button
          :label="__('Copy')"
          theme="gray"
          variant="outline"
          :title="__('Copy task summary to clipboard')"
          @click="copyToClipboard"
        >
          <template #prefix>
            <LucideClipboard class="h-4 w-4" />
          </template>
        </Button>
        <Button
          :label="__('Save')"
          theme="gray"
          variant="solid"
          :loading="isSaving"
          :disabled="!isDirty"
          @click="saveTask"
        />
      </template>
    </LayoutHeader>

    <div
      v-if="task.loading"
      class="flex items-center justify-center h-64"
    >
      <LoadingIndicator class="h-6 w-6 text-ink-gray-4" />
    </div>

    <div
      v-else-if="task.doc"
      class="flex flex-col gap-5 py-6 mx-auto w-full max-w-3xl px-5 overflow-auto"
    >
      <!-- Title -->
      <div class="flex flex-col gap-1.5">
        <label class="block text-sm font-medium text-ink-gray-7">
          {{ __("Title") }}
          <span class="text-red-500"> *</span>
        </label>
        <FormControl
          v-model="form.title"
          type="text"
          :placeholder="__('Task title')"
                  />
      </div>

      <!-- Status + Priority row -->
      <div class="grid grid-cols-2 gap-4">
        <div class="flex flex-col gap-1.5">
          <label class="block text-sm font-medium text-ink-gray-7">
            {{ __("Status") }}
          </label>
          <FormControl
            v-model="form.status"
            type="select"
            :options="statusOptions"
                      />
        </div>
        <div class="flex flex-col gap-1.5">
          <label class="block text-sm font-medium text-ink-gray-7">
            {{ __("Priority") }}
          </label>
          <Link
            v-model="form.priority"
            doctype="HD Ticket Priority"
            :placeholder="__('Select priority')"
            class="form-control"
                      />
        </div>
      </div>

      <!-- Assigned To + Due Date row -->
      <div class="grid grid-cols-2 gap-4">
        <div class="flex flex-col gap-1.5">
          <label class="block text-sm font-medium text-ink-gray-7">
            {{ __("Assigned To") }}
          </label>
          <Link
            v-model="form.assigned_to"
            doctype="HD Agent"
            :placeholder="__('Assign an agent')"
            class="form-control"
                      />
        </div>
        <div class="flex flex-col gap-1.5">
          <label class="block text-sm font-medium text-ink-gray-7">
            {{ __("Due Date") }}
          </label>
          <DatePicker
            v-model="form.due_date"
            :format="dateFormat"
            :clearable="true"
            :placeholder="__('Select date')"
          />
        </div>
      </div>

      <!-- Linked Ticket + Team row -->
      <div class="grid grid-cols-2 gap-4">
        <div class="flex flex-col gap-1.5">
          <label class="flex items-center gap-1 text-sm font-medium text-ink-gray-7">
            {{ __("Linked Ticket") }}
            <button
              v-if="form.ticket"
              class="text-ink-blue-4 hover:text-ink-blue-5"
              :title="__('Open ticket')"
              @click="router.push({ name: 'TicketAgent', params: { ticketId: form.ticket } })"
            >
              <LucideExternalLink class="h-3.5 w-3.5" />
            </button>
          </label>
          <Link
            v-model="form.ticket"
            doctype="HD Ticket"
            :placeholder="__('Link to a ticket')"
            class="form-control"
          />
        </div>
        <div class="flex flex-col gap-1.5">
          <label class="block text-sm font-medium text-ink-gray-7">
            {{ __("Team") }}
          </label>
          <Link
            v-model="form.team"
            doctype="HD Team"
            :placeholder="__('Assign a team')"
            class="form-control"
                      />
        </div>
      </div>

      <!-- Customer -->
      <div class="flex flex-col gap-1.5">
        <label class="block text-sm font-medium text-ink-gray-7">
          {{ __("Customer") }}
        </label>
        <Link
          v-model="form.customer"
          doctype="HD Customer"
          :placeholder="__('Select a customer')"
          class="form-control"
        />
      </div>

      <!-- Tags -->
      <div class="flex flex-col gap-1.5">
        <label class="block text-sm font-medium text-ink-gray-7">
          {{ __("Tags") }}
        </label>
        <TagInput
          v-model="form.user_tags"
          :all-tags="allTags"
        />
      </div>

      <!-- Description -->
      <div class="flex flex-col gap-1.5">
        <label class="block text-sm font-medium text-ink-gray-7">
          {{ __("Description") }}
        </label>
        <TextEditor
          v-model:content="form.description"
          :editable="true"
          editor-class="min-h-[8rem] prose-f p-2 rounded border border-outline-gray-2 focus-within:border-outline-gray-4"
          :placeholder="__('Add a description...')"
          @change="(val) => { form.description = val; if (isFormLoaded) isDirty = true; }"
        />
      </div>

      <!-- Subtasks -->
      <div class="flex flex-col gap-3">
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-semibold text-ink-gray-8">
            {{ __("Subtasks") }}
          </h3>
          <span class="text-sm text-ink-gray-5">
            {{ doneCount }} / {{ form.subtasks.length }} {{ __("done") }}
          </span>
        </div>

        <!-- Progress bar -->
        <div
          v-if="form.subtasks.length"
          class="h-1.5 w-full rounded-full bg-surface-gray-2 overflow-hidden"
        >
          <div
            class="h-full rounded-full bg-green-500 transition-all duration-300"
            :style="{ width: progressPct + '%' }"
          />
        </div>

        <!-- Subtask rows -->
        <div class="flex flex-col gap-1">
          <div
            v-for="(subtask, idx) in form.subtasks"
            :key="subtask.name || idx"
            class="flex items-center gap-2 rounded p-1 hover:bg-surface-gray-1 group"
          >
            <!-- Done checkbox -->
            <input
              type="checkbox"
              class="h-4 w-4 cursor-pointer accent-green-500 flex-shrink-0"
              :checked="subtask.status === 'Done'"
              @change="toggleSubtaskDone(idx)"
            />
            <!-- Title -->
            <input
              v-model="subtask.title"
              type="text"
              class="flex-1 bg-transparent text-sm text-ink-gray-8 outline-none placeholder:text-ink-gray-4"
              :placeholder="__('Subtask title')"
              :class="subtask.status === 'Done' && 'line-through text-ink-gray-4'"
                          />
            <!-- Status select -->
            <select
              v-model="subtask.status"
              class="text-xs rounded border border-outline-gray-2 bg-surface-white px-1.5 py-0.5 text-ink-gray-6 focus:outline-none"
                          >
              <option v-for="s in subtaskStatuses" :key="s" :value="s">
                {{ s }}
              </option>
            </select>
            <!-- Due date -->
            <DatePicker
              v-model="subtask.due_date"
              :format="dateFormat"
              :clearable="true"
              :placeholder="__('Date')"
              variant="outline"
              input-class="!text-xs !py-0.5 !px-1.5"
            />
            <!-- Remove -->
            <button
              class="invisible group-hover:visible text-ink-gray-4 hover:text-red-400"
              @click="removeSubtask(idx)"
            >
              <LucideX class="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        <!-- Add subtask -->
        <button
          class="flex items-center gap-1.5 text-sm text-ink-gray-5 hover:text-ink-gray-8 w-fit"
          @click="addSubtask"
        >
          <LucidePlus class="h-4 w-4" />
          {{ __("Add subtask") }}
        </button>
      </div>

      <!-- Created by footer -->
      <div class="flex items-center gap-3 pt-2 border-t border-outline-gray-1 text-xs text-ink-gray-4">
        <span>{{ __('Created by') }} <span class="font-medium text-ink-gray-6">{{ task.doc.owner }}</span></span>
        <span>{{ dayjs(task.doc.creation).format('DD MMM YYYY') }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { LayoutHeader } from "@/components";
import { __ } from "@/translation";
import Link from "@/components/frappe-ui/Link.vue";
import TagInput from "@/components/TagInput.vue";
import {
  Breadcrumbs,
  Button,
  call,
  createDocumentResource,
  DatePicker,
  FormControl,
  LoadingIndicator,
  TextEditor,
  toast,
  usePageMeta,
} from "frappe-ui";
import LucideClipboard from "~icons/lucide/clipboard";
import LucideExternalLink from "~icons/lucide/external-link";
import LucidePlus from "~icons/lucide/plus";
import LucideX from "~icons/lucide/x";
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { dayjs } from "frappe-ui";

const dateFormat = (window as any).date_format?.toUpperCase() || "DD-MM-YYYY";

function formatDate(d: string) {
  if (!d) return "";
  return dayjs(d).format(dateFormat);
}

const props = defineProps<{ taskId: string }>();
const router = useRouter();
const isSaving = ref(false);
const isDirty = ref(false);
const isFormLoaded = ref(false);

const allTags = ref<string[]>([]);

onMounted(async () => {
  try {
    const result = await call("helpdesk.helpdesk.doctype.hd_task.hd_task.get_all_task_tags");
    allTags.value = result ?? [];
  } catch {
    // non-critical
  }
});

const statusOptions = [
  { label: __("Backlog"), value: "Backlog" },
  { label: __("Todo"), value: "Todo" },
  { label: __("In Progress"), value: "In Progress" },
  { label: __("Done"), value: "Done" },
];

const subtaskStatuses = ["Backlog", "Todo", "In Progress", "Done"];

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
  user_tags: "",
  subtasks: [] as Subtask[],
  customer: "",
});

const task = createDocumentResource({
  doctype: "HD Task",
  name: props.taskId,
  auto: true,
});

watch(
  () => task.doc,
  (doc: any) => {
    if (!doc) return;
    isFormLoaded.value = false;
    form.title = doc.title ?? "";
    form.status = doc.status ?? "Backlog";
    form.priority = doc.priority ?? "";
    form.assigned_to = doc.assigned_to ?? "";
    form.due_date = doc.due_date ?? "";
    form.ticket = doc.ticket ?? "";
    form.team = doc.team ?? "";
    form.description = doc.description ?? "";
    form.user_tags = doc._user_tags ?? "";
    form.subtasks = (doc.subtasks ?? []).map((s: any) => ({
      name: s.name,
      title: s.title,
      status: s.status ?? "Backlog",
      due_date: s.due_date ?? "",
    }));
    form.customer = doc.customer ?? "";
    isDirty.value = false;
    nextTick(() => {
      isFormLoaded.value = true;
    });
  },
  { immediate: true }
);

// Customer isn't a user-facing field here — it's derived from the linked
// ticket so company-wide task matching (e.g. WhatsApp Business task counts)
// works even when a task is (re)linked to a ticket from this page, not just
// via the ticket sidebar's quick-add flow. Guarded by isFormLoaded so it
// doesn't run (and mark the form dirty) during initial hydration above.
watch(() => form.ticket, async (ticketId) => {
  if (!isFormLoaded.value) return;
  if (!ticketId) {
    form.customer = "";
    return;
  }
  try {
    const result = await call("frappe.client.get_value", {
      doctype: "HD Ticket",
      filters: ticketId,
      fieldname: "customer",
    });
    form.customer = result?.customer || "";
  } catch {
    form.customer = "";
  }
});

// Re-fetch when navigating between tasks without unmounting.
// immediate:true also acts as a safety-net for the initial load in case
// auto:true on createDocumentResource doesn't fire on SPA navigation.
watch(
  () => props.taskId,
  () => {
    isFormLoaded.value = false;
    task.reload();
  },
  { immediate: true }
);

// Mark dirty on any form change (after initial load)
watch(
  form,
  () => {
    if (isFormLoaded.value) isDirty.value = true;
  },
  { deep: true }
);

const doneCount = computed(
  () => form.subtasks.filter((s) => s.status === "Done").length
);

const progressPct = computed(() =>
  form.subtasks.length
    ? Math.round((doneCount.value / form.subtasks.length) * 100)
    : 0
);

function addSubtask() {
  form.subtasks.push({ title: "", status: "Backlog", due_date: "" });
  isDirty.value = true;
}

function removeSubtask(idx: number) {
  form.subtasks.splice(idx, 1);
  isDirty.value = true;
}

function toggleSubtaskDone(idx: number) {
  const sub = form.subtasks[idx];
  sub.status = sub.status === "Done" ? "Todo" : "Done";
  isDirty.value = true;
}

async function saveTask() {
  if (!form.title) return;
  isSaving.value = true;
  try {
    await call("helpdesk.helpdesk.doctype.hd_task.hd_task.save_task", {
      task_name: props.taskId,
      fields: JSON.stringify({
        title: form.title,
        status: form.status,
        priority: form.priority || null,
        assigned_to: form.assigned_to || null,
        due_date: form.due_date || null,
        ticket: form.ticket || null,
        team: form.team || null,
        description: form.description || null,
        _user_tags: form.user_tags || null,
        customer: form.customer || null,
      }),
      subtasks: JSON.stringify(
        form.subtasks.map((s) => ({
          name: s.name || null,
          title: s.title,
          status: s.status,
          due_date: s.due_date || null,
        }))
      ),
    });
    toast.success(__("Task saved"));
    isDirty.value = false;
    task.reload();
  } catch (e: any) {
    toast.error(e?.message || __("Failed to save task"));
  } finally {
    isSaving.value = false;
  }
}

function copyToClipboard() {
  const lines: string[] = [
    `Task: ${form.title}`,
    `Status: ${form.status}`,
  ];
  if (form.user_tags) lines.push(`Tags: ${form.user_tags}`);

  if (form.subtasks.length) {
    lines.push(
      `Progress: ${doneCount.value} of ${form.subtasks.length} subtasks completed`
    );
    lines.push("");
    lines.push("Subtasks:");
    for (const s of form.subtasks) {
      const check = s.status === "Done" ? "[x]" : "[ ]";
      const due = s.due_date ? ` - due ${formatDate(s.due_date)}` : "";
      lines.push(`${check} ${s.title} (${s.status})${due}`);
    }
  }

  navigator.clipboard
    .writeText(lines.join("\n"))
    .then(() => toast.success(__("Copied to clipboard")))
    .catch(() => toast.error(__("Could not copy to clipboard")));
}

usePageMeta(() => ({ title: form.title || props.taskId }));
</script>
