<template>
  <div
    class="flex cursor-pointer items-center gap-2.5 border-b border-l-2 border-outline-gray-2 px-3 py-2.5 hover:bg-surface-gray-2"
    :class="[selected ? 'bg-surface-gray-2' : '', statusBorderClass]"
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
      <!-- Open task count badge -->
      <span
        v-if="openTaskCount > 0"
        class="absolute -top-1.5 -right-1.5 flex min-w-[14px] h-3.5 items-center justify-center rounded-full bg-blue-600 px-0.5 text-[8px] font-bold text-white ring-1 ring-surface-white leading-none"
      >{{ openTaskCount }}</span>
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
        v-if="ticketStatus || company || ticketPriority || assignedTo"
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
        <UserAvatar
          v-if="assignedTo"
          class="ml-auto shrink-0"
          :name="assignedTo"
          size="xs"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { IndicatorIcon } from "@/components/icons";
import { useTicketStatusStore } from "@/stores/ticketStatus";
import UserAvatar from "@/components/UserAvatar.vue";

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
  assignedTo?: string | null;
  openTaskCount?: number;
}>();

defineEmits<{
  (e: "select", phone: string, displayName: string): void;
}>();

const STATUS_BORDER: Record<string, string> = {
  Green: "border-l-green-700",
  Black: "border-l-black",
  Gray: "border-l-gray-700",
  Blue: "border-l-blue-700",
  Red: "border-l-red-500",
  Pink: "border-l-pink-500",
  Orange: "border-l-orange-600",
  Amber: "border-l-amber-600",
  Yellow: "border-l-yellow-700",
  Cyan: "border-l-cyan-700",
  Teal: "border-l-teal-700",
  Violet: "border-l-violet-700",
  Purple: "border-l-purple-700",
};

const statusBorderClass = computed(() => {
  if (!props.ticketStatus) return "border-l-transparent";
  const status = ticketStatusStore.getStatus(props.ticketStatus);
  return STATUS_BORDER[status?.color] ?? "border-l-transparent";
});

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
