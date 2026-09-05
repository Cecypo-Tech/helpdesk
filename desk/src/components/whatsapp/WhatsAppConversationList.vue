<template>
  <div class="flex w-full h-full flex-col border-r border-outline-gray-2 bg-surface-gray-1">
    <div class="flex items-center justify-between border-b border-outline-gray-2 px-4 py-3">
      <h2 class="text-sm font-semibold text-ink-gray-9">WhatsApp Business</h2>
    </div>

    <div class="border-b border-outline-gray-2 px-3 py-2">
      <input
        v-model="search"
        type="text"
        placeholder="Search..."
        class="w-full rounded-lg border border-outline-gray-3 bg-surface-white px-3 py-1.5 text-xs text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none"
      />
    </div>

    <!-- Filter chips -->
    <div class="flex items-center gap-1.5 border-b border-outline-gray-2 px-3 py-2">
      <button
        v-for="f in filterOptions"
        :key="f.value"
        class="rounded-full px-2.5 py-1 text-xs font-medium transition-colors"
        :class="
          activeFilter === f.value
            ? 'bg-green-600 text-white'
            : 'bg-surface-gray-2 text-ink-gray-6 hover:bg-surface-gray-3'
        "
        @click="activeFilter = f.value"
      >{{ f.label }}</button>
    </div>

    <div class="flex-1 overflow-y-auto">
      <div v-if="conversations.loading && !loadedList.length" class="flex justify-center py-8">
        <LoadingIndicator :scale="5" class="text-ink-gray-5" />
      </div>
      <div
        v-else-if="!loadedList.length"
        class="py-10 text-center text-xs text-ink-gray-5"
      >
        {{ emptyMessage }}
      </div>
      <WhatsAppConversationItem
        v-for="conv in loadedList"
        :key="conv.phone"
        :phone="conv.phone"
        :displayName="conv.display_name"
        :lastMessage="conv.last_message"
        :lastMessageTime="conv.last_message_time"
        :lastDirection="conv.last_direction"
        :hasUnread="isUnread(conv)"
        :selected="conv.phone === selectedPhone"
        :ticketStatus="conv.ticket_status"
        :ticketPriority="conv.ticket_priority"
        :company="conv.company"
        :assignedTo="conv.assigned_to"
        :openTaskCount="conv.open_task_count || 0"
        @select="(phone, name) => $emit('select', phone, name)"
      />
      <div v-if="hasMore" class="px-3 py-2">
        <button
          class="w-full rounded-lg border border-outline-gray-2 py-1.5 text-xs font-medium text-ink-gray-6 hover:bg-surface-gray-2 disabled:opacity-50"
          :disabled="loadingMore"
          @click="loadMore"
        >
          {{ loadingMore ? "Loading…" : "Load older conversations" }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator } from "frappe-ui";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useDebounceFn } from "@vueuse/core";
import { globalStore } from "@/stores/globalStore";
import { applyEventToConversationList, type WaMessageEvent } from "@/utils/waRealtime";
import { watchResync, type ResyncHandle } from "@/utils/socketResync";
import WhatsAppConversationItem from "./WhatsAppConversationItem.vue";

const props = defineProps<{ selectedPhone: string | null }>();
const emit = defineEmits<{
  (e: "select", phone: string, displayName: string): void;
}>();

const search = ref("");
const lastReadMap = ref<Record<string, number>>({});

// ── Filter chips ─────────────────────────────────────────────────────────────
// "Open" mirrors the sidebar badge's definition (ticket not Resolved/Closed).
// It counts phones where the badge counts tickets, so one customer with two
// open tickets is 2 there and 1 here.
type FilterValue = "all" | "open" | "awaiting";
const filterOptions: { value: FilterValue; label: string }[] = [
  { value: "all", label: "All" },
  { value: "open", label: "Open" },
  { value: "awaiting", label: "Awaiting reply" },
];
const activeFilter = ref<FilterValue>("all");

// ── Conversations list ───────────────────────────────────────────────────────
// The endpoint returns one page at a time, so the rendered list is accumulated
// here rather than being whatever the last response happened to contain. Search
// and the chips are server-side for the same reason: narrowing a single page in
// the browser would hide conversations that simply hadn't been fetched yet.
const PAGE_SIZE = 50;

const loadedList = ref<any[]>([]);
const hasMore = ref(false);
const loadingMore = ref(false);

const conversations = createResource({
  url: "helpdesk.integrations.wa.get_whatsapp_conversations",
  auto: false,
});

const emptyMessage = computed(() => {
  if (search.value.trim()) return "No results";
  if (activeFilter.value === "open") return "No open conversations";
  if (activeFilter.value === "awaiting") return "Nothing awaiting a reply";
  return "No WhatsApp Business conversations yet";
});

function queryParams(offset: number) {
  return {
    search: search.value.trim(),
    conv_filter: activeFilter.value,
    limit: PAGE_SIZE,
    offset,
  };
}

let requestToken = 0;

async function fetchPage({ append = false } = {}) {
  const offset = append ? loadedList.value.length : 0;
  // Guard against a slow first page landing after the query has moved on.
  const token = ++requestToken;
  if (append) loadingMore.value = true;
  try {
    const data = await conversations.submit(queryParams(offset));
    if (token !== requestToken) return;
    const page = data?.conversations || [];
    if (append) {
      // The offset is derived from how many rows are loaded, but a socket
      // update can prepend a row between pages and shift everything down by
      // one — so a page can overlap what's already here.
      const loaded = new Set(loadedList.value.map((c: any) => c.phone));
      loadedList.value = [
        ...loadedList.value,
        ...page.filter((c: any) => !loaded.has(c.phone)),
      ];
    } else {
      loadedList.value = page;
    }
    hasMore.value = !!data?.has_more;
  } finally {
    if (token === requestToken) loadingMore.value = false;
  }
}

function reloadList() {
  fetchPage({ append: false });
}

// Merging the first page instead of resetting to it keeps any older pages the
// agent has already loaded — otherwise an idle list would collapse back to page
// one every time a message arrived.
async function mergeFirstPage() {
  const token = ++requestToken;
  const data = await conversations.submit(queryParams(0));
  if (token !== requestToken) return;
  const page = data?.conversations || [];
  const fetched = new Set(page.map((c: any) => c.phone));
  const older = loadedList.value.filter((c: any) => !fetched.has(c.phone));
  loadedList.value = [...page, ...older];
  if (!older.length) hasMore.value = !!data?.has_more;
}

function loadMore() {
  if (hasMore.value && !loadingMore.value) fetchPage({ append: true });
}

// Ticket status, assignee and company on a row come from the ticket, which an
// incoming message can create or reopen. The event cannot carry that, so the
// linked ("ingest") event also schedules one reconciling refetch per burst.
const reconcileDebounced = useDebounceFn(mergeFirstPage, 1500);

function onWhatsAppMessage(ev: WaMessageEvent) {
  // The event carries the row, so the list moves it in place. A refetch is
  // only needed when the phone is not loaded (new conversation, or one on a
  // page the agent never opened) or for the legacy event shape.
  const { list, handled } = applyEventToConversationList(loadedList.value, ev);
  if (!handled) {
    mergeFirstPage();
    return;
  }
  loadedList.value = list;
  if (ev.origin === "ingest") reconcileDebounced();
}

let resync: ResyncHandle | null = null;

onMounted(() => {
  reloadList();
  const { $socket } = globalStore();
  $socket.on("helpdesk:whatsapp-message", onWhatsAppMessage);
  // Events missed while the socket was down or the tab was hidden are gone;
  // the first page is the cheapest thing that puts the list right again.
  resync = watchResync($socket, mergeFirstPage);
});

onBeforeUnmount(() => {
  const { $socket } = globalStore();
  $socket.off("helpdesk:whatsapp-message", onWhatsAppMessage);
  resync?.dispose();
});

watch(activeFilter, reloadList);
// Debounced so a query isn't fired per keystroke.
watch(search, useDebounceFn(reloadList, 300));

watch(() => props.selectedPhone, (phone) => {
  if (phone) lastReadMap.value[phone] = Date.now();
});

function isUnread(conv: any): boolean {
  if (conv.last_direction !== "Incoming") return false;
  if (conv.phone === props.selectedPhone) return false;
  const msgTime = new Date(conv.last_message_time).getTime();
  const readTime = lastReadMap.value[conv.phone];
  if (readTime) return msgTime > readTime;
  const stored = localStorage.getItem(`whatsapp_business_last_read_${conv.phone}`);
  if (!stored) return true;
  return msgTime > new Date(stored).getTime();
}

defineExpose({ reload: reloadList });
</script>
