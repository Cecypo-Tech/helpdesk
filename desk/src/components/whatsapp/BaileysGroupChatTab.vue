<script setup lang="ts">
import { call, createResource, LoadingIndicator, toast } from "frappe-ui";
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { globalStore } from "@/stores/globalStore";
import { useWaLinesStore } from "@/stores/waLines";
import { foldReactions } from "@/utils/waReactions";
import ConfirmDialog from "@/components/ConfirmDialog.vue";
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
// message_ids we've already tried to resolve on demand (declared with the refs so
// the ticket watcher can clear it regardless of ordering).
const attemptedReplyTargets = new Set<string>();
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
    attemptedReplyTargets.clear();
    messages.update({ params: { ticket: ticketId, limit: PAGE_SIZE } });
    messages.reload();
    tabInfo.update({ params: { ticket: ticketId } });
    tabInfo.reload();
  }
);

const waLinesStore = useWaLinesStore();

const markReadResource = createResource({
  url: "helpdesk.integrations.wa.mark_wa_messages_read",
  // Reading WhatsApp messages inside a ticket has to drop the sidebar badge
  // too. This tab is addressed by ticket, not line, so it can't do the targeted
  // decrement BaileysChat does — but it only fires when an agent opens a tab
  // with genuinely unread messages, so a refetch here is bounded by user
  // actions rather than by message volume.
  onSuccess(cleared: number) {
    if (cleared) waLinesStore.reload();
  },
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

const deleteResource = createResource({
  url: "helpdesk.integrations.wa.delete_wa_message",
  onSuccess() {
    messages.reload();
  },
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to delete message");
  },
});

// Deleting also removes the message from the recipient's phone, so confirm first.
const pendingDeleteName = ref("");
const showDeleteConfirm = ref(false);

function handleDelete(messageName: string) {
  pendingDeleteName.value = messageName;
  showDeleteConfirm.value = true;
}

function confirmDelete() {
  const name = pendingDeleteName.value;
  showDeleteConfirm.value = false;
  pendingDeleteName.value = "";
  if (name) deleteResource.submit({ message_name: name });
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

const reactionsMap = computed(() => foldReactions(allMessages.value));

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

function findMessageEl(messageId: string): HTMLElement | null {
  return (
    (messagesContainer.value?.querySelector(`[data-msg-id="${messageId}"]`) as HTMLElement | null) ||
    null
  );
}

// The quoted preview can resolve a message that isn't loaded yet (it's paginated
// in from an older page — see resolveMissingReplyTargets), so it has no bubble
// and no data-msg-id to scroll to. Page back until it appears or history runs out.
async function scrollToMessage(messageId: string) {
  if (!messageId || !messagesContainer.value) return;
  let el = findMessageEl(messageId);
  while (!el && hasMore.value) {
    await loadMore();
    el = findMessageEl(messageId);
  }
  if (!el) {
    toast.error("Could not find the original message");
    return;
  }
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

// ── Optimistic send ─────────────────────────────────────────────────────────
// The reply box inserts a pending bubble before its upload/send completes, then
// resolves it to the real message_id (so the realtime reload dedupes against it
// via mergeMessages' message_id key) or removes it on a pre-send failure.
function addOptimistic(msg: Record<string, any>) {
  loadedMessages.value = [...loadedMessages.value, msg];
  scrollToBottom();
}

function resolveOptimistic(payload: { name: string; realName: string; message_id: string; status: string }) {
  const msg = loadedMessages.value.find((m) => m.name === payload.name);
  if (!msg) return;
  // If the real row already arrived via the realtime reload, drop the pending
  // bubble rather than stamping it (which would leave a duplicate). Match on
  // message_id, or on the real docname when the send failed (empty message_id).
  const dupExists = loadedMessages.value.some(
    (m) =>
      m !== msg &&
      ((payload.message_id && m.message_id === payload.message_id) ||
        (payload.realName && m.name === payload.realName))
  );
  if (dupExists) {
    loadedMessages.value = loadedMessages.value.filter((m) => m !== msg);
    return;
  }
  if (payload.message_id) msg.message_id = payload.message_id;
  if (payload.realName) msg.name = payload.realName;
  msg.status = payload.status;
  delete msg._optimistic;
}

function removeOptimistic(name: string) {
  loadedMessages.value = loadedMessages.value.filter((m) => m.name !== name);
}

// ── On-demand reply-target resolution ───────────────────────────────────────
// A reply whose quoted target is outside the loaded page and wasn't returned in
// reply_targets (e.g. after a realtime refresh) is fetched by message_id so its
// preview resolves instead of falling back to the "earlier message" placeholder.
// (`attemptedReplyTargets` is declared with the refs above.)

async function resolveMissingReplyTargets() {
  const jid = tabInfo.data?.jid;
  if (!jid) return;
  const have = messageByMsgId.value;
  const wanted: string[] = [];
  for (const m of messageList.value) {
    const rid = m.reply_to_message_id;
    if (m.is_reply && rid && !have[rid] && !attemptedReplyTargets.has(rid)) {
      attemptedReplyTargets.add(rid);
      wanted.push(rid);
    }
  }
  for (const rid of wanted) {
    try {
      const row = await call("helpdesk.integrations.wa.get_wa_message_by_message_id", {
        message_id: rid,
        jid,
      });
      if (row) replyTargets.value = mergeReplyTargets(replyTargets.value, [row]);
    } catch {
      // Leave it attempted; the bubble shows the graceful fallback.
    }
  }
}

watch(messageList, () => {
  resolveMissingReplyTargets();
});

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

// Looking the message up in the rendered list is what scopes this to the open
// chat: an id we aren't showing simply isn't found.
function handleDeleteUpdate(data: { message_id: string }) {
  const msg = loadedMessages.value.find((m) => m.message_id === data.message_id);
  if (msg) msg.is_deleted = 1;
}

watch(messageList, () => { scrollToBottom(); });

onMounted(() => {
  $socket.on("helpdesk:baileys-message", handleRealtimeMessage);
  $socket.on("helpdesk:baileys-status-update", handleStatusUpdate);
  $socket.on("helpdesk:whatsapp-message-edit", handleEditUpdate);
  $socket.on("helpdesk:whatsapp-message-delete", handleDeleteUpdate);
  scrollToBottom();
  markAsRead();
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:baileys-message", handleRealtimeMessage);
  $socket.off("helpdesk:baileys-status-update", handleStatusUpdate);
  $socket.off("helpdesk:whatsapp-message-edit", handleEditUpdate);
  $socket.off("helpdesk:whatsapp-message-delete", handleDeleteUpdate);
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
            :allowDelete="true"
            @reply="startReply"
            @react="sendReaction"
            @scrollToReply="scrollToMessage"
            @retry="retryMessage"
            @edit="handleEdit"
            @delete="handleDelete"
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
        @optimistic="addOptimistic"
        @optimistic-resolve="resolveOptimistic"
        @optimistic-remove="removeOptimistic"
      />
    </div>

    <div
      v-else-if="tabInfo.fetched && tabInfo.data && !tabInfo.data.has_whatsapp"
      class="border-t border-outline-gray-2 px-4 py-3 text-center text-xs text-ink-gray-5"
    >
      This ticket is not linked to a WhatsApp chat.
    </div>

    <ConfirmDialog
      v-if="showDeleteConfirm"
      v-model="showDeleteConfirm"
      title="Delete message"
      message="Delete this message for everyone? It will be removed from the recipient's WhatsApp too. This cannot be undone."
      :onConfirm="confirmDelete"
      :onCancel="() => (showDeleteConfirm = false)"
    />
  </div>
</template>
