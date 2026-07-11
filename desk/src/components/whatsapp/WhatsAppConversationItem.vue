<template>
  <div
    class="flex cursor-pointer items-center gap-2.5 border-b border-outline-gray-2 px-3 py-2.5 hover:bg-surface-gray-2"
    :class="selected ? 'bg-surface-gray-2' : ''"
    @click="$emit('select', phone, displayName)"
  >
    <!-- Avatar -->
    <div class="relative shrink-0">
      <div
        class="flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold text-white"
        :style="{ background: avatarColor }"
      >
        {{ avatarLetter }}
      </div>
    </div>

    <div class="min-w-0 flex-1">
      <div class="flex items-center justify-between gap-1">
        <span class="truncate text-sm font-semibold text-ink-gray-9">{{ displayName }}</span>
        <span class="shrink-0 text-[11px] text-ink-gray-5">{{ formattedTime }}</span>
      </div>
      <div class="mt-0.5 flex items-center justify-between gap-1">
        <span class="truncate text-xs text-ink-gray-5">
          <span v-if="lastDirection === 'Outgoing'" class="text-ink-gray-4">✓✓ </span>
          <span v-if="!lastMessage" class="italic">[media]</span>
          <span v-else>{{ lastMessage }}</span>
        </span>
        <span
          v-if="hasUnread"
          class="ml-1 h-2 w-2 shrink-0 rounded-full bg-green-500"
        />
      </div>
      <div
        v-if="ticketStatus || company || ticketPriority"
        class="mt-0.5 flex items-center gap-1 text-[11px] text-ink-gray-4"
      >
        <IndicatorIcon
          v-if="ticketStatus"
          class="size-2 shrink-0"
          :class="ticketStatusStore.getStatus(ticketStatus)?.parsed_color"
        />
        <span v-if="company" class="truncate">{{ company }}</span>
        <span v-if="company && ticketPriority">·</span>
        <span v-if="ticketPriority" class="shrink-0 truncate">{{ ticketPriority }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { IndicatorIcon } from "@/components/icons";
import { useTicketStatusStore } from "@/stores/ticketStatus";

const ticketStatusStore = useTicketStatusStore();

const props = defineProps<{
  phone: string;
  displayName: string;
  lastMessage: string;
  lastMessageTime: string;
  lastDirection: string;
  hasUnread: boolean;
  selected: boolean;
  ticketStatus?: string | null;
  ticketPriority?: string | null;
  company?: string | null;
}>();

defineEmits<{
  (e: "select", phone: string, displayName: string): void;
}>();

const avatarLetter = computed(() => (props.displayName || "?")[0].toUpperCase());

const avatarColor = computed(() => {
  const colors = ["#128c7e", "#7e57c2", "#e67e22", "#c0392b", "#2980b9", "#27ae60", "#8e44ad"];
  let hash = 0;
  for (const ch of props.phone) hash = ((hash * 31) + ch.charCodeAt(0)) & 0x7fffffff;
  return colors[hash % colors.length];
});

const formattedTime = computed(() => {
  if (!props.lastMessageTime) return "";
  const d = new Date(props.lastMessageTime);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffDays = Math.floor(diffMs / 86400000);
  if (diffDays === 0) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return d.toLocaleDateString([], { weekday: "short" });
  return d.toLocaleDateString([], { day: "2-digit", month: "2-digit" });
});
</script>
