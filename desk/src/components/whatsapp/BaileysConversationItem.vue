<template>
  <div
    class="group flex cursor-pointer items-center gap-2.5 border-b border-outline-gray-2 px-3 py-2.5 hover:bg-surface-gray-2"
    :class="selected ? 'bg-surface-gray-2' : ''"
    @click="$emit('select', jid, displayName, company || '', assignedTeam || '', phone || '')"
  >
    <!-- Avatar with type badge -->
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
      <span
        v-if="isGroup"
        class="absolute -bottom-0.5 -right-0.5 flex h-3 w-3 items-center justify-center rounded-full bg-surface-white ring-1 ring-outline-gray-2"
      >
        <LucideUsers class="h-2 w-2 text-ink-gray-5" />
      </span>
    </div>

    <div class="min-w-0 flex-1">
      <div class="flex items-center justify-between gap-1">
        <span class="truncate text-sm font-semibold text-ink-gray-9">{{ displayName }}</span>
        <div class="flex shrink-0 items-center gap-1">
          <!-- Favourite star — always visible once starred, otherwise only on hover -->
          <button
            class="flex h-4 w-4 items-center justify-center text-ink-gray-4 hover:text-yellow-500"
            :class="isFavourite ? 'text-yellow-500' : 'opacity-0 group-hover:opacity-100'"
            :title="isFavourite ? 'Remove from favourites' : 'Add to favourites'"
            @click.stop="$emit('toggle-favourite', jid)"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" :fill="isFavourite ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
            </svg>
          </button>
          <span class="text-[11px] text-ink-gray-5">{{ formattedTime }}</span>
        </div>
      </div>
      <div class="flex items-center gap-1">
        <span v-if="company" class="truncate text-[11px] text-ink-gray-4">{{ company }}</span>
        <span
          v-if="assignedTeam"
          class="shrink-0 rounded-full bg-blue-100 px-1.5 py-0.5 text-[9px] font-medium text-blue-700 dark:bg-blue-900 dark:text-blue-300"
        >{{ assignedTeam }}</span>
      </div>
      <div class="mt-0.5 flex items-center justify-between gap-1">
        <span class="truncate text-xs text-ink-gray-5">
          <span v-if="lastDirection === 'Outgoing'" class="text-ink-gray-4">✓✓ </span>
          <span v-if="isGroup && lastSenderName && lastDirection === 'Incoming' && contentType !== 'reaction'" class="font-medium text-ink-gray-6">{{ lastSenderName.split(" ")[0] }}: </span>
          <span v-if="contentType === 'reaction'">
            <span v-if="lastDirection === 'Incoming'" class="italic">Reacted {{ lastMessage || "👍" }}</span>
            <span v-else class="italic">You reacted {{ lastMessage || "👍" }}</span>
          </span>
          <span v-else-if="contentType !== 'text' && !lastMessage" class="italic">
            [{{ contentType }}]
          </span>
          <span v-else>{{ lastMessage }}</span>
        </span>
        <span
          v-if="unreadCount > 0"
          class="ml-1 flex min-w-[18px] h-[18px] shrink-0 items-center justify-center rounded-full bg-green-500 px-1 text-[10px] font-bold leading-none text-white"
        >{{ unreadCount > 99 ? "99+" : unreadCount }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import LucideUsers from "~icons/lucide/users";

const props = defineProps<{
  jid: string;
  displayName: string;
  company?: string;
  assignedTeam?: string;
  phone?: string;
  isGroup: boolean;
  lastMessage: string;
  lastSenderName?: string;
  lastMessageTime: string;
  lastDirection: string;
  contentType: string;
  unreadCount: number;
  isFavourite: boolean;
  selected: boolean;
  openTaskCount?: number;
}>();

defineEmits<{
  (e: "select", jid: string, displayName: string, company: string, assignedTeam: string, phone: string): void;
  (e: "toggle-favourite", jid: string): void;
}>();

const avatarLetter = computed(() => (props.displayName || "?")[0].toUpperCase());

const avatarColor = computed(() => {
  const colors = ["#128c7e", "#7e57c2", "#e67e22", "#c0392b", "#2980b9", "#27ae60", "#8e44ad"];
  let hash = 0;
  for (const ch of props.jid) hash = ((hash * 31) + ch.charCodeAt(0)) & 0x7fffffff;
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
