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

    <!-- New chat input -->
    <div v-if="showNewChat" class="border-b border-outline-gray-2 bg-surface-white px-3 py-2.5">
      <div class="text-[11px] font-medium text-ink-gray-5 mb-1.5">Start new chat (phone number)</div>
      <div class="flex gap-1.5">
        <input
          v-model="newChatPhone"
          type="tel"
          placeholder="+254712345678"
          class="flex-1 rounded border border-outline-gray-3 px-2 py-1 text-xs text-ink-gray-9 focus:border-outline-gray-4 focus:outline-none"
          @keydown.enter="startNewChat"
          @keydown.esc="showNewChat = false; newChatPhone = ''"
        />
        <button
          class="rounded bg-green-600 px-2 py-1 text-xs font-medium text-white hover:bg-green-700"
          @click="startNewChat"
        >Go</button>
        <button
          class="rounded border border-outline-gray-3 px-2 py-1 text-xs text-ink-gray-6 hover:bg-surface-gray-2"
          @click="showNewChat = false; newChatPhone = ''"
        >✕</button>
      </div>
      <div v-if="newChatError" class="mt-1 text-[11px] text-red-500">{{ newChatError }}</div>
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
import { computed, ref, watch } from "vue";
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
const showNewChat = ref(false);
const newChatPhone = ref("");
const newChatError = ref("");

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

function startNewChat() {
  newChatError.value = "";
  const raw = newChatPhone.value.trim().replace(/[\s\-()]/g, "");
  if (!raw) { newChatError.value = "Enter a phone number"; return; }
  // Normalise: strip leading +
  const digits = raw.replace(/^\+/, "");
  if (!/^\d{7,15}$/.test(digits)) { newChatError.value = "Invalid phone number"; return; }
  const jid = `${digits}@s.whatsapp.net`;
  showNewChat.value = false;
  newChatPhone.value = "";
  emit("select", jid, `+${digits}`, "", "");
}

defineExpose({ reload: () => conversations.reload() });
</script>
