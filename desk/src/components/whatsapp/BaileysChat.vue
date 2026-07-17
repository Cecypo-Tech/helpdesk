<template>
  <div
    v-if="!jid"
    class="flex flex-1 flex-col items-center justify-center text-ink-gray-4"
  >
    <WhatsAppIcon class="mb-3 h-14 w-14" />
    <p class="text-sm">Select a conversation</p>
  </div>

  <div v-else class="flex flex-1 min-h-0 flex-col overflow-hidden">
    <!-- Header -->
    <div class="flex items-center gap-3 border-b border-outline-gray-2 bg-surface-gray-1 px-4 py-2.5">
      <!-- Mobile back button -->
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
          {{ displayName || jid.split("@")[0] }}
        </div>
        <!-- Phone number row — DM chats only -->
        <div v-if="!isGroup" class="flex items-center gap-1.5">
          <span v-if="contactPhone" class="text-[11px] font-medium text-ink-gray-6">{{ contactPhone }}</span>
          <button
            v-if="contactPhone"
            class="text-ink-gray-3 hover:text-ink-gray-6"
            title="Copy phone"
            @click.stop="copyContactPhone"
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
          </button>
          <span v-else class="text-[10px] italic text-ink-gray-3">no phone (enter via ✎)</span>
        </div>
        <div class="flex flex-wrap items-center gap-1.5">
          <span v-if="company" class="truncate text-[11px] text-ink-gray-5">{{ company }}</span>
          <span
            v-if="assignedTeam"
            class="shrink-0 rounded-full bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-700 dark:bg-blue-900 dark:text-blue-300"
          >{{ assignedTeam }}</span>
        </div>
      </div>
      <!-- Members button (groups only) -->
      <button
        v-if="isGroup"
        class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-ink-gray-4 hover:bg-surface-gray-2 hover:text-ink-gray-7"
        :class="{ 'bg-surface-gray-2 text-ink-gray-8': showMembers }"
        title="Group members"
        @click="toggleMembers"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
          <circle cx="9" cy="7" r="4"/>
          <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
          <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
        </svg>
      </button>
      <!-- Customer notes button (shown when customer is linked) -->
      <button
        v-if="company"
        class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full hover:bg-surface-gray-2"
        :class="showNotes ? 'bg-amber-100 text-amber-600 dark:bg-amber-900/40' : 'text-ink-gray-4 hover:text-amber-600'"
        title="Customer notes"
        @click="toggleNotes"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/>
          <line x1="16" y1="17" x2="8" y2="17"/>
          <polyline points="10 9 9 9 8 9"/>
        </svg>
      </button>
      <!-- Edit contact button -->
      <button
        class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-ink-gray-4 hover:bg-surface-gray-2 hover:text-ink-gray-7"
        title="Edit contact / customer"
        @click="openEdit"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
        </svg>
      </button>
      <!-- Tasks panel toggle -->
      <div class="relative shrink-0">
        <button
          class="flex h-7 w-7 items-center justify-center rounded-full text-ink-gray-4 hover:bg-surface-gray-2 hover:text-ink-gray-7"
          :class="{ 'bg-blue-50 text-blue-600': tasksOpen }"
          title="Tasks & tickets"
          @click="emit('toggleTasks')"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
          </svg>
        </button>
        <span
          v-if="(tasksCount || 0) + (ticketsCount || 0) > 0"
          class="absolute -right-1 -top-1 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-blue-500 px-0.5 text-[9px] font-bold leading-none text-white"
        >{{ ((tasksCount || 0) + (ticketsCount || 0)) > 99 ? '99+' : (tasksCount || 0) + (ticketsCount || 0) }}</span>
      </div>
    </div>

    <!-- Group members panel -->
    <div v-if="showMembers && isGroup" class="border-b border-outline-gray-2 bg-surface-gray-1">
      <div class="flex items-center justify-between px-4 py-2">
        <span class="text-[11px] font-semibold text-ink-gray-6">
          Members{{ participantsResource.data ? ` (${participantsResource.data.length})` : '' }}
        </span>
        <button class="text-[11px] text-ink-gray-4 hover:text-ink-gray-7" @click="showMembers = false">✕</button>
      </div>
      <div v-if="participantsResource.loading" class="px-4 py-3 text-center text-xs text-ink-gray-5">Loading…</div>
      <div v-else-if="!participantsResource.data?.length" class="px-4 py-3 text-center text-xs text-ink-gray-5">No members found</div>
      <div v-else class="max-h-52 overflow-y-auto divide-y divide-outline-gray-1">
        <div
          v-for="p in participantsResource.data"
          :key="p.jid"
          class="flex items-center gap-2.5 px-4 py-2"
        >
          <div
            class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white"
            :style="{ background: memberColor(p.jid) }"
          >{{ (p.name || p.phone || '?')[0].toUpperCase() }}</div>
          <div class="min-w-0 flex-1">
            <div class="truncate text-xs font-medium text-ink-gray-8">
              {{ p.name || (p.phone ? '+' + p.phone : p.jid.split('@')[0]) }}
              <span v-if="p.isAdmin" class="ml-1 rounded-full bg-blue-100 px-1.5 py-0.5 text-[9px] font-medium text-blue-700">admin</span>
            </div>
            <div v-if="p.phone" class="flex items-center gap-1 text-[11px] text-ink-gray-5">
              <span>+{{ p.phone }}</span>
              <button
                class="rounded p-0.5 text-ink-gray-4 hover:bg-surface-gray-2 hover:text-ink-gray-7"
                title="Copy phone"
                @click="copyPhone(p.phone)"
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                </svg>
              </button>
            </div>
            <div v-else class="text-[11px] text-ink-gray-4 italic">LID: {{ p.jid.split('@')[0] }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Customer notes panel -->
    <div v-if="showNotes && company" class="border-b border-outline-gray-2 bg-amber-50 dark:bg-surface-gray-2 px-4 py-3">
      <div class="mb-1.5 flex items-center justify-between">
        <span class="text-[11px] font-semibold text-amber-700 dark:text-amber-400">Customer Notes · {{ company }}</span>
        <button class="text-ink-gray-4 hover:text-ink-gray-7" @click="showNotes = false">✕</button>
      </div>
      <div v-if="customerNotesResource.loading" class="text-xs text-ink-gray-5">Loading…</div>
      <div v-else-if="!notesText" class="text-xs italic text-ink-gray-4">No notes for this customer.</div>
      <pre v-else class="whitespace-pre-wrap text-xs leading-relaxed text-ink-gray-8">{{ notesText }}</pre>
    </div>

    <!-- Inline contact edit form -->
    <div v-if="editingContact" class="border-b border-outline-gray-2 bg-surface-gray-1 px-4 py-3">
      <div class="flex items-end gap-2">
        <div class="flex-1 space-y-1.5">
          <div>
            <label class="mb-0.5 block text-[11px] font-medium text-ink-gray-5">Display Name</label>
            <input
              v-model="editName"
              type="text"
              placeholder="Contact name..."
              class="w-full rounded border border-outline-gray-3 bg-surface-white px-2 py-1 text-xs text-ink-gray-9 focus:border-outline-gray-4 focus:outline-none"
              @keydown.enter="saveContact"
              @keydown.esc="editingContact = false"
            />
          </div>
          <div>
            <label class="mb-0.5 block text-[11px] font-medium text-ink-gray-5">Phone</label>
            <input
              v-model="editPhone"
              type="text"
              placeholder="e.g. 254712345678"
              class="w-full rounded border border-outline-gray-3 bg-surface-white px-2 py-1 text-xs text-ink-gray-9 focus:border-outline-gray-4 focus:outline-none"
              @keydown.enter="saveContact"
              @keydown.esc="editingContact = false"
            />
          </div>
          <div class="relative">
            <label class="mb-0.5 block text-[11px] font-medium text-ink-gray-5">Customer</label>
            <input
              v-model="editCustomerQuery"
              type="text"
              placeholder="Search customers…"
              autocomplete="off"
              class="w-full rounded border border-outline-gray-3 bg-surface-white px-2 py-1 pr-6 text-xs text-ink-gray-9 focus:border-outline-gray-4 focus:outline-none"
              @input="onCustomerInput"
              @keydown.enter.prevent="pickFirstCustomer"
              @keydown.esc="customerDropdown = false"
            />
            <button
              v-if="editCustomerQuery"
              class="absolute right-1.5 top-[18px] text-ink-gray-3 hover:text-ink-gray-6"
              tabindex="-1"
              @mousedown.prevent="clearCustomer"
            >✕</button>
            <div
              v-if="customerDropdown && customerResults.length"
              class="absolute z-20 mt-0.5 max-h-36 w-full overflow-y-auto rounded border border-outline-gray-2 bg-surface-white shadow-sm"
            >
              <button
                v-for="c in customerResults"
                :key="c.name"
                class="flex w-full flex-col px-2.5 py-1.5 text-left hover:bg-surface-gray-2"
                @mousedown.prevent="selectCustomer(c)"
              >
                <span class="text-xs font-medium text-ink-gray-8">{{ c.customer_name }}</span>
                <span v-if="c.domain" class="text-[10px] text-ink-gray-5">{{ c.domain }}</span>
              </button>
            </div>
          </div>
          <div>
            <label class="mb-0.5 block text-[11px] font-medium text-ink-gray-5">Assigned Team</label>
            <select
              v-model="editTeam"
              class="w-full rounded border border-outline-gray-3 bg-surface-white px-2 py-1 text-xs text-ink-gray-9 focus:border-outline-gray-4 focus:outline-none"
            >
              <option value="">— All agents —</option>
              <option v-for="t in (teamsResource.data || [])" :key="t.name" :value="t.name">{{ t.name }}</option>
            </select>
          </div>
        </div>
        <div class="flex gap-1.5 pb-0.5">
          <button
            class="rounded-lg bg-green-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-green-700 disabled:opacity-50"
            :disabled="savingContact"
            @click="saveContact"
          >Save</button>
          <button
            class="rounded-lg border border-outline-gray-3 px-2.5 py-1 text-xs text-ink-gray-6 hover:bg-surface-gray-2"
            @click="editingContact = false"
          >Cancel</button>
        </div>
      </div>
    </div>

    <div ref="messagesContainer" class="flex-1 overflow-y-auto bg-[#e5ddd5] dark:bg-surface-gray-2 px-5 py-4">
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
            :isGroup="isGroup"
            :mentionMap="mentionMap"
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

    <BaileysReplyBox
      ticketId=""
      :jid="jid"
      :line="line"
      :replyTo="replyingTo"
      @sent="onMessageSent"
      @clearReply="replyingTo = null"
      @optimistic="addOptimistic"
      @optimistic-resolve="resolveOptimistic"
      @optimistic-remove="removeOptimistic"
    />

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

<script setup lang="ts">
import { call, createResource, LoadingIndicator, toast } from "frappe-ui";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { globalStore } from "@/stores/globalStore";
import { foldReactions } from "@/utils/waReactions";
import ConfirmDialog from "@/components/ConfirmDialog.vue";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";
import WhatsAppBubble from "./WhatsAppBubble.vue";
import BaileysReplyBox from "./BaileysReplyBox.vue";

interface HdCustomer { name: string; customer_name: string; domain?: string }

const props = defineProps<{
  jid: string | null;
  displayName: string;
  company?: string;
  assignedTeam?: string;
  phone?: string;
  line?: string;
  showBack?: boolean;
  tasksOpen?: boolean;
  tasksCount?: number;
  ticketsCount?: number;
}>();

const emit = defineEmits<{
  (e: "contactSaved", data: { custom_name: string; company: string; assigned_team: string; phone: string }): void;
  (e: "back"): void;
  (e: "toggleTasks"): void;
}>();

const messagesContainer = ref<HTMLElement | null>(null);
const replyingTo = ref<Record<string, any> | null>(null);

const isGroup = computed(() => !!props.jid?.endsWith("@g.us"));

// ── Customer notes panel ──────────────────────────────────────────────────────
const showNotes = ref(false);

const customerNotesResource = createResource({
  url: "helpdesk.integrations.wa.get_customer_notes",
  auto: false,
});
const notesText = computed(() => (customerNotesResource.data as { notes?: string } | null)?.notes || "");

function toggleNotes() {
  if (!showNotes.value) {
    showNotes.value = true;
    if (props.company) customerNotesResource.submit({ customer: props.company });
  } else {
    showNotes.value = false;
  }
}

watch(() => props.company, (newCustomer) => {
  if (showNotes.value) {
    if (newCustomer) customerNotesResource.submit({ customer: newCustomer });
    else showNotes.value = false;
  }
});

// ── Group members panel ───────────────────────────────────────────────────────
const showMembers = ref(false);

const participantsResource = createResource({
  url: "helpdesk.integrations.wa.get_wa_group_participants",
  auto: false,
});

const mentionMap = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {};
  for (const p of (participantsResource.data || []) as Array<{ jid: string; phone: string; name: string }>) {
    if (!p.name) continue;
    if (p.phone) map[p.phone] = p.name;
    if (p.jid?.endsWith("@lid")) {
      const lid = p.jid.split("@")[0];
      if (lid) map[lid] = p.name;
    }
  }
  return map;
});

function toggleMembers() {
  if (!showMembers.value) {
    showMembers.value = true;
    if (!participantsResource.data && props.jid) {
      participantsResource.submit({ jid: props.jid, line: props.line || "" });
    }
  } else {
    showMembers.value = false;
  }
}

const MEMBER_COLORS = ["#128c7e", "#7e57c2", "#e67e22", "#c0392b", "#2980b9", "#27ae60", "#8e44ad"];
function memberColor(jid: string) {
  let hash = 0;
  for (const ch of jid) hash = ((hash * 31) + ch.charCodeAt(0)) & 0x7fffffff;
  return MEMBER_COLORS[hash % MEMBER_COLORS.length];
}

function copyPhone(phone: string) {
  navigator.clipboard.writeText(`+${phone}`).then(() => toast.success(`+${phone} copied`));
}

function copyContactPhone() {
  if (contactPhone.value) {
    navigator.clipboard.writeText(contactPhone.value).then(() => toast.success(`${contactPhone.value} copied`));
  }
}

// Reset panels when switching conversations
watch(() => props.jid, (newJid) => {
  showMembers.value = false;
  participantsResource.data = null;
  showNotes.value = false;
  customerNotesResource.data = null;
  // Eagerly fetch participants for groups so mentionMap resolves without opening the panel
  if (newJid?.endsWith("@g.us") && props.line) {
    participantsResource.submit({ jid: newJid, line: props.line });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
const editingContact = ref(false);
const editName = ref("");
const editPhone = ref("");
const editCustomer = ref(""); // stored Link value (HD Customer name)
const editCustomerQuery = ref(""); // display text in the autocomplete input
const customerResults = ref<HdCustomer[]>([]);
const customerDropdown = ref(false);
let customerSearchTimer: ReturnType<typeof setTimeout> | null = null;
const editTeam = ref("");
const savingContact = ref(false);

const customerSearchResource = createResource({
  url: "helpdesk.integrations.wa.get_hd_customers",
  auto: false,
  onSuccess(data: HdCustomer[]) {
    customerResults.value = data;
    customerDropdown.value = data.length > 0;
  },
});

function onCustomerInput() {
  editCustomer.value = editCustomerQuery.value.trim();
  if (customerSearchTimer) clearTimeout(customerSearchTimer);
  customerSearchTimer = setTimeout(() => {
    customerSearchResource.submit({ query: editCustomerQuery.value });
  }, 280);
}

function selectCustomer(c: HdCustomer) {
  editCustomer.value = c.name;
  editCustomerQuery.value = c.customer_name;
  customerDropdown.value = false;
  customerResults.value = [];
}

function pickFirstCustomer() {
  if (customerResults.value.length) selectCustomer(customerResults.value[0]);
}

function clearCustomer() {
  editCustomer.value = "";
  editCustomerQuery.value = "";
  customerDropdown.value = false;
  customerResults.value = [];
}

const PAGE_SIZE = 40;
const loadingMore = ref(false);

// Pages accumulate here rather than living in `messages.data`, because a chat's
// history is fetched a page at a time and each refresh only re-fetches the
// newest page. Kept sorted oldest → newest.
const loadedMessages = ref<Record<string, any>[]>([]);
// Messages quoted by a reply whose target sits outside the loaded pages. Needed
// to render the quoted preview, but never rendered as bubbles themselves.
const replyTargets = ref<Record<string, any>[]>([]);
// message_ids we've already tried to resolve on demand (declared here so the
// immediate jid watcher below can clear it without hitting the TDZ).
const attemptedReplyTargets = new Set<string>();
const hasMore = ref(false);

const messages = createResource({
  url: "helpdesk.integrations.wa.get_whatsapp_messages",
  auto: false,
});

// Cross-line duplicates share a message_id, so key on that where present.
function messageKey(m: Record<string, any>) {
  return m.message_id || m.name;
}

// Refreshes re-send the same targets, so dedupe rather than growing forever.
function mergeReplyTargets(existing: Record<string, any>[], incoming: Record<string, any>[]) {
  const byId = new Map<string, Record<string, any>>();
  for (const m of [...existing, ...incoming]) byId.set(messageKey(m), m);
  return [...byId.values()];
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

const markReadResource = createResource({
  url: "helpdesk.integrations.wa.mark_wa_messages_read",
});

const sendReactionResource = createResource({
  url: "helpdesk.integrations.wa.send_wa_reaction",
  onSuccess() {
    loadMessages();
  },
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send reaction");
  },
});

const saveContactResource = createResource({
  url: "helpdesk.integrations.wa.save_whatsapp_contact",
  onSuccess(data: { custom_name: string; company: string; assigned_team: string; phone: string }) {
    savingContact.value = false;
    editingContact.value = false;
    emit("contactSaved", data);
    toast.success("Contact saved");
  },
  onError(e: any) {
    savingContact.value = false;
    toast.error(e?.messages?.[0] || "Failed to save contact");
  },
});

const teamsResource = createResource({
  url: "helpdesk.integrations.wa.get_hd_teams",
  auto: true,
});

async function loadMore() {
  if (loadingMore.value || !hasMore.value || !props.jid) return;
  loadingMore.value = true;
  const container = messagesContainer.value;
  const prevScrollHeight = container?.scrollHeight ?? 0;
  // Page back from the oldest real message. Reactions are excluded because the
  // server pages over real messages only, so a reaction is never the boundary.
  const oldest = loadedMessages.value.find((m) => m.content_type !== "reaction");
  try {
    const data = await call("helpdesk.integrations.wa.get_whatsapp_messages", {
      jid: props.jid,
      limit: PAGE_SIZE,
      before: oldest?.creation,
      before_name: oldest?.name,
    });
    mergeMessages(data?.messages || []);
    replyTargets.value = mergeReplyTargets(replyTargets.value, data?.reply_targets || []);
    hasMore.value = !!data?.has_more;
    await nextTick();
    if (container) {
      // Keep scroll position so user stays at where they were
      container.scrollTop = container.scrollHeight - prevScrollHeight;
    }
  } catch {
    toast.error("Could not load older messages");
  } finally {
    // Released only after the scroll is restored — the messageList watcher skips
    // its jump-to-bottom while this is set.
    loadingMore.value = false;
  }
}

function openEdit() {
  editName.value = props.displayName || "";
  editPhone.value = props.phone || "";
  editCustomer.value = props.company || "";
  editCustomerQuery.value = props.company || "";
  customerDropdown.value = false;
  customerResults.value = [];
  editTeam.value = props.assignedTeam || "";
  editingContact.value = true;
}

function saveContact() {
  if (!props.jid || savingContact.value) return;
  savingContact.value = true;
  saveContactResource.submit({
    jid: props.jid,
    custom_name: editName.value.trim(),
    phone: editPhone.value.trim(),
    company: editCustomer.value || editCustomerQuery.value.trim(),
    assigned_team: editTeam.value,
  });
}

// `fresh` marks a conversation switch (as opposed to a refresh triggered by an
// incoming/sent message). Only a fresh load may reset `hasMore`: a refresh
// re-fetches the newest page, whose has_more says nothing about how far back the
// user has already paged.
async function loadMessages({ fresh = false } = {}) {
  if (!props.jid) return;
  const jid = props.jid;
  const data = await messages.submit({ jid, limit: PAGE_SIZE });
  // Ignore a response that landed after the user moved to another conversation.
  if (jid !== props.jid || !data) return;
  mergeMessages(data.messages || []);
  replyTargets.value = fresh
    ? data.reply_targets || []
    : mergeReplyTargets(replyTargets.value, data.reply_targets || []);
  if (fresh) hasMore.value = !!data.has_more;
  markReadResource.submit({ jid });
  localStorage.setItem(`baileys_last_read_${jid}`, new Date().toISOString());
}

watch(
  () => props.jid,
  (newJid) => {
    if (newJid) {
      replyingTo.value = null;
      // Drop the previous conversation before fetching. createResource keeps
      // `.data` until the new request resolves, so without this the old chat
      // stays rendered (and the loading spinner can't show) until it lands.
      messages.reset();
      loadedMessages.value = [];
      replyTargets.value = [];
      hasMore.value = false;
      attemptedReplyTargets.clear();
      loadMessages({ fresh: true });
    }
  },
  { immediate: true }
);

const allMessages = computed<Record<string, any>[]>(() => loadedMessages.value);

// The server pages history, so everything loaded is meant to be on screen.
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

const contactPhone = computed(() => {
  const p = props.phone;
  if (!p) return null;
  return p.startsWith("+") ? p : `+${p}`;
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
  loadMessages();
  scrollToBottom();
}

const retryResource = createResource({
  url: "helpdesk.integrations.wa.retry_wa_message",
  onSuccess() {
    loadMessages();
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
    loadMessages();
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
    loadMessages();
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

// ── Optimistic send ─────────────────────────────────────────────────────────
// The reply box inserts a pending bubble before its upload/send completes, then
// resolves it to the real message_id (so the reload dedupes against it via
// mergeMessages' message_id key) or removes it on a pre-send failure.
function addOptimistic(msg: Record<string, any>) {
  loadedMessages.value = [...loadedMessages.value, msg];
  scrollToBottom();
}

function resolveOptimistic(payload: { name: string; realName: string; message_id: string; status: string }) {
  const msg = loadedMessages.value.find((m) => m.name === payload.name);
  if (!msg) return;
  // If the real row already arrived via the reload, drop the pending bubble
  // rather than stamping it (which would leave a duplicate). Match on message_id,
  // or on the real docname when the send failed (empty message_id).
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
// reply_targets is fetched by message_id so its preview resolves instead of
// falling back to the "earlier message" placeholder.
// (`attemptedReplyTargets` is declared above, before the jid watcher that clears it.)

async function resolveMissingReplyTargets() {
  const jid = props.jid;
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

// Prepending older messages also grows messageList — don't yank the user to the
// bottom when that happens; loadMore restores their scroll position itself.
watch(messageList, () => { if (!loadingMore.value) scrollToBottom(); });

function handleBaileysMessage(data: { jid?: string }) {
  if (data.jid && props.jid && data.jid === props.jid) {
    loadMessages();
  }
}

function handleEditUpdate(data: { message_id: string; new_text: string; name: string }) {
  const msg = loadedMessages.value.find(
    (m) => m.message_id === data.message_id || m.name === data.name
  );
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

onMounted(() => {
  const { $socket } = globalStore();
  $socket.on("helpdesk:baileys-message", handleBaileysMessage);
  $socket.on("helpdesk:whatsapp-message-edit", handleEditUpdate);
  $socket.on("helpdesk:whatsapp-message-delete", handleDeleteUpdate);
  if (props.jid?.endsWith("@g.us") && props.line && !participantsResource.data) {
    participantsResource.submit({ jid: props.jid, line: props.line });
  }
});

onBeforeUnmount(() => {
  const { $socket } = globalStore();
  $socket.off("helpdesk:baileys-message", handleBaileysMessage);
  $socket.off("helpdesk:whatsapp-message-edit", handleEditUpdate);
  $socket.off("helpdesk:whatsapp-message-delete", handleDeleteUpdate);
});

defineExpose({
  refresh() {
    loadMessages();
    scrollToBottom();
  },
  patchMessageStatus(messageId: string, status: string) {
    const msg = loadedMessages.value.find((m) => m.message_id === messageId);
    if (msg) msg.status = status;
  },
});
</script>
