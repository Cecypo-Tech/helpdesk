<template>
  <div
    v-if="!phone"
    class="flex flex-1 flex-col items-center justify-center text-ink-gray-4"
  >
    <WhatsAppIcon class="mb-3 h-14 w-14" />
    <p class="text-sm">Select a conversation</p>
  </div>

  <div v-else class="flex flex-1 min-h-0 flex-col overflow-hidden">
    <!-- Header -->
    <div class="flex items-center gap-3 border-b border-outline-gray-2 bg-surface-gray-1 px-4 py-2.5">
      <button
        v-if="showBack"
        class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8"
        title="Back to conversations"
        @click="emit('back')"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="15 18 9 12 15 6"/>
        </svg>
      </button>
      <WhatsAppIcon class="h-4 w-4 shrink-0 text-green-600" />
      <div class="min-w-0 flex-1">
        <div class="truncate text-sm font-semibold text-ink-gray-9">
          {{ displayName || phone }}
        </div>
        <div class="flex items-center gap-1.5">
          <span class="text-[11px] font-medium text-ink-gray-6">+{{ phone }}</span>
        </div>
      </div>
      <router-link
        v-if="activeTicket.data?.ticket"
        :to="`/tickets/${activeTicket.data.ticket}`"
        class="shrink-0 text-xs font-medium text-blue-600 hover:underline"
      >
        View ticket #{{ activeTicket.data.ticket }} →
      </router-link>
    </div>

    <!-- Messages Area -->
    <div ref="messagesContainer" class="flex-1 overflow-y-auto px-5 py-4">
      <div v-if="messages.loading && !messages.data" class="flex justify-center py-10">
        <LoadingIndicator :scale="6" class="text-ink-gray-5" />
      </div>

      <div
        v-else-if="!messageList.length"
        class="flex flex-col items-center justify-center py-16 text-ink-gray-5"
      >
        <WhatsAppIcon class="mb-3 h-8 w-8 text-ink-gray-4" />
        <p class="text-sm">No WhatsApp messages</p>
      </div>

      <div v-else class="space-y-3">
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
            :reactions="reactionsMap[msg.message_id] || []"
            :replyToMessage="msg.is_reply && msg.reply_to_message_id ? messageByMsgId[msg.reply_to_message_id] || null : null"
            :isGroup="false"
            :mentionMap="{}"
            @reply="startReply"
            @react="sendReaction"
            @scrollToReply="scrollToMessage"
          />
        </template>
      </div>
    </div>

    <!-- Reply box -->
    <WhatsAppReplyBox
      v-if="activeTicket.data?.ticket"
      :ticketId="activeTicket.data.ticket"
      :replyTo="replyingTo"
      @sent="onMessageSent"
      @clearReply="replyingTo = null"
    />
    <div
      v-else-if="activeTicket.fetched"
      class="border-t border-outline-gray-2 px-4 py-3 text-center text-xs text-ink-gray-5"
    >
      No ticket found for this number yet.
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
  phone: string | null;
  displayName?: string;
  showBack?: boolean;
}>();

const emit = defineEmits<{ (e: "back"): void }>();

const { $socket } = globalStore();
const messagesContainer = ref<HTMLElement | null>(null);
const replyingTo = ref<Record<string, any> | null>(null);

const messages = createResource({
  url: "helpdesk.integrations.wa.get_whatsapp_messages",
  auto: false,
});

const activeTicket = createResource({
  url: "helpdesk.integrations.wa.get_active_whatsapp_ticket_for_phone",
  auto: false,
});

function reload() {
  if (!props.phone) return;
  messages.update({ params: { phone: props.phone } });
  messages.reload();
  activeTicket.update({ params: { phone: props.phone } });
  activeTicket.reload();
}

watch(() => props.phone, reload, { immediate: true });

const sendReactionResource = createResource({
  url: "helpdesk.integrations.wa.send_wa_reaction",
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send reaction");
  },
});

// All messages (including reactions)
const allMessages = computed<Record<string, any>[]>(() => messages.data || []);

// Main message list — reactions are displayed as badges on bubbles, not as standalone items
const messageList = computed(() =>
  allMessages.value.filter((m) => m.content_type !== "reaction")
);

// Map WhatsApp message_id → message object (for reply context lookups)
const messageByMsgId = computed(() => {
  const map: Record<string, Record<string, any>> = {};
  for (const m of allMessages.value) {
    if (m.message_id) map[m.message_id] = m;
  }
  return map;
});

// Map WhatsApp message_id → array of reactions
const reactionsMap = computed(() => {
  const map: Record<string, Array<{ emoji: string; type: string }>> = {};
  for (const m of allMessages.value) {
    if (m.content_type === "reaction" && m.reply_to_message_id && m.message) {
      if (!map[m.reply_to_message_id]) map[m.reply_to_message_id] = [];
      map[m.reply_to_message_id].push({ emoji: m.message, type: m.type });
    }
  }
  return map;
});

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

function onMessageSent() {
  replyingTo.value = null;
  reload();
}

function startReply(message: Record<string, any>) {
  replyingTo.value = message;
}

function sendReaction(emoji: string, targetMessageId: string) {
  if (!activeTicket.data?.ticket) return;
  if (!targetMessageId) {
    toast.error("Cannot react: message has no WhatsApp ID yet");
    return;
  }
  sendReactionResource.submit({
    ticket: activeTicket.data.ticket,
    target_message_id: targetMessageId,
    emoji,
  });
}

function scrollToMessage(messageId: string) {
  if (!messageId || !messagesContainer.value) return;
  const el = messagesContainer.value.querySelector(`[data-msg-id="${messageId}"]`) as HTMLElement | null;
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.style.transition = "background 0.2s";
  el.style.background = "rgba(99,178,115,0.25)";
  setTimeout(() => { el.style.background = ""; }, 1200);
}

function handleRealtimeMessage() {
  // frappe_whatsapp events only carry a ticket name, not a phone number, so
  // there's no cheap client-side way to filter to "does this belong to the
  // currently open phone conversation" — just re-resolve when one is open.
  if (props.phone) reload();
}

watch(messageList, () => scrollToBottom());

onMounted(() => {
  $socket.on("helpdesk:whatsapp-message", handleRealtimeMessage);
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:whatsapp-message", handleRealtimeMessage);
});

defineExpose({ scrollToBottom, refresh: reload });
</script>
