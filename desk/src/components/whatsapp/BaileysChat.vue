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
      <!-- Edit contact button -->
      <button
        class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-ink-gray-4 hover:bg-surface-gray-2 hover:text-ink-gray-7"
        title="Edit contact name / company"
        @click="openEdit"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
        </svg>
      </button>
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
            <div v-if="p.phone" class="text-[11px] text-ink-gray-5">+{{ p.phone }}</div>
            <div v-else class="text-[11px] text-ink-gray-4 italic">phone unavailable (LID)</div>
          </div>
          <button
            v-if="p.phone"
            class="shrink-0 rounded p-1 text-ink-gray-4 hover:bg-surface-gray-2 hover:text-ink-gray-7"
            title="Copy phone"
            @click="copyPhone(p.phone)"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
          </button>
        </div>
      </div>
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
          <div>
            <label class="mb-0.5 block text-[11px] font-medium text-ink-gray-5">Company</label>
            <input
              v-model="editCompany"
              type="text"
              placeholder="Company name..."
              class="w-full rounded border border-outline-gray-3 bg-surface-white px-2 py-1 text-xs text-ink-gray-9 focus:border-outline-gray-4 focus:outline-none"
              @keydown.enter="saveContact"
              @keydown.esc="editingContact = false"
            />
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
      <div v-if="messages.loading && !messages.data" class="flex justify-center py-10">
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
import { computed, nextTick, ref, watch } from "vue";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";
import WhatsAppBubble from "./WhatsAppBubble.vue";
import BaileysReplyBox from "./BaileysReplyBox.vue";

const props = defineProps<{
  jid: string | null;
  displayName: string;
  company?: string;
  assignedTeam?: string;
  phone?: string;
}>();

const emit = defineEmits<{
  (e: "contactSaved", data: { custom_name: string; company: string; assigned_team: string; phone: string }): void;
}>();

const messagesContainer = ref<HTMLElement | null>(null);
const replyingTo = ref<Record<string, any> | null>(null);

const isGroup = computed(() => !!props.jid?.endsWith("@g.us"));

// ── Group members panel ───────────────────────────────────────────────────────
const showMembers = ref(false);

const participantsResource = createResource({
  url: "helpdesk.integrations.baileys.get_group_participants",
  auto: false,
});

function toggleMembers() {
  if (!showMembers.value) {
    showMembers.value = true;
    if (!participantsResource.data && props.jid) {
      participantsResource.submit({ jid: props.jid });
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

// Reset members panel when switching conversations
watch(() => props.jid, () => {
  showMembers.value = false;
  participantsResource.data = null;
});

// ─────────────────────────────────────────────────────────────────────────────
const editingContact = ref(false);
const editName = ref("");
const editPhone = ref("");
const editCompany = ref("");
const editTeam = ref("");
const savingContact = ref(false);

const PAGE_SIZE = 40;
const visibleCount = ref(PAGE_SIZE);
const loadingMore = ref(false);


const messages = createResource({
  url: "helpdesk.integrations.baileys.get_baileys_messages",
  auto: false,
});

const markReadResource = createResource({
  url: "helpdesk.integrations.baileys.mark_baileys_messages_read",
});

const sendReactionResource = createResource({
  url: "helpdesk.integrations.baileys.send_baileys_reaction",
  onSuccess() {
    loadMessages();
  },
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send reaction");
  },
});

const saveContactResource = createResource({
  url: "helpdesk.integrations.baileys.save_baileys_contact",
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
  url: "helpdesk.integrations.baileys.get_hd_teams",
  auto: true,
});

function loadMore() {
  if (loadingMore.value || !hasMore.value) return;
  loadingMore.value = true;
  const container = messagesContainer.value;
  const prevScrollHeight = container?.scrollHeight ?? 0;
  visibleCount.value += PAGE_SIZE;
  nextTick(() => {
    loadingMore.value = false;
    if (container) {
      // Keep scroll position so user stays at where they were
      container.scrollTop = container.scrollHeight - prevScrollHeight;
    }
  });
}

function openEdit() {
  editName.value = props.displayName || "";
  editPhone.value = props.phone || "";
  editCompany.value = props.company || "";
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
    company: editCompany.value.trim(),
    assigned_team: editTeam.value,
  });
}

function loadMessages() {
  if (props.jid) {
    messages.submit({ jid: props.jid });
    markReadResource.submit({ jid: props.jid });
    localStorage.setItem(`baileys_last_read_${props.jid}`, new Date().toISOString());
  }
}

watch(
  () => props.jid,
  (newJid) => {
    if (newJid) {
      replyingTo.value = null;
      visibleCount.value = PAGE_SIZE;
      loadMessages();
    }
  },
  { immediate: true }
);

const allMessages = computed<Record<string, any>[]>(() => messages.data || []);

const allNonReactions = computed(() =>
  allMessages.value.filter((m) => m.content_type !== "reaction")
);

const hasMore = computed(() => visibleCount.value < allNonReactions.value.length);

const messageList = computed(() =>
  allNonReactions.value.slice(-visibleCount.value)
);

const messageByMsgId = computed(() => {
  const map: Record<string, Record<string, any>> = {};
  for (const m of allMessages.value) {
    if (m.message_id) map[m.message_id] = m;
  }
  return map;
});

const reactionsMap = computed(() => {
  const map: Record<string, Array<{ emoji: string; type: string; sender: string }>> = {};
  for (const m of allMessages.value) {
    if (m.content_type === "reaction" && m.reply_to_message_id && m.message) {
      if (!map[m.reply_to_message_id]) map[m.reply_to_message_id] = [];
      const sender = m.type === "Outgoing"
        ? (m.sender_full_name || m.sender_name || "You")
        : (m.profile_name || m.sender_name || "Customer");
      map[m.reply_to_message_id].push({ emoji: m.message, type: m.type, sender });
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

watch(messageList, () => { scrollToBottom(); });

defineExpose({
  refresh() {
    loadMessages();
    scrollToBottom();
  },
  patchMessageStatus(messageId: string, status: string) {
    const list: Record<string, any>[] = messages.data || [];
    const msg = list.find((m) => m.message_id === messageId);
    if (msg) msg.status = status;
  },
});
</script>
