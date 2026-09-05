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
      <div v-if="activeTicketId" class="flex shrink-0 items-center gap-2">
        <Dropdown v-if="ticket?.doc" :options="statusDropdown" placement="right">
          <template #default>
            <Button :label="ticket.doc.status" size="sm">
              <template #prefix>
                <IndicatorIcon :class="ticketStatusStore.getStatus(ticket.doc.status)?.parsed_color" />
              </template>
            </Button>
          </template>
        </Dropdown>
        <router-link
          :to="`/tickets/${activeTicketId}`"
          class="text-xs font-medium text-blue-600 hover:underline"
        >
          #{{ activeTicketId }} →
        </router-link>
      </div>
    </div>

    <!-- Messages Area -->
    <div ref="messagesContainer" class="flex-1 overflow-y-auto px-5 py-4" @scroll="onScroll">
      <div v-if="initialLoading && !messageList.length" class="flex justify-center py-10">
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
        <!-- Load older messages -->
        <div v-if="hasMore" class="flex justify-center pb-2">
          <button
            :disabled="loadingOlder"
            class="rounded-lg border border-outline-gray-3 px-3 py-1.5 text-xs font-medium text-ink-gray-6 hover:bg-surface-gray-1 disabled:opacity-50"
            @click="loadOlder"
          >
            {{ loadingOlder ? "Loading…" : "Load older messages" }}
          </button>
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
            :isGroup="false"
            :mentionMap="{}"
            :allowRetry="!!msg._optimistic && msg.status === 'Failed'"
            @reply="startReply"
            @react="sendReaction"
            @scrollToReply="scrollToMessage"
            @retry="retryOptimistic"
          />
        </template>
      </div>
    </div>

    <!-- Reply box -->
    <WhatsAppReplyBox
      v-if="activeTicketId"
      ref="replyBox"
      :ticketId="activeTicketId"
      :replyTo="replyingTo"
      @sent="onMessageSent"
      @clearReply="replyingTo = null"
      @optimistic="addOptimistic"
      @optimistic-resolve="resolveOptimistic"
      @optimistic-remove="removeOptimistic"
    />
    <div
      v-else-if="!activeTicketLoading"
      class="border-t border-outline-gray-2 px-4 py-3 text-center text-xs text-ink-gray-5"
    >
      No ticket found for this number yet.
    </div>
  </div>
</template>

<script setup lang="ts">
import { call, createResource, Dropdown, LoadingIndicator, toast } from "frappe-ui";
import { computed, h, inject, nextTick, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { useDebounceFn } from "@vueuse/core";
import { globalStore } from "@/stores/globalStore";
import { hasRow, upsertMessage, type WaMessageEvent } from "@/utils/waRealtime";
import {
  applyResolve,
  markRetrying,
  mergeThread,
  removePending,
  upsertPending,
  type PendingBubble,
  type ResolvePayload,
} from "@/utils/waOptimistic";
import { watchResync, type ResyncHandle } from "@/utils/socketResync";
import { foldReactions } from "@/utils/waReactions";
import { useTicketStatusStore } from "@/stores/ticketStatus";
import { TicketSymbol } from "@/types";
import { HDTicketStatus } from "@/types/doctypes";
import { IndicatorIcon } from "@/components/icons";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";
import WhatsAppBubble from "./WhatsAppBubble.vue";
import WhatsAppReplyBox from "./WhatsAppReplyBox.vue";

const PAGE_SIZE = 50;
const NEAR_BOTTOM_THRESHOLD = 100;
const NEAR_TOP_THRESHOLD = 80;

const props = defineProps<{
  phone: string | null;
  displayName?: string;
  activeTicketId?: string | null;
  activeTicketLoading?: boolean;
  showBack?: boolean;
}>();

const emit = defineEmits<{ (e: "back"): void }>();

const { $socket } = globalStore();
const messagesContainer = ref<HTMLElement | null>(null);
const replyingTo = ref<Record<string, any> | null>(null);

// Loaded messages accumulate as older pages are fetched — oldest to newest.
const loadedMessages = ref<Record<string, any>[]>([]);
// Pending bubbles from the composer, kept apart so a merge cannot drop them.
const pending = ref<PendingBubble[]>([]);
const replyBox = ref<{ flush: () => void; retry: (name: string) => void } | null>(null);
const hasMore = ref(false);
const initialLoading = ref(false);
const loadingOlder = ref(false);

// ── Ticket status dropdown — same context WhatsAppBusinessPage.vue provides ──
const ticket = inject(TicketSymbol);
const ticketStatusStore = useTicketStatusStore();

const statusDropdown = computed(() => {
  const statuses = ticketStatusStore.statuses.data?.filter((s) => s.enabled) || [];
  return statuses.map((o: HDTicketStatus) => ({
    label: o.label_agent,
    value: o.label_agent,
    onClick: () => {
      if (!ticket?.value || !props.activeTicketId) return;
      $socket.emit("notify_ticket_update", props.activeTicketId, "Status", o.label_agent);
      if (ticket.value.doc.status === o.label_agent) return;
      ticket.value.setValue.submit({ status: o.label_agent });
    },
    icon: () => h(IndicatorIcon, { class: o.parsed_color }),
  }));
});

function isNearBottom(): boolean {
  const c = messagesContainer.value;
  if (!c) return true;
  return c.scrollHeight - c.scrollTop - c.clientHeight < NEAR_BOTTOM_THRESHOLD;
}

async function loadInitial() {
  loadedMessages.value = [];
  pending.value = [];
  hasMore.value = false;
  if (!props.phone) return;
  initialLoading.value = true;
  try {
    const res: any = await call("helpdesk.integrations.wa.get_whatsapp_messages", {
      phone: props.phone,
      limit: PAGE_SIZE,
    });
    loadedMessages.value = res?.messages || [];
    hasMore.value = !!res?.has_more;
    scrollToBottom();
  } finally {
    initialLoading.value = false;
  }
}

watch(() => props.phone, loadInitial, { immediate: true });

async function loadOlder() {
  if (!props.phone || loadingOlder.value || !hasMore.value || !loadedMessages.value.length) return;
  loadingOlder.value = true;
  const oldest = loadedMessages.value[0];
  const container = messagesContainer.value;
  const prevScrollHeight = container?.scrollHeight || 0;
  const prevScrollTop = container?.scrollTop || 0;
  try {
    const res: any = await call("helpdesk.integrations.wa.get_whatsapp_messages", {
      phone: props.phone,
      limit: PAGE_SIZE,
      before: oldest.creation,
    });
    const older = res?.messages || [];
    hasMore.value = !!res?.has_more;
    if (older.length) {
      loadedMessages.value = [...older, ...loadedMessages.value];
      await nextTick();
      if (container) {
        container.scrollTop = container.scrollHeight - prevScrollHeight + prevScrollTop;
      }
    }
  } finally {
    loadingOlder.value = false;
  }
}

function onScroll() {
  if (messagesContainer.value && messagesContainer.value.scrollTop < NEAR_TOP_THRESHOLD) {
    loadOlder();
  }
}

// Re-fetch just the latest page and merge it into what's already loaded —
// keeps any older pages the user scrolled up to load intact, instead of
// collapsing back to only the newest 50 on every new message.
async function mergeLatest(forceScroll = false) {
  if (!props.phone) return;
  const wasNearBottom = forceScroll || isNearBottom();
  const res: any = await call("helpdesk.integrations.wa.get_whatsapp_messages", {
    phone: props.phone,
    limit: PAGE_SIZE,
  });
  const latest: Record<string, any>[] = res?.messages || [];
  const byName = new Map(loadedMessages.value.map((m) => [m.name, m]));
  for (const m of latest) byName.set(m.name, m);
  const merged = Array.from(byName.values());
  merged.sort((a, b) => new Date(a.creation).getTime() - new Date(b.creation).getTime());
  loadedMessages.value = merged;
  if (wasNearBottom) scrollToBottom();
}

const sendReactionResource = createResource({
  url: "helpdesk.integrations.wa.send_wa_reaction",
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send reaction");
  },
});

// All currently-loaded messages (including reactions), plus whatever is pending
const allMessages = computed<Record<string, any>[]>(() =>
  mergeThread(loadedMessages.value, pending.value)
);

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
const reactionsMap = computed(() => foldReactions(allMessages.value));

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
  // The bubble is already in the thread; the stored row arrives by event.
  replyingTo.value = null;
}

// ── Optimistic send ─────────────────────────────────────────────────────────
function addOptimistic(bubble: PendingBubble) {
  pending.value = upsertPending(pending.value, bubble);
  scrollToBottom();
}

function resolveOptimistic(payload: ResolvePayload) {
  const result = applyResolve(loadedMessages.value, pending.value, payload);
  pending.value = result.pending;
  if (result.base !== loadedMessages.value) loadedMessages.value = result.base;
}

function removeOptimistic(name: string) {
  pending.value = removePending(pending.value, name);
}

function retryOptimistic(name: string) {
  pending.value = markRetrying(pending.value, name);
  replyBox.value?.retry(name);
}

function startReply(message: Record<string, any>) {
  replyingTo.value = message;
}

function sendReaction(emoji: string, targetMessageId: string) {
  if (!props.activeTicketId) return;
  if (!targetMessageId) {
    toast.error("Cannot react: message has no WhatsApp ID yet");
    return;
  }
  sendReactionResource.submit({
    ticket: props.activeTicketId,
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

// One reconciling refetch per burst: the event is applied directly, this
// catches what it cannot carry (media attached after the row was stored).
const reconcileDebounced = useDebounceFn(() => mergeLatest(), 2000);

function handleRealtimeMessage(data: WaMessageEvent) {
  if (!props.phone) return;
  if (!hasRow(data)) {
    // Legacy shape without a phone: nothing to filter on, re-resolve.
    mergeLatest();
    return;
  }
  if (data.phone !== props.phone) return;
  const wasNearBottom = isNearBottom();
  loadedMessages.value = upsertMessage(loadedMessages.value, data);
  if (wasNearBottom || data.type === "Outgoing") scrollToBottom();
  reconcileDebounced();
}

let resync: ResyncHandle | null = null;

onMounted(() => {
  $socket.on("helpdesk:whatsapp-message", handleRealtimeMessage);
  resync = watchResync($socket, () => mergeLatest());
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:whatsapp-message", handleRealtimeMessage);
  resync?.dispose();
});

defineExpose({ scrollToBottom, refresh: loadInitial });
</script>
