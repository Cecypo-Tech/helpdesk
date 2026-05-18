<template>
  <div
    class="flex cursor-pointer items-center gap-2.5 border-b border-outline-gray-2 px-3 py-2.5 hover:bg-surface-gray-2"
    :class="selected ? 'bg-surface-gray-2' : ''"
    @click="$emit('select', jid, displayName, company || '', assignedTeam || '')"
  >
    <!-- Avatar with type badge -->
    <div class="relative shrink-0">
      <div
        class="flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold text-white"
        :style="{ background: avatarColor }"
      >
        {{ avatarLetter }}
      </div>
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
        <span class="shrink-0 text-[11px] text-ink-gray-5">{{ formattedTime }}</span>
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
          <span v-if="contentType !== 'text' && !lastMessage" class="italic">
            [{{ contentType }}]
          </span>
          <span v-else>{{ lastMessage }}</span>
        </span>
        <span
          v-if="hasUnread"
          class="ml-1 h-2 w-2 shrink-0 rounded-full bg-green-500"
        />
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
  isGroup: boolean;
  lastMessage: string;
  lastMessageTime: string;
  lastDirection: string;
  contentType: string;
  hasUnread: boolean;
  selected: boolean;
}>();

defineEmits<{
  (e: "select", jid: string, displayName: string, company: string, assignedTeam: string): void;
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
