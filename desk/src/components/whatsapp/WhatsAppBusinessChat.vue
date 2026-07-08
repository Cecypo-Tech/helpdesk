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
      <router-link
        v-if="activeTicket.data?.ticket"
        :to="`/tickets/${activeTicket.data.ticket}`"
        class="shrink-0 text-xs font-medium text-blue-600 hover:underline"
      >
        View ticket #{{ activeTicket.data.ticket }} →
      </router-link>
    </div>

    <!-- Messages Area -->
    <div ref="messagesContainer" class="flex-1 overflow-y-auto px-5 py-4">
      <div v-if="messages.loading && !messages.data" class="flex justify-center py-10">
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
        <template v-for="(group, dateKey) in groupedMessages" :key="dateKey">
          <div class="my-4 flex items-center gap-3">
            <div class="flex-1 border-t border-outline-gray-2" />
            <span class="text-[11px] font-medium text-ink-gray-5">{{ dateKey }}</span>
            <div class="flex-1 border-t border-outline-gray-2" />
          </div>
          <WhatsAppBubble
            v-for="msg in group"
            :key="msg.name"
            :message="msg"
            :reactions="[]"
            :replyToMessage="null"
            :isGroup="false"
            :mentionMap="{}"
          />
        </template>
      </div>
    </div>

    <!-- Reply box -->
    <WhatsAppReplyBox
      v-if="activeTicket.data?.ticket"
      :ticketId="activeTicket.data.ticket"
      @sent="onMessageSent"
    />
    <div
      v-else-if="activeTicket.fetched"
      class="border-t border-outline-gray-2 px-4 py-3 text-center text-xs text-ink-gray-5"
    >
      No ticket found for this number yet.
    </div>
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator } from "frappe-ui";
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { globalStore } from "@/stores/globalStore";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";
import WhatsAppBubble from "./WhatsAppBubble.vue";
import WhatsAppReplyBox from "./WhatsAppReplyBox.vue";

const props = defineProps<{
  phone: string | null;
  displayName?: string;
  showBack?: boolean;
}>();

const emit = defineEmits<{ (e: "back"): void }>();

const { $socket } = globalStore();
const messagesContainer = ref<HTMLElement | null>(null);

const messages = createResource({
  url: "helpdesk.integrations.wa.get_whatsapp_messages",
  auto: false,
});

const activeTicket = createResource({
  url: "helpdesk.integrations.wa.get_active_whatsapp_ticket_for_phone",
  auto: false,
});

function reload() {
  if (!props.phone) return;
  messages.update({ params: { phone: props.phone } });
  messages.reload();
  activeTicket.update({ params: { phone: props.phone } });
  activeTicket.reload();
}

watch(() => props.phone, reload, { immediate: true });

const messageList = computed<Record<string, any>[]>(() => messages.data || []);

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
  reload();
}

function handleRealtimeMessage() {
  // frappe_whatsapp events only carry a ticket name, not a phone number, so
  // there's no cheap client-side way to filter to "does this belong to the
  // currently open phone conversation" — just re-resolve when one is open.
  if (props.phone) reload();
}

watch(messageList, () => scrollToBottom());

onMounted(() => {
  $socket.on("helpdesk:whatsapp-message", handleRealtimeMessage);
});

onBeforeUnmount(() => {
  $socket.off("helpdesk:whatsapp-message", handleRealtimeMessage);
});

defineExpose({ scrollToBottom, refresh: reload });
</script>
