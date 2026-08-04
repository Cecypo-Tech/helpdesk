<template>
  <Dialog v-model="show" :options="{ size: '3xl' }">
    <template #body>
      <div class="p-4">
        <div class="mb-3 text-lg font-semibold text-ink-gray-9">
          {{ __("Send a WhatsApp Template") }}
        </div>

        <div v-if="templates.loading" class="py-6 text-center text-sm text-ink-gray-5">
          {{ __("Loading templates…") }}
        </div>

        <div
          v-else-if="!(templates.data || []).length"
          class="py-6 text-center text-sm text-ink-gray-5"
        >
          {{ __("No approved templates available.") }}
        </div>

        <div v-else class="flex flex-col gap-3">
          <div class="max-h-72 overflow-y-auto rounded-lg border border-outline-gray-2">
            <table class="w-full table-fixed text-left text-sm">
              <thead class="sticky top-0 z-10 bg-surface-gray-2 text-xs text-ink-gray-5">
                <tr>
                  <th class="w-1/3 px-3 py-2 font-medium">{{ __("Template") }}</th>
                  <th class="px-3 py-2 font-medium">{{ __("Message") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="t in templates.data"
                  :key="t.name"
                  class="cursor-pointer border-t border-outline-gray-2 hover:bg-surface-gray-1"
                  :class="selected === t.name ? 'bg-surface-gray-2' : ''"
                  @click="selectTemplate(t.name)"
                >
                  <td class="px-3 py-2 align-top">
                    <div class="break-words font-medium text-ink-gray-8">
                      {{ t.template_name || t.name }}
                    </div>
                    <div v-if="t.language_code" class="text-xs text-ink-gray-5">
                      {{ t.language_code }}
                    </div>
                  </td>
                  <td class="whitespace-pre-wrap break-words px-3 py-2 align-top text-ink-gray-7">
                    {{ t.template }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div
            v-if="selected"
            class="rounded-lg border border-outline-gray-2 bg-surface-gray-1 p-3 text-sm text-ink-gray-8"
          >
            <div class="mb-1 text-xs font-medium text-ink-gray-5">
              {{ __("Preview — exactly what will be sent") }}
            </div>
            <div v-if="previewLoading" class="text-ink-gray-5">{{ __("Rendering…") }}</div>
            <div v-else-if="previewError" class="text-red-600">{{ previewError }}</div>
            <div v-else class="whitespace-pre-wrap">{{ previewText }}</div>
          </div>

          <div class="flex justify-end gap-2">
            <Button :label="__('Cancel')" @click="show = false" />
            <Button
              variant="solid"
              theme="green"
              :label="__('Send')"
              :loading="sending"
              :disabled="!selected || previewLoading || !!previewError"
              @click="sendTemplate"
            />
          </div>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup lang="ts">
import { Button, Dialog, call, createResource, toast } from "frappe-ui";
import { ref } from "vue";

const props = defineProps<{ ticketId: string }>();
const emit = defineEmits<{ (e: "sent"): void }>();

const show = defineModel<boolean>();

const selected = ref("");
const previewText = ref("");
const previewError = ref("");
const previewLoading = ref(false);
const sending = ref(false);

const templates = createResource({
  url: "helpdesk.integrations.wa.get_outgoing_templates",
  auto: true,
});

function selectTemplate(name: string) {
  if (sending.value) return;
  selected.value = name;
  loadPreview();
}

async function loadPreview() {
  previewText.value = "";
  previewError.value = "";
  if (!selected.value) return;
  previewLoading.value = true;
  try {
    const res = await call("helpdesk.integrations.wa.preview_template_for_ticket", {
      ticket: props.ticketId,
      template_name: selected.value,
    });
    previewText.value = res?.message || "";
  } catch (e: any) {
    previewError.value = e?.messages?.[0] || __("Could not render this template.");
  } finally {
    previewLoading.value = false;
  }
}

async function sendTemplate() {
  if (!selected.value || sending.value) return;
  sending.value = true;
  try {
    await call("helpdesk.integrations.wa.send_template_to_ticket", {
      ticket: props.ticketId,
      template_name: selected.value,
    });
    toast.success(__("Template sent"));
    selected.value = "";
    previewText.value = "";
    show.value = false;
    emit("sent");
  } catch (e: any) {
    toast.error(e?.messages?.[0] || __("Failed to send template"));
  } finally {
    sending.value = false;
  }
}
</script>
