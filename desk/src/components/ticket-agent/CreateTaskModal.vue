<template>
  <Dialog
    v-model="show"
    :options="{ title: __('Create Task'), size: 'lg' }"
  >
    <template #body-content>
      <div class="flex flex-col gap-4">
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
            autofocus
          />
        </div>

        <!-- Status + Priority -->
        <div class="grid grid-cols-2 gap-3">
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

        <!-- Assigned To + Due Date -->
        <div class="grid grid-cols-2 gap-3">
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

        <!-- Linked ticket (read-only display) -->
        <div class="flex flex-col gap-1.5">
          <label class="block text-sm font-medium text-ink-gray-7">
            {{ __("Linked Ticket") }}
          </label>
          <FormControl
            :model-value="ticketId"
            type="text"
            :disabled="true"
          />
        </div>
      </div>
    </template>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button
          :label="__('Cancel')"
          theme="gray"
          variant="outline"
          @click="show = false"
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
    </template>
  </Dialog>
</template>

<script setup lang="ts">
import Link from "@/components/frappe-ui/Link.vue";
import { TicketSymbol } from "@/types";
import { __ } from "@/translation";
import { Button, call, Dialog, FormControl, toast } from "frappe-ui";
import { inject, reactive, ref } from "vue";
import { useRouter } from "vue-router";

const show = defineModel<boolean>();
const router = useRouter();
const isSaving = ref(false);

const ticket = inject(TicketSymbol);
const ticketId = ticket?.value?.doc?.name ?? "";

const statusOptions = [
  { label: __("Backlog"), value: "Backlog" },
  { label: __("Todo"), value: "Todo" },
  { label: __("In Progress"), value: "In Progress" },
  { label: __("Done"), value: "Done" },
];

const form = reactive({
  title: "",
  status: "Todo",
  priority: "",
  assigned_to: "",
  due_date: "",
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
        ticket: ticketId || null,
      },
    });
    toast.success(__("Task created"));
    show.value = false;
    router.push({ name: "TaskAgent", params: { taskId: doc.name } });
  } catch (e) {
    toast.error(__("Failed to create task"));
  } finally {
    isSaving.value = false;
  }
}
</script>
