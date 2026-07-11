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

    <div class="flex-1 overflow-y-auto">
      <div v-if="conversations.loading && !conversations.data" class="flex justify-center py-8">
        <LoadingIndicator :scale="5" class="text-ink-gray-5" />
      </div>
      <div
        v-else-if="!filteredList.length"
        class="py-10 text-center text-xs text-ink-gray-5"
      >
        {{ search ? "No results" : "No WhatsApp Business conversations yet" }}
      </div>
      <WhatsAppConversationItem
        v-for="conv in filteredList"
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
        @select="(phone, name) => $emit('select', phone, name)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator } from "frappe-ui";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { globalStore } from "@/stores/globalStore";
import WhatsAppConversationItem from "./WhatsAppConversationItem.vue";

const props = defineProps<{ selectedPhone: string | null }>();
const emit = defineEmits<{
  (e: "select", phone: string, displayName: string): void;
}>();

const search = ref("");
const lastReadMap = ref<Record<string, number>>({});

const conversations = createResource({
  url: "helpdesk.integrations.wa.get_whatsapp_conversations",
  auto: true,
});

function onWhatsAppMessage() {
  conversations.reload();
}

onMounted(() => {
  const { $socket } = globalStore();
  $socket.on("helpdesk:whatsapp-message", onWhatsAppMessage);
});

onBeforeUnmount(() => {
  const { $socket } = globalStore();
  $socket.off("helpdesk:whatsapp-message", onWhatsAppMessage);
});

watch(() => props.selectedPhone, (phone) => {
  if (phone) lastReadMap.value[phone] = Date.now();
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
  if (conv.phone === props.selectedPhone) return false;
  const msgTime = new Date(conv.last_message_time).getTime();
  const readTime = lastReadMap.value[conv.phone];
  if (readTime) return msgTime > readTime;
  const stored = localStorage.getItem(`whatsapp_business_last_read_${conv.phone}`);
  if (!stored) return true;
  return msgTime > new Date(stored).getTime();
}

defineExpose({ reload: () => conversations.reload() });
</script>
