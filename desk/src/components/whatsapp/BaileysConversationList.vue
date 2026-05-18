<template>
  <div class="flex w-full h-full flex-col border-r border-outline-gray-2 bg-surface-gray-1">
    <div class="border-b border-outline-gray-2 px-4 py-3">
      <h2 class="text-sm font-semibold text-ink-gray-9">WhatsApp</h2>
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
        :isGroup="conv.is_group"
        :lastMessage="conv.last_message"
        :lastMessageTime="conv.last_message_time"
        :lastDirection="conv.last_direction"
        :contentType="conv.content_type"
        :hasUnread="isUnread(conv)"
        :selected="conv.jid === selectedJid"
        @select="(jid, name) => $emit('select', jid, name)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator } from "frappe-ui";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { globalStore } from "@/stores/globalStore";
import BaileysConversationItem from "./BaileysConversationItem.vue";

const props = defineProps<{ selectedJid: string | null }>();
const emit = defineEmits<{ (e: "select", jid: string, displayName: string): void }>();

const { $socket } = globalStore();
const search = ref("");

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
  const key = `baileys_last_read_${conv.jid}`;
  const lastRead = localStorage.getItem(key);
  if (!lastRead) return true;
  return new Date(conv.last_message_time) > new Date(lastRead);
}

function handleNewMessage() {
  conversations.reload();
}

onMounted(() => {
  $socket.on("helpdesk:baileys-message", handleNewMessage);
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:baileys-message", handleNewMessage);
});
</script>
