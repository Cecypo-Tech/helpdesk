<template>
  <div
    v-if="!jid"
    class="flex flex-1 flex-col items-center justify-center text-ink-gray-4"
  >
    <WhatsAppIcon class="mb-3 h-14 w-14" />
    <p class="text-sm">Select a conversation</p>
  </div>

  <div v-else class="flex flex-1 flex-col overflow-hidden">
    <div class="flex items-center gap-3 border-b border-outline-gray-2 bg-surface-gray-1 px-4 py-2.5">
      <WhatsAppIcon class="h-4 w-4 shrink-0 text-green-600" />
      <div class="min-w-0 flex-1">
        <div class="truncate text-sm font-semibold text-ink-gray-9">
          {{ displayName || jid.split("@")[0] }}
        </div>
        <div v-if="phoneDisplay" class="text-[11px] text-ink-gray-5">
          Connected: {{ phoneDisplay }}
        </div>
      </div>
    </div>

    <div ref="messagesContainer" class="flex-1 overflow-y-auto bg-[#e5ddd5] px-5 py-4">
      <div v-if="messages.loading && !messages.data" class="flex justify-center py-10">
        <LoadingIndicator :scale="6" class="text-ink-gray-5" />
      </div>

      <div
        v-else-if="!messageList.length"
        class="flex flex-col items-center justify-center py-16 text-ink-gray-5"
      >
        <WhatsAppIcon class="mb-3 h-10 w-10 text-ink-gray-4" />
        <p class="text-sm">No messages yet</p>
      </div>

      <div v-else class="space-y-3">
        <template v-for="(group, dateKey) in groupedMessages" :key="dateKey">
          <div class="my-4 flex items-center gap-3">
            <div class="flex-1 border-t border-outline-gray-2" />
            <span class="rounded-full bg-surface-white px-2 text-[11px] font-medium text-ink-gray-5">
              {{ dateKey }}
            </span>
            <div class="flex-1 border-t border-outline-gray-2" />
          </div>
          <WhatsAppBubble
            v-for="msg in group"
            :key="msg.name"
            :data-msg-id="msg.message_id"
            :message="msg"
            :reactions="reactionsMap[msg.message_id] || []"
            :replyToMessage="msg.is_reply && msg.reply_to_message_id ? messageByMsgId[msg.reply_to_message_id] || null : null"
            @reply="startReply"
            @react="sendReaction"
            @scrollToReply="scrollToMessage"
          />
        </template>
      </div>
    </div>

    <BaileysReplyBox
      ticketId=""
      :jid="jid"
      :replyTo="replyingTo"
      @sent="onMessageSent"
      @clearReply="replyingTo = null"
    />
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator, toast } from "frappe-ui";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { globalStore } from "@/stores/globalStore";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";
import WhatsAppBubble from "./WhatsAppBubble.vue";
import BaileysReplyBox from "./BaileysReplyBox.vue";

const props = defineProps<{
  jid: string | null;
  displayName: string;
}>();

const { $socket } = globalStore();
const messagesContainer = ref<HTMLElement | null>(null);
const replyingTo = ref<Record<string, any> | null>(null);

const connectedPhone = createResource({
  url: "helpdesk.integrations.baileys.get_connected_phone",
  auto: true,
});

const messages = createResource({
  url: "helpdesk.integrations.baileys.get_baileys_messages",
  auto: false,
});

const sendReactionResource = createResource({
  url: "helpdesk.integrations.baileys.send_baileys_reaction",
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send reaction");
  },
});

function loadMessages() {
  if (props.jid) {
    messages.submit({ jid: props.jid });
    localStorage.setItem(`baileys_last_read_${props.jid}`, new Date().toISOString());
  }
}

watch(
  () => props.jid,
  (newJid) => {
    if (newJid) {
      replyingTo.value = null;
      loadMessages();
    }
  },
  { immediate: true }
);

const allMessages = computed<Record<string, any>[]>(() => messages.data || []);

const messageList = computed(() =>
  allMessages.value.filter((m) => m.content_type !== "reaction")
);

const messageByMsgId = computed(() => {
  const map: Record<string, Record<string, any>> = {};
  for (const m of allMessages.value) {
    if (m.message_id) map[m.message_id] = m;
  }
  return map;
});

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
      year: "numeric", month: "short", day: "numeric",
    });
    if (!groups[date]) groups[date] = [];
    groups[date].push(msg);
  }
  return groups;
});

const phoneDisplay = computed(() => {
  const phone = connectedPhone.data?.phone;
  return phone ? `+${phone}` : null;
});

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value)
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
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

function startReply(message: Record<string, any>) {
  replyingTo.value = message;
}

function sendReaction(emoji: string, targetMessageId: string) {
  if (!targetMessageId) {
    toast.error("Cannot react: message has no WhatsApp ID yet");
    return;
  }
  sendReactionResource.submit({ jid: props.jid, target_message_id: targetMessageId, emoji });
}

function onMessageSent() {
  replyingTo.value = null;
  if (props.jid) messages.submit({ jid: props.jid });
  scrollToBottom();
}

function handleRealtimeMessage(data: { jid: string; is_incoming: boolean }) {
  if (data.jid === props.jid) {
    if (props.jid) messages.submit({ jid: props.jid });
    if (data.is_incoming) {
      scrollToBottom();
      localStorage.setItem(`baileys_last_read_${props.jid}`, new Date().toISOString());
    }
  }
}

watch(messageList, () => { scrollToBottom(); });

onMounted(() => {
  $socket.on("helpdesk:baileys-message", handleRealtimeMessage);
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:baileys-message", handleRealtimeMessage);
});
</script>
