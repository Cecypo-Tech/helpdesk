<template>
  <SettingsLayoutBase
    :description="__('Configure the WhatsApp integration for your helpdesk.')"
  >
    <template #title>
      <div class="flex items-center gap-2">
        <h1 class="text-lg font-semibold text-ink-gray-8">
          {{ __("WhatsApp") }}
        </h1>
        <Badge
          v-if="isDirty"
          :label="__('Unsaved')"
          theme="orange"
          variant="subtle"
        />
      </div>
    </template>
    <template #header-actions>
      <Button
        :label="__('Save')"
        variant="solid"
        @click="save"
        :disabled="!isDirty"
        :loading="settings.save.loading"
      />
    </template>
    <template #content>
      <div
        v-if="settings.get.loading && !settings.doc"
        class="flex justify-center mt-12"
      >
        <LoadingIndicator class="w-4" />
      </div>
      <div v-else-if="settings.doc" class="space-y-8">
        <!-- General -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">
            {{ __("General") }}
          </div>
          <div class="mt-4 flex items-center justify-between">
            <div class="flex flex-col gap-1">
              <span class="text-sm font-medium text-ink-gray-8">{{
                __("Enabled")
              }}</span>
              <span class="text-p-sm text-ink-gray-6">{{
                __("Turn the WhatsApp integration on or off.")
              }}</span>
            </div>
            <Switch
              :modelValue="Boolean(settings.doc.enabled)"
              @update:modelValue="settings.doc.enabled = $event ? 1 : 0"
            />
          </div>
        </div>

        <hr />

        <!-- Notifications -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">
            {{ __("Notifications") }}
          </div>
          <div class="text-p-sm text-ink-gray-6 mt-1">
            {{
              __(
                "Control when agents receive bell notifications for incoming messages."
              )
            }}
          </div>
          <div class="mt-4 max-w-xs">
            <FormControl
              type="number"
              :label="__('Notification Quiet Period (minutes)')"
              v-model="settings.doc.notification_quiet_minutes"
              :description="
                __(
                  'Suppress the notification if an agent replied within this many minutes. Set to 0 to always notify.'
                )
              "
            />
          </div>
        </div>

        <hr />

        <!-- Ticket Defaults -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">
            {{ __("Ticket Defaults") }}
          </div>
          <div class="text-p-sm text-ink-gray-6 mt-1">
            {{
              __(
                "Applied when a new WhatsApp message creates a ticket automatically."
              )
            }}
          </div>
          <div class="mt-4 grid grid-cols-2 gap-4">
            <FormControl
              type="text"
              :label="__('Default Team')"
              v-model="settings.doc.default_team"
              :description="
                __(
                  'Team notified when a ticket has no assigned agent. Also used as a notification fallback.'
                )
              "
            />
            <FormControl
              type="text"
              :label="__('Default Ticket Type')"
              v-model="settings.doc.default_ticket_type"
            />
          </div>
        </div>

        <hr />

        <!-- Status Automation -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">
            {{ __("Status Automation") }}
          </div>
          <div class="text-p-sm text-ink-gray-6 mt-1">
            {{ __("Automatically update ticket status on WhatsApp activity.") }}
          </div>
          <div class="mt-4 grid grid-cols-2 gap-4">
            <FormControl
              type="text"
              :label="__('Status on Customer Reply')"
              v-model="settings.doc.customer_reply_status"
              :description="
                __('Set when an incoming WhatsApp message arrives.')
              "
            />
            <FormControl
              type="text"
              :label="__('Status on Agent Reply')"
              v-model="settings.doc.agent_reply_status"
              :description="__('Set when an agent sends a WhatsApp reply.')"
            />
          </div>
        </div>
      </div>
    </template>
  </SettingsLayoutBase>
</template>

<script setup lang="ts">
import {
  Badge,
  Button,
  createDocumentResource,
  FormControl,
  LoadingIndicator,
  Switch,
  toast,
} from "frappe-ui";
import { computed, watch } from "vue";
import { __ } from "@/translation";
import { disableSettingModalOutsideClick } from "../settingsModal";
import SettingsLayoutBase from "@/components/layouts/SettingsLayoutBase.vue";

const settings = createDocumentResource({
  doctype: "WhatsApp Helpdesk Settings",
  name: "WhatsApp Helpdesk Settings",
  auto: true,
});

const isDirty = computed(() => {
  if (!settings.doc || !settings.originalDoc) return false;
  return JSON.stringify(settings.doc) !== JSON.stringify(settings.originalDoc);
});

watch(isDirty, (val) => {
  disableSettingModalOutsideClick.value = val;
});

async function save() {
  try {
    await settings.save.submit();
    toast.success(__("WhatsApp settings saved"));
  } catch (e: any) {
    toast.error(e?.messages?.[0] || __("Failed to save settings"));
  }
}
</script>
