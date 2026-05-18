<template>
  <SettingsLayoutBase :description="__('Configure the Baileys WhatsApp gateway.')">
    <template #title>
      <div class="flex items-center gap-2">
        <h1 class="text-lg font-semibold text-ink-gray-8">{{ __("Baileys Gateway") }}</h1>
        <Badge v-if="isDirty" :label="__('Unsaved')" theme="orange" variant="subtle" />
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
      <div v-if="settings.get.loading && !settings.doc" class="flex justify-center mt-12">
        <LoadingIndicator class="w-4" />
      </div>
      <div v-else-if="settings.doc" class="space-y-8">

        <!-- Connection -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">{{ __("Connection") }}</div>
          <div class="mt-4 flex items-center justify-between">
            <div class="flex flex-col gap-1">
              <span class="text-sm font-medium text-ink-gray-8">{{ __("Enabled") }}</span>
              <span class="text-p-sm text-ink-gray-6">{{ __("Activate the Baileys WhatsApp gateway.") }}</span>
            </div>
            <Switch
              :modelValue="Boolean(settings.doc.enabled)"
              @update:modelValue="settings.doc.enabled = $event ? 1 : 0"
            />
          </div>
          <div class="mt-4 grid grid-cols-2 gap-4">
            <FormControl
              type="text"
              :label="__('Gateway URL')"
              v-model="settings.doc.gateway_url"
              :description="__('e.g. http://localhost:3001')"
            />
            <FormControl
              type="password"
              :label="__('API Key')"
              v-model="settings.doc.api_key"
              :description="__('Shared secret (X-API-Key header).')"
            />
          </div>
          <div class="mt-4 max-w-xs">
            <FormControl type="text" :label="__('Session Name')" v-model="settings.doc.session_name" />
          </div>
        </div>

        <hr />

        <!-- Connection Status -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">{{ __("Connection Status") }}</div>
          <div class="mt-3 flex items-center gap-3">
            <Button :label="__('Check Status')" @click="checkStatus" :loading="statusResource.loading" />
            <div v-if="statusResult" class="flex items-center gap-2">
              <span
                class="h-2 w-2 rounded-full"
                :class="statusResult.connected ? 'bg-green-500' : 'bg-red-400'"
              />
              <span class="text-sm text-ink-gray-7">
                {{ statusResult.connected ? __("Connected") : __("Disconnected") }}
                <span v-if="statusResult.connected && statusResult.phone" class="ml-1 text-ink-gray-5">
                  (+{{ statusResult.phone }})
                </span>
              </span>
            </div>
          </div>
        </div>

        <hr />

        <!-- Chat Behaviour -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">{{ __("Chat Behaviour") }}</div>
          <div class="mt-4 flex items-center justify-between">
            <div class="flex flex-col gap-1">
              <span class="text-sm font-medium text-ink-gray-8">{{ __("Append Agent Initials") }}</span>
              <span class="text-p-sm text-ink-gray-6">
                {{ __("Adds ^XX (agent initials) to the end of each outgoing message.") }}
              </span>
            </div>
            <Switch
              :modelValue="Boolean(settings.doc.append_agent_initials)"
              @update:modelValue="settings.doc.append_agent_initials = $event ? 1 : 0"
            />
          </div>
          <div class="mt-4 max-w-xs">
            <FormControl
              type="number"
              :label="__('Notification Quiet Period (minutes)')"
              v-model="settings.doc.notification_quiet_minutes"
              :description="__('Suppress bell if an agent replied within this many minutes. 0 = always notify.')"
            />
          </div>
        </div>

        <hr />

        <!-- Ticket Defaults -->
        <div>
          <div class="text-base font-semibold text-ink-gray-8">{{ __("Ticket Defaults") }}</div>
          <div class="mt-4 grid grid-cols-2 gap-4">
            <FormControl type="text" :label="__('Default Team')" v-model="settings.doc.default_team" />
            <FormControl
              type="text"
              :label="__('Default Ticket Type')"
              v-model="settings.doc.default_ticket_type"
            />
          </div>
          <div class="mt-4 max-w-xs">
            <FormControl
              type="text"
              :label="__('Placeholder Email Domain')"
              v-model="settings.doc.placeholder_email_domain"
              :description="__('Domain for synthetic addresses on group and unknown-contact tickets.')"
            />
          </div>
        </div>

      </div>
    </template>
  </SettingsLayoutBase>
</template>

<script setup lang="ts">
import {
  Badge, Button, createDocumentResource, createResource,
  FormControl, LoadingIndicator, Switch, toast,
} from "frappe-ui";
import { computed, ref, watch } from "vue";
import { __ } from "@/translation";
import { disableSettingModalOutsideClick } from "../settingsModal";
import SettingsLayoutBase from "@/components/layouts/SettingsLayoutBase.vue";

const settings = createDocumentResource({
  doctype: "Baileys Gateway Settings",
  name: "Baileys Gateway Settings",
  auto: true,
});

const statusResult = ref<{ connected: boolean; phone?: string | null } | null>(null);

const statusResource = createResource({
  url: "helpdesk.integrations.baileys.get_connected_phone",
  onSuccess(data: any) { statusResult.value = data; },
  onError() { statusResult.value = { connected: false }; },
});

function checkStatus() {
  statusResult.value = null;
  statusResource.fetch();
}

const isDirty = computed(() => {
  if (!settings.doc || !settings.originalDoc) return false;
  return JSON.stringify(settings.doc) !== JSON.stringify(settings.originalDoc);
});

watch(isDirty, (val) => { disableSettingModalOutsideClick.value = val; });

async function save() {
  try {
    await settings.save.submit();
    toast.success(__("Baileys Gateway settings saved"));
  } catch (e: any) {
    toast.error(e?.messages?.[0] || __("Failed to save settings"));
  }
}
</script>
