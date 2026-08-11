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
        <WhatsAppIcon class="mb-3 h-8 w-8 text-ink-gray-4" />
        <p class="text-sm">No WhatsApp messages</p>
      </div>

      <!-- Messages -->
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
            :isGroup="ticketInfo.data?.is_group ?? false"
            :mentionMap="mentionMap"
            @reply="startReply"
            @react="sendReaction"
            @scrollToReply="scrollToMessage"
            @edit="applyEdit"
          />
        </template>
      </div>
    </div>

    <!-- Bottom Area -->
    <div v-if="ticketInfo.data && ticketInfo.data.has_whatsapp">
      <!-- Assign-to-self banner (non-blocking) -->
      <div
        v-if="!ticketInfo.data.is_assigned"
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

      <!-- Reply box. Stays mounted past the 24-hour window: the free-form
           controls disable themselves and the template picker stays live. -->
      <WhatsAppReplyBox
        :ticketId="ticketId"
        :replyTo="replyingTo"
        :replyWindowOpen="ticketInfo.data.reply_window_open"
        @sent="onMessageSent"
        @delivered="onMessageDelivered"
        @clearReply="replyingTo = null"
      />
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
import { call, createResource, LoadingIndicator, toast } from "frappe-ui";
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { useDebounceFn } from "@vueuse/core";
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
const replyingTo = ref<Record<string, any> | null>(null);

const messages = createResource({
  url: "helpdesk.integrations.wa.get_whatsapp_messages",
  params: { ticket: props.ticketId },
  auto: true,
});

const ticketInfo = createResource({
  url: "helpdesk.integrations.wa.get_whatsapp_ticket_info",
  params: { ticket: props.ticketId },
  auto: true,
});

const markReadResource = createResource({
  url: "helpdesk.integrations.wa.mark_wa_messages_read",
});

const participantsResource = createResource({
  url: "helpdesk.integrations.wa.get_wa_group_participants",
  auto: false,
});

const mentionMap = computed<Record<string, string>>(() => {
  const participants: Array<{ jid: string; phone: string; name: string }> = participantsResource.data || [];
  const map: Record<string, string> = {};
  for (const p of participants) {
    if (!p.name) continue;
    if (p.phone) map[p.phone] = p.name;
    if (p.jid?.endsWith("@lid")) {
      const lid = p.jid.split("@")[0];
      if (lid) map[lid] = p.name;
    }
  }
  return map;
});

const sendReactionResource = createResource({
  url: "helpdesk.integrations.wa.send_wa_reaction",
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send reaction");
  },
});

function markAsRead() {
  markReadResource.submit({ ticket: props.ticketId });
}

const markAsReadDebounced = useDebounceFn(markAsRead, 2500);

const pickUpResource = createResource({
  url: "helpdesk.integrations.wa.pickup_whatsapp_ticket",
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

function scrollToMessage(messageId: string) {
  if (!messageId || !messagesContainer.value) return;
  const el = messagesContainer.value.querySelector(`[data-msg-id="${messageId}"]`) as HTMLElement | null;
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  // Flash highlight
  el.style.transition = "background 0.2s";
  el.style.background = "rgba(99,178,115,0.25)";
  setTimeout(() => { el.style.background = ""; }, 1200);
}

function pickUp() {
  pickingUp.value = true;
  pickUpResource.submit({ ticket: props.ticketId });
}

function startReply(message: Record<string, any>) {
  replyingTo.value = message;
}

function sendReaction(emoji: string, targetMessageId: string) {
  if (!targetMessageId) {
    toast.error("Cannot react: message has no WhatsApp ID yet");
    return;
  }
  sendReactionResource.submit({
    ticket: props.ticketId,
    target_message_id: targetMessageId,
    emoji,
  });
}

function onMessageSent() {
  replyingTo.value = null;
}

function onMessageDelivered() {
  // The thread used to refresh only on helpdesk:whatsapp-message, so an agent's
  // own message stayed invisible until they switched conversation and came back
  // and the list refetched. Refetching on the send's own response makes the
  // message appear whether or not the socket event arrives.
  messages.reload();
  ticketInfo.reload();
  scrollToBottom();
}

function handleRealtimeMessage(data: { ticket: string; is_incoming: boolean }) {
  if (String(data.ticket) === String(props.ticketId)) {
    messages.reload();
    ticketInfo.reload();
    scrollToBottom();
    markAsReadDebounced();
  }
}

function handleStatusUpdate(data: { ticket: string; message_name: string; status: string }) {
  if (String(data.ticket) !== String(props.ticketId)) return;
  const list = messages.data;
  if (!list) return;
  const msg = list.find((m: any) => m.name === data.message_name);
  if (msg) msg.status = data.status;
}

function handleMessageEdit(data: { message_id: string; new_text: string; name: string }) {
  const list = messages.data
  if (!list) return
  const msg = list.find((m: any) => m.message_id === data.message_id)
  if (msg) {
    msg.message = data.new_text
    msg.is_edited = 1
  }
}

async function applyEdit(messageName: string, newText: string) {
  try {
    await call("helpdesk.integrations.wa.edit_wa_message", {
      message_name: messageName,
      new_text: newText,
    })
  } catch (e: any) {
    toast.error(e?.messages?.[0] || "Could not save edit")
  }
}

watch(messageList, () => {
  scrollToBottom();
});

watch(() => ticketInfo.data, (info) => {
  if (info?.is_group && info?.jid && info?.baileys_line && !participantsResource.data) {
    participantsResource.submit({ jid: info.jid, line: info.baileys_line });
  }
});

onMounted(() => {
  $socket.on("helpdesk:whatsapp-message", handleRealtimeMessage);
  $socket.on("helpdesk:whatsapp-status-update", handleStatusUpdate);
  $socket.on("helpdesk:whatsapp-message-edit", handleMessageEdit);
  scrollToBottom();
  markAsRead();
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:whatsapp-message", handleRealtimeMessage);
  $socket.off("helpdesk:whatsapp-status-update", handleStatusUpdate);
  $socket.off("helpdesk:whatsapp-message-edit", handleMessageEdit);
});

defineExpose({ scrollToBottom });
</script>
