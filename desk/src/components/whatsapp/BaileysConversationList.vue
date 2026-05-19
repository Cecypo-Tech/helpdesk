<template>
  <div class="flex w-full h-full flex-col border-r border-outline-gray-2 bg-surface-gray-1">
    <div class="flex items-center justify-between border-b border-outline-gray-2 px-4 py-3">
      <h2 class="text-sm font-semibold text-ink-gray-9">WhatsApp</h2>
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

    <!-- New chat panel -->
    <div v-if="showNewChat" class="border-b border-outline-gray-2 bg-surface-white px-3 py-2.5">
      <div class="mb-1.5 flex items-center justify-between">
        <span class="text-[11px] font-medium text-ink-gray-5">New chat</span>
        <button class="text-ink-gray-4 hover:text-ink-gray-7" @click="closeNewChat">✕</button>
      </div>
      <input
        ref="newChatInputRef"
        v-model="newChatQuery"
        type="text"
        placeholder="Search contacts or enter phone..."
        class="w-full rounded border border-outline-gray-3 px-2 py-1.5 text-xs text-ink-gray-9 focus:border-outline-gray-4 focus:outline-none"
        @keydown.esc="closeNewChat"
        @keydown.enter="onNewChatEnter"
      />
      <div v-if="newChatError" class="mt-1 text-[11px] text-red-500">{{ newChatError }}</div>

      <!-- Contact suggestions -->
      <div v-if="contactSuggestions.length || phoneOption" class="mt-1.5 max-h-48 overflow-y-auto rounded border border-outline-gray-2 bg-surface-white shadow-sm">
        <!-- Existing contacts -->
        <button
          v-for="c in contactSuggestions"
          :key="c.jid"
          class="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-surface-gray-1"
          @click="selectContact(c)"
        >
          <div class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-100 text-[10px] font-bold text-green-700">
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
          class="flex w-full items-center gap-2 border-t border-outline-gray-2 px-2.5 py-1.5 text-left hover:bg-surface-gray-1"
          @click="startWithPhone(phoneOption)"
        >
          <div class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-[10px] text-ink-gray-5">
            #
          </div>
          <div class="text-xs text-ink-gray-6">Start chat with <span class="font-medium text-ink-gray-8">+{{ phoneOption }}</span></div>
        </button>
      </div>

      <!-- Empty state when typing but no matches -->
      <div
        v-else-if="newChatQuery.trim() && !contactsResource.loading"
        class="mt-1.5 rounded border border-outline-gray-2 bg-surface-white px-3 py-2 text-[11px] text-ink-gray-5"
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

    <div class="flex-1 overflow-y-auto">
      <div v-if="conversations.loading && !conversations.data" class="flex justify-center py-8">
        <LoadingIndicator :scale="5" class="text-ink-gray-5" />
      </div>
      <div
        v-else-if="!filteredList.length"
        class="py-10 text-center text-xs text-ink-gray-5"
      >
        {{ search ? "No results" : "No conversations" }}
      </div>
      <BaileysConversationItem
        v-for="conv in filteredList"
        :key="conv.jid"
        :jid="conv.jid"
        :displayName="conv.display_name"
        :company="conv.company"
        :assignedTeam="conv.assigned_team"
        :isGroup="conv.is_group"
        :lastMessage="conv.last_message"
        :lastMessageTime="conv.last_message_time"
        :lastDirection="conv.last_direction"
        :contentType="conv.content_type"
        :hasUnread="isUnread(conv)"
        :selected="conv.jid === selectedJid"
        @select="(jid, name, company, team) => $emit('select', jid, name, company, team)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator } from "frappe-ui";
import { computed, nextTick, ref, watch } from "vue";
import BaileysConversationItem from "./BaileysConversationItem.vue";

const props = defineProps<{ selectedJid: string | null }>();
const emit = defineEmits<{
  (e: "select", jid: string, displayName: string, company: string, assignedTeam: string): void;
}>();

const search = ref("");
const lastReadMap = ref<Record<string, number>>({});

watch(() => props.selectedJid, (jid) => {
  if (jid) lastReadMap.value[jid] = Date.now();
});

// ── New chat ────────────────────────────────────────────────────────────────

const showNewChat = ref(false);
const newChatQuery = ref("");
const newChatError = ref("");
const newChatInputRef = ref<HTMLInputElement | null>(null);

const contactsResource = createResource({
  url: "helpdesk.integrations.baileys.search_baileys_contacts",
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
  return /^\d{7,15}$/.test(raw) ? raw : null;
});

function closeNewChat() {
  showNewChat.value = false;
  newChatQuery.value = "";
  newChatError.value = "";
}

function selectContact(c: { jid: string; custom_name: string; phone: string; company: string }) {
  closeNewChat();
  emit("select", c.jid, c.custom_name || c.phone, c.company || "", "");
}

function startWithPhone(digits: string) {
  closeNewChat();
  emit("select", `${digits}@s.whatsapp.net`, `+${digits}`, "", "");
}

function onNewChatEnter() {
  if (contactSuggestions.value.length === 1) {
    selectContact(contactSuggestions.value[0]);
  } else if (phoneOption.value) {
    startWithPhone(phoneOption.value);
  } else {
    newChatError.value = "Select a contact or enter a valid phone number";
  }
}

// ── Conversations list ──────────────────────────────────────────────────────

const conversations = createResource({
  url: "helpdesk.integrations.baileys.get_baileys_conversations",
  auto: true,
});

const filteredList = computed(() => {
  const list: any[] = conversations.data || [];
  if (!search.value.trim()) return list;
  const q = search.value.toLowerCase();
  return list.filter(
    (c) =>
      (c.display_name || "").toLowerCase().includes(q) ||
      (c.last_message || "").toLowerCase().includes(q)
  );
});

function isUnread(conv: any): boolean {
  if (conv.last_direction !== "Incoming") return false;
  if (conv.jid === props.selectedJid) return false;
  const msgTime = new Date(conv.last_message_time).getTime();
  const readTime = lastReadMap.value[conv.jid];
  if (readTime) return msgTime > readTime;
  const stored = localStorage.getItem(`baileys_last_read_${conv.jid}`);
  if (!stored) return true;
  return msgTime > new Date(stored).getTime();
}

defineExpose({ reload: () => conversations.reload() });
</script>
