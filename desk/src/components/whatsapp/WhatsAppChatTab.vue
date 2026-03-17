<template>
  <div class="flex flex-1 flex-col overflow-hidden">
    <!-- Messages Area -->
    <div ref="messagesContainer" class="flex-1 overflow-y-auto px-5 py-4">
      <!-- Loading -->
      <div v-if="messages.loading && !messages.data" class="flex justify-center py-10">
        <LoadingIndicator :scale="6" class="text-ink-gray-5" />
      </div>

      <!-- Empty state -->
      <div
        v-else-if="!messageList.length"
        class="flex flex-col items-center justify-center py-16 text-ink-gray-5"
      >
        <WhatsAppIcon class="h-10 w-10 mb-3 text-ink-gray-4" />
        <p class="text-sm">No WhatsApp messages</p>
      </div>

      <!-- Messages -->
      <div v-else class="space-y-3">
        <!-- Date separators and messages -->
        <template v-for="(group, dateKey) in groupedMessages" :key="dateKey">
          <div class="flex items-center gap-3 my-4">
            <div class="flex-1 border-t border-outline-gray-2" />
            <span class="text-[11px] text-ink-gray-5 font-medium">{{ dateKey }}</span>
            <div class="flex-1 border-t border-outline-gray-2" />
          </div>
          <WhatsAppBubble
            v-for="msg in group"
            :key="msg.name"
            :message="msg"
          />
        </template>
      </div>
    </div>

    <!-- Bottom Area -->
    <div v-if="ticketInfo.data && ticketInfo.data.has_whatsapp">
      <!-- Assign-to-self banner (non-blocking) -->
      <div
        v-if="!ticketInfo.data.is_assigned"
        class="flex items-center justify-between border-t border-outline-gray-2 px-4 py-2 bg-surface-gray-1"
      >
        <span class="text-xs text-ink-gray-5">Not assigned to you</span>
        <button
          :disabled="pickingUp"
          class="text-xs font-medium text-blue-600 hover:underline disabled:opacity-50"
          @click="pickUp"
        >
          {{ pickingUp ? "Assigning..." : "Assign to me" }}
        </button>
      </div>

      <!-- Reply box or window expired -->
      <WhatsAppReplyBox
        v-if="ticketInfo.data.reply_window_open"
        :ticketId="ticketId"
        @sent="onMessageSent"
      />
      <!-- Window expired — template sender -->
      <div v-else class="border-t border-outline-gray-2 px-4 py-3">
        <div class="mb-2 flex items-center gap-1.5 text-xs text-ink-gray-5">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="shrink-0"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          24-hour reply window has expired.
          <span v-if="!ticketInfo.data.allow_template_outside_window" class="text-ink-gray-4">Enable template messages in WhatsApp settings to reopen.</span>
        </div>
        <div v-if="ticketInfo.data.allow_template_outside_window" class="flex gap-2">
          <select
            v-model="selectedTemplate"
            class="flex-1 rounded-lg border border-outline-gray-3 bg-surface-white px-3 py-2 text-sm text-ink-gray-8 focus:border-outline-gray-4 focus:outline-none"
            :disabled="sendingTemplate || templates.loading"
          >
            <option value="">{{ templates.loading ? 'Loading templates…' : 'Select a template…' }}</option>
            <option v-for="t in templates.data || []" :key="t.name" :value="t.name">
              {{ t.template_name || t.name }}
            </option>
          </select>
          <button
            :disabled="!selectedTemplate || sendingTemplate"
            class="flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-green-600 px-3 text-xs font-medium text-white hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50"
            @click="sendTemplate"
          >
            <svg v-if="!sendingTemplate" width="13" height="13" viewBox="0 0 16 16" fill="none"><path d="M14.67 1.33L7.33 8.67M14.67 1.33l-4.34 13.34-3-6-6-3 13.34-4.34z" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"/></svg>
            <svg v-else class="animate-spin" width="13" height="13" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="2" stroke-dasharray="28" stroke-dashoffset="8" stroke-linecap="round"/></svg>
            Send
          </button>
        </div>
      </div>
    </div>

    <!-- No phone number linked -->
    <div
      v-else-if="ticketInfo.fetched && ticketInfo.data && !ticketInfo.data.has_whatsapp"
      class="border-t border-outline-gray-2 px-4 py-3 text-center text-xs text-ink-gray-5"
    >
      No phone number linked to this ticket's contact.
    </div>
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator, toast } from "frappe-ui";
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { globalStore } from "@/stores/globalStore";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";
import WhatsAppBubble from "./WhatsAppBubble.vue";
import WhatsAppReplyBox from "./WhatsAppReplyBox.vue";

const props = defineProps<{
  ticketId: string;
}>();

const { $socket } = globalStore();
const messagesContainer = ref<HTMLElement | null>(null);
const pickingUp = ref(false);
const selectedTemplate = ref("");
const sendingTemplate = ref(false);

const messages = createResource({
  url: "helpdesk.integrations.whatsapp.get_whatsapp_messages",
  params: { ticket: props.ticketId },
  auto: true,
});

const ticketInfo = createResource({
  url: "helpdesk.integrations.whatsapp.get_ticket_whatsapp_info",
  params: { ticket: props.ticketId },
  auto: true,
});

const markReadResource = createResource({
  url: "helpdesk.integrations.whatsapp.mark_messages_read",
});

const templates = createResource({
  url: "helpdesk.integrations.whatsapp.get_outgoing_templates",
  auto: true,
});

const sendTemplateResource = createResource({
  url: "helpdesk.integrations.whatsapp.send_template_to_ticket",
  onSuccess() {
    sendingTemplate.value = false;
    selectedTemplate.value = "";
    messages.reload();
    ticketInfo.reload();
    scrollToBottom();
    toast.success("Template sent");
  },
  onError(e: any) {
    sendingTemplate.value = false;
    toast.error(e?.messages?.[0] || "Failed to send template");
  },
});

function markAsRead() {
  markReadResource.submit({ ticket: props.ticketId });
}

const pickUpResource = createResource({
  url: "helpdesk.integrations.whatsapp.pickup_ticket",
  onSuccess() {
    pickingUp.value = false;
    ticketInfo.reload();
    toast.success("Assigned to you");
  },
  onError(e: any) {
    pickingUp.value = false;
    toast.error(e?.messages?.[0] || "Could not assign ticket");
  },
});

const messageList = computed(() => messages.data || []);

const groupedMessages = computed(() => {
  const groups: Record<string, any[]> = {};
  for (const msg of messageList.value) {
    const date = new Date(msg.creation).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
    if (!groups[date]) groups[date] = [];
    groups[date].push(msg);
  }
  return groups;
});

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
    }
  });
}

function pickUp() {
  pickingUp.value = true;
  pickUpResource.submit({ ticket: props.ticketId });
}

function onMessageSent() {
  messages.reload();
  ticketInfo.reload();
  scrollToBottom();
}

function sendTemplate() {
  if (!selectedTemplate.value || sendingTemplate.value) return;
  sendingTemplate.value = true;
  sendTemplateResource.submit({
    ticket: props.ticketId,
    template_name: selectedTemplate.value,
  });
}

function handleRealtimeMessage(data: { ticket: string; is_incoming: boolean }) {
  if (String(data.ticket) === String(props.ticketId)) {
    messages.reload();
    ticketInfo.reload();
    scrollToBottom();
    markAsRead();
  }
}

function handleStatusUpdate(data: { ticket: string; message_name: string; status: string }) {
  if (String(data.ticket) !== String(props.ticketId)) return;
  const list = messages.data;
  if (!list) return;
  const msg = list.find((m: any) => m.name === data.message_name);
  if (msg) msg.status = data.status;
}

watch(messageList, () => {
  scrollToBottom();
});

onMounted(() => {
  $socket.on("helpdesk:whatsapp-message", handleRealtimeMessage);
  $socket.on("helpdesk:whatsapp-status-update", handleStatusUpdate);
  scrollToBottom();
  // Mark all visible incoming messages as read when the tab opens
  markAsRead();
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:whatsapp-message", handleRealtimeMessage);
  $socket.off("helpdesk:whatsapp-status-update", handleStatusUpdate);
});
</script>
