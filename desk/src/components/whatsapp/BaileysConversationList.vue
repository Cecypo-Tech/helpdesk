<template>
  <div class="flex w-full h-full flex-col border-r border-outline-gray-2 bg-surface-gray-1">
    <div class="flex items-center justify-between border-b border-outline-gray-2 px-4 py-3">
      <div>
        <h2 class="text-sm font-semibold text-ink-gray-9">WhatsApp</h2>
      </div>
      <div class="flex items-center gap-1">
        <!-- Mark all read (UI badge only — does not send WhatsApp read receipts) -->
        <button
          class="flex h-6 w-6 items-center justify-center rounded-full text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8 disabled:opacity-40"
          :title="unreadCount ? `Mark all read (${unreadCount}) — does not send read receipts` : 'No unread'"
          :disabled="!unreadCount"
          @click="markAllRead"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"/><polyline points="20 12 9 23 4 18"/>
          </svg>
        </button>
        <!-- Sync contacts -->
        <button
          class="flex h-6 w-6 items-center justify-center rounded-full text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8 disabled:opacity-40"
          :title="syncingContacts ? 'Syncing…' : 'Sync contacts'"
          :disabled="syncingContacts"
          @click="syncContacts"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
            :class="syncingContacts ? 'animate-spin' : ''">
            <polyline points="1 4 1 10 7 10"/><polyline points="23 20 23 14 17 14"/>
            <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/>
          </svg>
        </button>
        <!-- Analytics -->
        <button
          class="flex h-6 w-6 items-center justify-center rounded-full text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8"
          title="Analytics"
          @click="router.push('/whatsapp/analytics')"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
          </svg>
        </button>
        <!-- New chat -->
        <button
          class="flex h-6 w-6 items-center justify-center rounded-full text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8"
          title="New chat"
          @click="showNewChat = true"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><line x1="12" y1="8" x2="12" y2="14"/><line x1="9" y1="11" x2="15" y2="11"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- New chat panel -->
    <div v-if="showNewChat" class="border-b border-outline-gray-2 bg-surface-gray-1 px-3 py-2.5">
      <div class="mb-1.5 flex items-center justify-between">
        <span class="text-[11px] font-medium text-ink-gray-5">New chat</span>
        <button class="text-ink-gray-4 hover:text-ink-gray-7" @click="closeNewChat">✕</button>
      </div>
      <input
        ref="newChatInputRef"
        v-model="newChatQuery"
        type="text"
        placeholder="Search contacts or enter phone..."
        class="w-full rounded border border-outline-gray-3 bg-surface-white px-2 py-1.5 text-xs text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none"
        @keydown.esc="closeNewChat"
        @keydown.enter="onNewChatEnter"
      />
      <div v-if="newChatError" class="mt-1 text-[11px] text-red-500">{{ newChatError }}</div>

      <!-- Contact suggestions -->
      <div v-if="contactSuggestions.length || phoneOption" class="mt-1.5 max-h-48 overflow-y-auto rounded border border-outline-gray-2 bg-surface-gray-1 shadow-sm">
        <!-- Existing contacts -->
        <button
          v-for="c in contactSuggestions"
          :key="c.jid"
          class="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-surface-gray-2"
          @click="selectContact(c)"
        >
          <div class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-gray-3 text-[10px] font-bold text-ink-gray-7">
            {{ (c.custom_name || c.phone || "?")[0].toUpperCase() }}
          </div>
          <div class="min-w-0 flex-1">
            <div class="truncate text-xs font-medium text-ink-gray-8">{{ c.custom_name || c.phone }}</div>
            <div v-if="c.company" class="truncate text-[10px] text-ink-gray-5">{{ c.company }}</div>
            <div class="truncate text-[10px] text-ink-gray-4">{{ c.phone }}</div>
          </div>
        </button>

        <!-- Raw phone number fallback -->
        <button
          v-if="phoneOption"
          class="flex w-full items-center gap-2 border-t border-outline-gray-2 px-2.5 py-1.5 text-left hover:bg-surface-gray-2"
          @click="startWithPhone(phoneOption)"
        >
          <div class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-gray-3 text-[10px] text-ink-gray-5">
            #
          </div>
          <div class="text-xs text-ink-gray-6">Start chat with <span class="font-medium text-ink-gray-8">+{{ phoneOption }}</span></div>
        </button>
      </div>

      <!-- Empty state when typing but no matches -->
      <div
        v-else-if="newChatQuery.trim() && !contactsResource.loading"
        class="mt-1.5 rounded border border-outline-gray-2 bg-surface-gray-1 px-3 py-2 text-[11px] text-ink-gray-5"
      >
        No contacts found. Enter a valid phone number to start a new chat.
      </div>
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
        {{ search || activeFilter !== "all" ? "No results" : "No conversations" }}
      </div>
      <BaileysConversationItem
        v-for="conv in loadedList"
        :key="conv.jid"
        :jid="conv.jid"
        :displayName="conv.display_name"
        :company="conv.company"
        :assignedTeam="conv.assigned_team"
        :phone="conv.phone"
        :isGroup="conv.is_group"
        :lastMessage="conv.last_message"
        :lastSenderName="conv.last_sender_name"
        :lastMessageTime="conv.last_message_time"
        :lastDirection="conv.last_direction"
        :contentType="conv.content_type"
        :unreadCount="conv.unread_count || 0"
        :isFavourite="favouriteJids.has(conv.jid)"
        :selected="conv.jid === selectedJid"
        :openTaskCount="conv.open_task_count || 0"
        @select="(jid, name, company, team, phone) => $emit('select', jid, name, company, team, phone)"
        @toggle-favourite="toggleFavourite"
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
import { createResource, LoadingIndicator, toast } from "frappe-ui";
import { useDebounceFn } from "@vueuse/core";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { useWaLinesStore } from "@/stores/waLines";
import { globalStore } from "@/stores/globalStore";
import BaileysConversationItem from "./BaileysConversationItem.vue";

const props = defineProps<{ line: string; selectedJid: string | null }>();
const emit = defineEmits<{
  (e: "select", jid: string, displayName: string, company: string, assignedTeam: string, phone: string): void;
}>();

const router = useRouter();
const waLinesStore = useWaLinesStore();

const search = ref("");
const syncingContacts = ref(false);

// ── Filter chips ─────────────────────────────────────────────────────────────

type FilterValue = "all" | "unread" | "favourites" | "groups";
const filterOptions: { value: FilterValue; label: string }[] = [
  { value: "all", label: "All" },
  { value: "unread", label: "Unread" },
  { value: "favourites", label: "Favourites" },
  { value: "groups", label: "Groups" },
];
const activeFilter = ref<FilterValue>("all");

// ── Favourites (personal, per-browser) ──────────────────────────────────────

const FAVOURITES_KEY = "wa_favourite_chats";
const favouriteJids = ref<Set<string>>(new Set(loadFavourites()));

function loadFavourites(): string[] {
  try {
    return JSON.parse(localStorage.getItem(FAVOURITES_KEY) || "[]");
  } catch {
    return [];
  }
}

function toggleFavourite(jid: string) {
  const next = new Set(favouriteJids.value);
  if (next.has(jid)) next.delete(jid);
  else next.add(jid);
  favouriteJids.value = next;
  try {
    localStorage.setItem(FAVOURITES_KEY, JSON.stringify([...next]));
  } catch {}
}

const syncResource = createResource({
  url: "helpdesk.integrations.wa.enqueue_wa_sync",
  auto: false,
  onSuccess() {
    // Job queued — spinner stays until helpdesk:wa-sync-complete fires
  },
  onError(e: any) {
    syncingContacts.value = false;
    toast.error(e?.messages?.[0] || "Sync failed to queue");
  },
});

function syncContacts() {
  if (syncingContacts.value) return;
  syncingContacts.value = true;
  syncResource.submit({});
  // Group sync re-resolves up to 50 groups per WA Line (~1s each via Evolution API),
  // so this can legitimately take several minutes across multiple lines — the backend
  // job itself is allowed up to 30 minutes. This is just a safety net for a lost
  // socket event, not a realistic expectation of how long a healthy sync takes.
  setTimeout(() => {
    if (syncingContacts.value) {
      syncingContacts.value = false;
      toast.error("Sync is taking unusually long — check server logs");
    }
  }, 480_000);
}

function onSyncComplete(data: { contacts?: number; groups?: number; error?: string }) {
  syncingContacts.value = false;
  if (data?.error) {
    toast.error(`Sync failed: ${data.error}`);
  } else {
    toast.success(`Synced ${data?.contacts ?? 0} contact(s) and ${data?.groups ?? 0} group(s)`);
  }
  reloadList();
}

// get_wa_conversations aggregates over the entire message table with no LIMIT,
// so refetching it on every `helpdesk:baileys-message` — which fires for every
// message in both directions, to every connected agent — was by a wide margin
// the most expensive thing this app did. The event now carries a preview of the
// message, which is everything a row needs, so the common case patches in place
// and never touches the network.
const debouncedReload = useDebounceFn(() => mergeFirstPage(), 3000);

interface BaileysMessageEvent {
  jid?: string;
  line?: string;
  is_incoming?: boolean;
  preview?: {
    message?: string;
    content_type?: string;
    sender_name?: string;
    direction?: string;
    creation?: string;
  };
}

function onBaileysMessage(data: BaileysMessageEvent) {
  if (!data?.jid) return;
  if (props.line && data.line && data.line !== props.line) return;

  const list: any[] = loadedList.value;
  const row = list.find((c) => c.jid === data.jid);

  // No row means a conversation we've never rendered (first message from a new
  // contact) — only a fetch can supply its name, company and team. No preview
  // means an older backend, or a publisher without the message doc at hand.
  if (!row || !data.preview) {
    debouncedReload();
    return;
  }

  const p = data.preview;
  const isOpen = props.selectedJid === data.jid;
  const patched = {
    ...row,
    last_message: p.message || `[${p.content_type || "media"}]`,
    last_sender_name: row.is_group ? p.sender_name || "" : "",
    last_message_time: p.creation,
    last_direction: p.direction || (data.is_incoming ? "Incoming" : "Outgoing"),
    content_type: p.content_type || "text",
    // The open conversation marks itself read as it renders the message, so
    // counting it here would only produce a badge that immediately clears.
    unread_count:
      data.is_incoming && !isOpen
        ? (row.unread_count || 0) + 1
        : row.unread_count || 0,
  };

  // The server orders by most recent first; keep that as rows are patched.
  loadedList.value = [patched, ...list.filter((c) => c.jid !== data.jid)];
}

// Missed events during a dropped connection can leave rows stale or absent.
function onSocketConnect() {
  debouncedReload();
}

onMounted(() => {
  const { $socket } = globalStore();
  $socket.on("helpdesk:wa-sync-complete", onSyncComplete);
  $socket.on("helpdesk:baileys-message", onBaileysMessage);
  $socket.on("connect", onSocketConnect);
});

onBeforeUnmount(() => {
  const { $socket } = globalStore();
  $socket.off("helpdesk:wa-sync-complete", onSyncComplete);
  $socket.off("helpdesk:baileys-message", onBaileysMessage);
  $socket.off("connect", onSocketConnect);
});

// ── New chat ────────────────────────────────────────────────────────────────

const showNewChat = ref(false);
const newChatQuery = ref("");
const newChatError = ref("");
const newChatInputRef = ref<HTMLInputElement | null>(null);

const contactsResource = createResource({
  url: "helpdesk.integrations.wa.search_whatsapp_contacts",
  auto: false,
});

watch(newChatQuery, (q) => {
  newChatError.value = "";
  contactsResource.submit({ query: q.trim() });
});

watch(showNewChat, (open) => {
  if (open) {
    newChatQuery.value = "";
    newChatError.value = "";
    contactsResource.submit({ query: "" });
    nextTick(() => newChatInputRef.value?.focus());
  }
});

const contactSuggestions = computed<any[]>(() => contactsResource.data || []);

const phoneOption = computed<string | null>(() => {
  const raw = newChatQuery.value.trim().replace(/[\s\-()]/g, "").replace(/^\+/, "");
  // Reject local-format numbers (leading 0) — WhatsApp needs international format (e.g. 254720776486)
  if (/^\d{7,15}$/.test(raw) && !raw.startsWith("0")) return raw;
  return null;
});

function closeNewChat() {
  showNewChat.value = false;
  newChatQuery.value = "";
  newChatError.value = "";
}

function selectContact(c: { jid: string; custom_name: string; phone: string; company: string }) {
  closeNewChat();
  emit("select", c.jid, c.custom_name || c.phone, c.company || "", "", c.phone || "");
}

function startWithPhone(digits: string) {
  closeNewChat();
  emit("select", `${digits}@s.whatsapp.net`, `+${digits}`, "", "", digits);
}

function onNewChatEnter() {
  if (contactSuggestions.value.length === 1) {
    selectContact(contactSuggestions.value[0]);
  } else if (phoneOption.value) {
    startWithPhone(phoneOption.value);
  } else {
    const raw = newChatQuery.value.trim().replace(/[\s\-()]/g, "").replace(/^\+/, "");
    if (/^\d{7,15}$/.test(raw) && raw.startsWith("0")) {
      newChatError.value = "Use international format — include country code (e.g. 254720776486, not 0720776486)";
    } else {
      newChatError.value = "Select a contact or enter a valid phone number with country code";
    }
  }
}

// ── Conversations list ──────────────────────────────────────────────────────

// The endpoint returns one page at a time now, so the rendered list is
// accumulated here rather than being whatever the last response happened to
// contain. Search and the filter chips are server-side for the same reason:
// narrowing a single page in the browser would hide conversations that simply
// hadn't been fetched yet.
const PAGE_SIZE = 50;

const loadedList = ref<any[]>([]);
const hasMore = ref(false);
const loadingMore = ref(false);

const conversations = createResource({
  url: "helpdesk.integrations.wa.get_wa_conversations",
  auto: false,
});

function queryParams(offset: number) {
  return {
    line: props.line,
    search: search.value.trim(),
    conv_filter: activeFilter.value,
    // Favourites are per-browser (localStorage), so the server can only filter
    // on them if we hand them over.
    favourite_jids:
      activeFilter.value === "favourites"
        ? JSON.stringify([...favouriteJids.value])
        : "[]",
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
    loadedList.value = append ? [...loadedList.value, ...page] : page;
    hasMore.value = !!data?.has_more;
  } finally {
    if (token === requestToken) loadingMore.value = false;
  }
}

function reloadList() {
  fetchPage({ append: false });
}

// Used when a message arrives for a JID we have no row for: only a fetch can
// supply its name and company. Merging the first page instead of resetting to
// it keeps any older pages the agent has already loaded — otherwise an idle
// list would collapse back to page one every time a new contact wrote in.
async function mergeFirstPage() {
  const token = ++requestToken;
  const data = await conversations.submit(queryParams(0));
  if (token !== requestToken) return;
  const page = data?.conversations || [];
  const fetched = new Set(page.map((c: any) => c.jid));
  const older = loadedList.value.filter((c: any) => !fetched.has(c.jid));
  loadedList.value = [...page, ...older];
  if (!older.length) hasMore.value = !!data?.has_more;
}

function loadMore() {
  if (hasMore.value && !loadingMore.value) fetchPage({ append: true });
}

onMounted(reloadList);

watch(() => props.line, reloadList);
watch(activeFilter, reloadList);
// Debounced so a query isn't fired per keystroke.
watch(search, useDebounceFn(reloadList, 350));

// Opening a conversation marks its messages read server-side (BaileysChat calls
// mark_wa_messages_read), but this list's own fetched data never reflected that —
// only the badge for the currently-selected row was faked via a template check,
// so the count came back the moment you switched away, and "Unread" never
// actually dropped the chat. Mutate the underlying data so both stay correct.
watch(() => props.selectedJid, (jid) => {
  if (!jid) return;
  const target = loadedList.value.find((c: any) => c.jid === jid);
  if (target && target.unread_count) {
    loadedList.value = loadedList.value.map((c: any) =>
      c.jid === jid ? { ...c, unread_count: 0 } : c
    );
  }
});

// Counts every unread message on the line, not just the loaded page — the
// sidebar store already tracks exactly that.
const unreadCount = computed(
  () => waLinesStore.lines.find((l) => l.name === props.line)?.unread || 0
);

const markAllReadResource = createResource({
  url: "helpdesk.integrations.wa.mark_all_wa_messages_read",
  auto: false,
  onSuccess() {
    waLinesStore.reload();
    reloadList();
  },
});

function markAllRead() {
  markAllReadResource.submit({ line: props.line });
}

defineExpose({ reload: reloadList });
</script>
