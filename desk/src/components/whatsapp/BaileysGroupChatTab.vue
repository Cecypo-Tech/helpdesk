<script setup lang="ts">
import { call, createResource, LoadingIndicator, toast } from "frappe-ui";
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
const replyingTo = ref<Record<string, any> | null>(null);

const PAGE_SIZE = 40;
const loadingMore = ref(false);
// Pages accumulate here rather than in `messages.data` — each refresh only
// re-fetches the newest page. Kept sorted oldest → newest.
const loadedMessages = ref<Record<string, any>[]>([]);
// Targets of replies that quote a message older than the loaded pages.
const replyTargets = ref<Record<string, any>[]>([]);
const hasMore = ref(false);
// Once the user has paged back, a newest-page refresh must not clobber hasMore.
const loadedOlder = ref(false);

const messages = createResource({
  url: "helpdesk.integrations.wa.get_whatsapp_messages",
  params: { ticket: props.ticketId, limit: PAGE_SIZE },
  auto: true,
  onSuccess(data: any) {
    mergeMessages(data?.messages || []);
    // This fires for refreshes too (new message, retry, edit), which only
    // re-fetch the newest page. Once older pages are loaded their reply targets
    // must survive, or previews on older replies silently go blank — and
    // has_more from the newest page says nothing about how far back we've gone.
    replyTargets.value = loadedOlder.value
      ? mergeReplyTargets(replyTargets.value, data?.reply_targets || [])
      : data?.reply_targets || [];
    if (!loadedOlder.value) hasMore.value = !!data?.has_more;
  },
});

// Cross-line duplicates share a message_id, so key on that where present.
function messageKey(m: Record<string, any>) {
  return m.message_id || m.name;
}

function mergeMessages(incoming: Record<string, any>[]) {
  const byKey = new Map<string, Record<string, any>>();
  for (const m of loadedMessages.value) byKey.set(messageKey(m), m);
  // Re-fetched rows win so refreshes pick up status changes and edits.
  for (const m of incoming) byKey.set(messageKey(m), m);
  loadedMessages.value = [...byKey.values()].sort((a, b) => {
    if (a.creation === b.creation) return a.name < b.name ? -1 : 1;
    return a.creation < b.creation ? -1 : 1;
  });
}

// Refreshes re-send the same targets, so dedupe rather than growing forever.
function mergeReplyTargets(existing: Record<string, any>[], incoming: Record<string, any>[]) {
  const byId = new Map<string, Record<string, any>>();
  for (const m of [...existing, ...incoming]) byId.set(messageKey(m), m);
  return [...byId.values()];
}

async function loadMore() {
  if (loadingMore.value || !hasMore.value) return;
  loadingMore.value = true;
  const container = messagesContainer.value;
  const prevScrollHeight = container?.scrollHeight ?? 0;
  const oldest = loadedMessages.value.find((m) => m.content_type !== "reaction");
  try {
    const data = await call("helpdesk.integrations.wa.get_whatsapp_messages", {
      ticket: props.ticketId,
      limit: PAGE_SIZE,
      before: oldest?.creation,
      before_name: oldest?.name,
    });
    mergeMessages(data?.messages || []);
    replyTargets.value = mergeReplyTargets(replyTargets.value, data?.reply_targets || []);
    hasMore.value = !!data?.has_more;
    loadedOlder.value = true;
    await nextTick();
    if (container) container.scrollTop = container.scrollHeight - prevScrollHeight;
  } catch {
    toast.error("Could not load older messages");
  } finally {
    loadingMore.value = false;
  }
}

const tabInfo = createResource({
  url: "helpdesk.integrations.wa.get_whatsapp_ticket_info",
  params: { ticket: props.ticketId },
  auto: true,
});

// This component isn't keyed on the ticket, so it can be reused across tickets
// with only the prop changing. `params` above is captured at setup, so without
// this watcher a reused instance keeps showing the previous ticket's chat.
// Resetting first also stops the old messages rendering while the new ones load.
watch(
  () => props.ticketId,
  (ticketId) => {
    if (!ticketId) return;
    messages.reset();
    loadedMessages.value = [];
    replyTargets.value = [];
    hasMore.value = false;
    loadedOlder.value = false;
    messages.update({ params: { ticket: ticketId, limit: PAGE_SIZE } });
    messages.reload();
    tabInfo.update({ params: { ticket: ticketId } });
    tabInfo.reload();
  }
);

const markReadResource = createResource({
  url: "helpdesk.integrations.wa.mark_wa_messages_read",
});

const pickUpResource = createResource({
  url: "helpdesk.integrations.wa.pickup_whatsapp_ticket",
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

const sendReactionResource = createResource({
  url: "helpdesk.integrations.wa.send_wa_reaction",
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send reaction");
  },
});

const retryResource = createResource({
  url: "helpdesk.integrations.wa.retry_wa_message",
  onSuccess() {
    messages.reload();
    toast.success("Message sent");
  },
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Retry failed");
  },
});

function retryMessage(messageName: string) {
  retryResource.submit({ message_name: messageName });
}

const editResource = createResource({
  url: "helpdesk.integrations.wa.edit_wa_message",
  onSuccess() {
    messages.reload();
  },
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to edit message");
  },
});

function handleEdit(messageName: string, newText: string) {
  editResource.submit({ message_name: messageName, new_text: newText });
}

const allMessages = computed<Record<string, any>[]>(() => loadedMessages.value);

const messageList = computed(() =>
  allMessages.value.filter((m) => m.content_type !== "reaction")
);

const messageByMsgId = computed(() => {
  const map: Record<string, Record<string, any>> = {};
  // Reply targets first, so a loaded message always wins over its stub copy.
  for (const m of [...replyTargets.value, ...allMessages.value]) {
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
  sendReactionResource.submit({ ticket: props.ticketId, target_message_id: targetMessageId, emoji });
}

function onMessageSent() {
  replyingTo.value = null;
}

function markAsRead() {
  markReadResource.submit({ ticket: props.ticketId });
}

function handleRealtimeMessage(data: { ticket?: string; jid?: string; is_incoming?: boolean }) {
  // WA Line broadcasts on helpdesk:baileys-message. Outgoing events carry `ticket`;
  // incoming events only carry `jid`, so match on either.
  const sameTicket = data.ticket && String(data.ticket) === String(props.ticketId);
  const sameJid = data.jid && tabInfo.data?.jid && data.jid === tabInfo.data.jid;
  if (sameTicket || sameJid) {
    messages.reload();
    tabInfo.reload();
    scrollToBottom();
    markAsRead();
  }
}

function handleStatusUpdate(data: { message_id: string; status: string }) {
  const msg = loadedMessages.value.find((m) => m.message_id === data.message_id);
  if (msg) msg.status = data.status;
}

function handleEditUpdate(data: { message_id: string; new_text: string; name: string; jid: string }) {
  const list: Record<string, any>[] = messages.data || [];
  const msg = list.find((m) => m.message_id === data.message_id || m.name === data.name);
  if (msg) {
    msg.message = data.new_text;
    msg.is_edited = 1;
  }
}

watch(messageList, () => { scrollToBottom(); });

onMounted(() => {
  $socket.on("helpdesk:baileys-message", handleRealtimeMessage);
  $socket.on("helpdesk:baileys-status-update", handleStatusUpdate);
  $socket.on("helpdesk:whatsapp-message-edit", handleEditUpdate);
  scrollToBottom();
  markAsRead();
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:baileys-message", handleRealtimeMessage);
  $socket.off("helpdesk:baileys-status-update", handleStatusUpdate);
  $socket.off("helpdesk:whatsapp-message-edit", handleEditUpdate);
});

defineExpose({ scrollToBottom });
</script>

<template>
  <div class="flex flex-1 flex-col overflow-hidden">
    <!-- Messages area -->
    <div ref="messagesContainer" class="flex-1 overflow-y-auto px-5 py-4">
      <div v-if="messages.loading && !loadedMessages.length" class="flex justify-center py-10">
        <LoadingIndicator :scale="6" class="text-ink-gray-5" />
      </div>

      <div
        v-else-if="!messageList.length"
        class="flex flex-col items-center justify-center py-16 text-ink-gray-5"
      >
        <WhatsAppIcon class="mb-3 h-8 w-8 text-ink-gray-4" />
        <p class="text-sm">No messages yet</p>
      </div>

      <div v-else class="space-y-3">
        <!-- Load older messages -->
        <div v-if="hasMore" class="flex justify-center pb-2">
          <button
            class="rounded-full bg-surface-white px-3 py-1 text-xs text-ink-gray-6 shadow-sm hover:bg-surface-gray-2"
            :disabled="loadingMore"
            @click="loadMore"
          >{{ loadingMore ? "Loading…" : "Load older messages" }}</button>
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
            :reactions="reactionsMap[msg.message_id] || []"
            :replyToMessage="msg.is_reply && msg.reply_to_message_id ? messageByMsgId[msg.reply_to_message_id] || null : null"
            :isGroup="true"
            :allowRetry="true"
            :allowEdit="true"
            @reply="startReply"
            @react="sendReaction"
            @scrollToReply="scrollToMessage"
            @retry="retryMessage"
            @edit="handleEdit"
          />
        </template>
      </div>
    </div>

    <!-- Bottom area -->
    <div v-if="tabInfo.data?.has_whatsapp">
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
        :replyTo="replyingTo"
        @sent="onMessageSent"
        @clearReply="replyingTo = null"
      />
    </div>

    <div
      v-else-if="tabInfo.fetched && tabInfo.data && !tabInfo.data.has_whatsapp"
      class="border-t border-outline-gray-2 px-4 py-3 text-center text-xs text-ink-gray-5"
    >
      This ticket is not linked to a WhatsApp chat.
    </div>
  </div>
</template>
