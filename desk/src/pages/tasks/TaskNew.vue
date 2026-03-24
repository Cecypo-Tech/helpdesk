<template>
  <div class="flex flex-col overflow-y-auto">
    <LayoutHeader>
      <template #left-header>
        <Breadcrumbs
          :items="[
            { label: __('Tasks'), route: { name: 'TasksAgent' } },
            { label: __('New Task'), route: { name: 'TaskAgentNew' } },
          ]"
        />
      </template>
    </LayoutHeader>
    <div
      class="flex flex-col gap-5 py-6 h-full flex-1 self-center overflow-auto mx-auto w-full max-w-3xl px-5"
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
          <FormControl v-model="form.due_date" type="date" />
        </div>
      </div>

      <!-- Ticket (optional) -->
      <div class="flex flex-col gap-1.5">
        <label class="block text-sm font-medium text-ink-gray-7">
          {{ __("Linked Ticket") }}
        </label>
        <Link
          v-model="form.ticket"
          doctype="HD Ticket"
          :placeholder="__('Link to a ticket (optional)')"
          class="form-control"
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
        />
      </div>

      <!-- Actions -->
      <div class="flex justify-end gap-2 pb-6">
        <Button
          :label="__('Cancel')"
          theme="gray"
          variant="outline"
          @click="$router.push({ name: 'TasksAgent' })"
        />
        <Button
          :label="__('Create Task')"
          theme="gray"
          variant="solid"
          :loading="isSaving"
          :disabled="!form.title"
          @click="createTask"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { LayoutHeader } from "@/components";
import { __ } from "@/translation";
import Link from "@/components/frappe-ui/Link.vue";
import { Breadcrumbs, Button, call, FormControl, TextEditor, toast, usePageMeta } from "frappe-ui";
import { reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

const router = useRouter();
const route = useRoute();
const isSaving = ref(false);

const statusOptions = [
  { label: __("Backlog"), value: "Backlog" },
  { label: __("Todo"), value: "Todo" },
  { label: __("In Progress"), value: "In Progress" },
  { label: __("Done"), value: "Done" },
];

const validStatuses = ["Backlog", "Todo", "In Progress", "Done"];
const initialStatus = validStatuses.includes(route.query.status as string)
  ? (route.query.status as string)
  : "Backlog";

const form = reactive({
  title: "",
  status: initialStatus,
  priority: "",
  assigned_to: "",
  due_date: "",
  ticket: "",
  description: "",
});

async function createTask() {
  if (!form.title) return;
  isSaving.value = true;
  try {
    const doc = await call("frappe.client.insert", {
      doc: {
        doctype: "HD Task",
        title: form.title,
        status: form.status,
        priority: form.priority || null,
        assigned_to: form.assigned_to || null,
        due_date: form.due_date || null,
        ticket: form.ticket || null,
        description: form.description || null,
      },
    });
    toast.success(__("Task created"));
    router.push({ name: "TaskAgent", params: { taskId: doc.name } });
  } catch (e) {
    toast.error(__("Failed to create task"));
  } finally {
    isSaving.value = false;
  }
}

usePageMeta(() => ({ title: __("New Task") }));
</script>
