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
        <WhatsAppIcon class="mb-3 h-10 w-10 text-ink-gray-4" />
        <p class="text-sm">No messages yet</p>
      </div>

      <!-- Messages -->
      <div v-else class="space-y-3">
        <!-- Group badge -->
        <div v-if="tabInfo.data?.is_group && tabInfo.data?.group_name" class="flex justify-center">
          <span class="rounded-full bg-surface-gray-2 px-3 py-1 text-[11px] text-ink-gray-5">
            {{ tabInfo.data.group_name }}
          </span>
        </div>

        <template v-for="(group, dateKey) in groupedMessages" :key="dateKey">
          <div class="my-4 flex items-center gap-3">
            <div class="flex-1 border-t border-outline-gray-2" />
            <span class="text-[11px] font-medium text-ink-gray-5">{{ dateKey }}</span>
            <div class="flex-1 border-t border-outline-gray-2" />
          </div>
          <WhatsAppBubble
            v-for="msg in group"
            :key="msg.name"
            :data-msg-id="msg.message_id"
            :message="msg"
            :reactions="[]"
            :replyToMessage="null"
          />
        </template>
      </div>
    </div>

    <!-- Bottom area -->
    <div v-if="tabInfo.data?.has_baileys">
      <!-- Assign-to-self banner -->
      <div
        v-if="!tabInfo.data.is_assigned"
        class="flex items-center justify-between border-t border-outline-gray-2 bg-surface-gray-1 px-4 py-2"
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

      <BaileysReplyBox
        :ticketId="ticketId"
        @sent="onMessageSent"
      />
    </div>

    <!-- Not a Baileys ticket -->
    <div
      v-else-if="tabInfo.fetched && tabInfo.data && !tabInfo.data.has_baileys"
      class="border-t border-outline-gray-2 px-4 py-3 text-center text-xs text-ink-gray-5"
    >
      This ticket is not linked to a Baileys chat.
    </div>
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator, toast } from "frappe-ui";
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { globalStore } from "@/stores/globalStore";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";
import WhatsAppBubble from "./WhatsAppBubble.vue";
import BaileysReplyBox from "./BaileysReplyBox.vue";

const props = defineProps<{
  ticketId: string;
}>();

const { $socket } = globalStore();
const messagesContainer = ref<HTMLElement | null>(null);
const pickingUp = ref(false);

const messages = createResource({
  url: "helpdesk.integrations.baileys.get_baileys_messages",
  params: { ticket: props.ticketId },
  auto: true,
});

const tabInfo = createResource({
  url: "helpdesk.integrations.baileys.get_ticket_baileys_info",
  params: { ticket: props.ticketId },
  auto: true,
});

const markReadResource = createResource({
  url: "helpdesk.integrations.baileys.mark_baileys_messages_read",
});

const pickUpResource = createResource({
  url: "helpdesk.integrations.baileys.pickup_baileys_ticket",
  onSuccess() {
    pickingUp.value = false;
    tabInfo.reload();
    toast.success("Assigned to you");
  },
  onError(e: any) {
    pickingUp.value = false;
    toast.error(e?.messages?.[0] || "Could not assign ticket");
  },
});

const messageList = computed<Record<string, any>[]>(() => messages.data || []);

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
  tabInfo.reload();
  scrollToBottom();
}

function markAsRead() {
  markReadResource.submit({ ticket: props.ticketId });
}

function handleRealtimeMessage(data: { ticket: string; is_incoming: boolean }) {
  if (String(data.ticket) === String(props.ticketId)) {
    messages.reload();
    tabInfo.reload();
    if (data.is_incoming) scrollToBottom();
    markAsRead();
  }
}

watch(messageList, () => {
  scrollToBottom();
});

onMounted(() => {
  $socket.on("helpdesk:whatsapp-message", handleRealtimeMessage);
  scrollToBottom();
  markAsRead();
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:whatsapp-message", handleRealtimeMessage);
});
</script>
