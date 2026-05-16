<template>
  <div v-if="!contact.loading" class="my-4">
    <div class="flex items-center justify-start gap-5">
      <Avatar :label="contact.data.name" :image="contactImage" size="2xl" />
      <div class="flex flex-col gap-1.5 min-w-0">
        <Tooltip :text="contact.data.name || contact.data.email_id">
          <div class="flex gap-2.5 items-center">
            <p class="text-ink-gray-8 font-medium text-xl max-w-[170px] truncate">
              {{ contact.data.name || contact.data.email_id }}
            </p>
            <ExternalLinkIcon
              class="size-4 text-ink-gray-6 cursor-pointer shrink-0"
              @click="openContact(contact.data.name)"
            />
          </div>
        </Tooltip>
        <!-- Phone -->
        <div
          v-if="contact.data.mobile_no || contact.data.phone"
          class="flex items-center gap-1.5 text-sm text-ink-gray-6"
        >
          <PhoneIcon class="size-3.5 shrink-0" />
          <span class="truncate">{{ contact.data.mobile_no || contact.data.phone }}</span>
        </div>
        <!-- Company -->
        <div
          v-if="contact.data.company_name"
          class="flex items-center gap-1.5 text-sm text-ink-gray-6"
        >
          <LucideBuilding2 class="size-3.5 shrink-0" />
          <span class="truncate">{{ contact.data.company_name }}</span>
        </div>
        <div class="flex gap-1.5 mt-0.5">
          <Tooltip :text="contact.data.email_id">
            <!-- Email Button -->
            <Button size="sm" @click="toggleEmailBox()">
              <template #icon>
                <EmailIcon class="size-4" />
              </template>
            </Button>
            <!-- Call Button -->
            <Button size="sm" v-if="isCallingEnabled" @click="callContact">
              <template #icon>
                <PhoneIcon class="size-4" />
              </template>
            </Button>
          </Tooltip>
        </div>
      </div>
    </div>

    <!-- Company notes -->
    <div v-if="ticket?.value?.doc?.customer && !notesUnavailable" class="mt-3">
      <div class="flex items-center justify-between mb-1">
        <span class="text-xs font-medium text-ink-gray-5 uppercase tracking-wide">Company Notes</span>
        <span class="text-xs text-ink-gray-4 transition-opacity" :class="saveStatus ? 'opacity-100' : 'opacity-0'">Saved</span>
      </div>
      <textarea
        v-model="notesValue"
        @input="debouncedSave"
        rows="4"
        placeholder="Remote access, account info, preferences…"
        class="w-full resize-none rounded border border-outline-gray-2 bg-surface-gray-1 px-2.5 py-2 text-sm text-ink-gray-8 placeholder-ink-gray-4 focus:border-outline-gray-4 focus:outline-none focus:ring-0"
      />
    </div>

    <SetContactPhoneModal
      v-model="showPhoneModal"
      :name="contact.data.name"
      @onUpdate="contact.reload"
    />
  </div>
</template>

<script setup lang="ts">
import { toggleEmailBox } from "@/pages/ticket/modalStates";
import { useTelephonyStore } from "@/stores/telephony";
import { useUserStore } from "@/stores/user";
import { TicketContactSymbol, TicketSymbol } from "@/types";
import { useDebounceFn } from "@vueuse/core";
import { Avatar, Button, Tooltip, createResource } from "frappe-ui";
import { storeToRefs } from "pinia";
import { computed, inject, ref, watch } from "vue";
import { ExternalLinkIcon } from "../icons";
import EmailIcon from "../icons/EmailIcon.vue";
import PhoneIcon from "../icons/PhoneIcon.vue";
import SetContactPhoneModal from "../ticket/SetContactPhoneModal.vue";

const telephonyStore = useTelephonyStore();
const { getUser } = useUserStore();
const { isCallingEnabled } = storeToRefs(telephonyStore);
const showPhoneModal = ref(false);

const ticket = inject(TicketSymbol);
const contact = inject(TicketContactSymbol);

const contactImage = computed(() => {
  return (
    contact.value?.data?.image ||
    getUser(contact.value?.data?.email_id)?.user_image ||
    null
  );
});

function openContact(name: string) {
  let url = window.location.origin + "/app/contact/" + name;
  window.open(url, "_blank");
}

const callContact = () => {
  if (!contact.value.data.mobile_no && !contact.value.data.phone) {
    showPhoneModal.value = true;
    return;
  }
  telephonyStore.makeCall({
    number: contact.value.data.mobile_no || contact.value.data.phone,
    doctype: "HD Ticket",
    docname: ticket.value.name,
  });
};

// --- Company notes ---
const notesValue = ref("");
const saveStatus = ref(false);
const notesUnavailable = ref(false);
let saveTimer: ReturnType<typeof setTimeout> | null = null;

const customer = computed(() => ticket?.value?.doc?.customer);

const getNotesResource = createResource({
  url: "frappe.client.get_value",
  onSuccess(data: { helpdesk_notes?: string }) {
    notesValue.value = data?.helpdesk_notes || "";
  },
  onError() {
    notesUnavailable.value = true;
  },
});

const setNotesResource = createResource({
  url: "frappe.client.set_value",
  onSuccess() {
    saveStatus.value = true;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => (saveStatus.value = false), 2000);
  },
  onError() {},
});

watch(
  customer,
  (val) => {
    if (val) {
      getNotesResource.submit({
        doctype: "Customer",
        filters: val,
        fieldname: "helpdesk_notes",
      });
    } else {
      notesValue.value = "";
    }
  },
  { immediate: true }
);

const debouncedSave = useDebounceFn(() => {
  if (!customer.value) return;
  setNotesResource.submit({
    doctype: "Customer",
    name: customer.value,
    fieldname: "helpdesk_notes",
    value: notesValue.value,
  });
}, 800);
</script>

<style scoped></style>
