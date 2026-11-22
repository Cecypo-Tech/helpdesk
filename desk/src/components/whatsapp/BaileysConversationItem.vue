<template>
  <div
    class="group relative flex cursor-pointer items-start gap-2.5 border-b border-outline-gray-2 px-3 py-2.5 hover:bg-surface-gray-2"
    :class="selected ? 'bg-surface-gray-2' : ''"
    :title="assignedTeam ? `Team: ${assignedTeam}` : undefined"
    @click="$emit('select', jid, displayName, company || '', assignedTeam || '', phone || '')"
  >
    <!-- Avatar; the star overlays its corner so it never takes width from the name -->
    <div class="relative mt-0.5 shrink-0">
      <div
        class="flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold text-white"
        :style="{ background: avatarColor }"
      >
        {{ avatarLetter }}
      </div>
      <button
        class="absolute -left-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-surface-white text-ink-gray-5 ring-1 ring-outline-gray-2 hover:text-yellow-500"
        :class="isFavourite ? 'text-yellow-500' : 'opacity-0 focus:opacity-100 group-hover:opacity-100'"
        :title="isFavourite ? 'Remove from favourites' : 'Add to favourites'"
        :aria-label="isFavourite ? 'Remove from favourites' : 'Add to favourites'"
        @click.stop="$emit('toggle-favourite', jid)"
      >
        <LucideStar class="h-2.5 w-2.5" :fill="isFavourite ? 'currentColor' : 'none'" />
      </button>
      <span
        v-if="isGroup"
        class="absolute -bottom-0.5 -right-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-surface-white ring-1 ring-outline-gray-2"
        title="Group"
      >
        <LucideUsers class="h-2.5 w-2.5 text-ink-gray-6" />
      </span>
    </div>

    <div class="min-w-0 flex-1">
      <!-- Who: name · company, one truncating line -->
      <div class="flex items-baseline gap-1.5">
        <span class="min-w-0 flex-1 truncate text-sm">
          <span class="font-semibold text-ink-gray-9">{{ title }}</span>
          <span v-if="subtitle" class="text-ink-gray-6"> · {{ subtitle }}</span>
        </span>
        <button
          v-if="!company && !isGroup"
          class="hidden shrink-0 text-xs text-ink-gray-5 underline hover:text-ink-gray-8 group-hover:inline"
          @click.stop="$emit('add-company', jid, displayName, '', assignedTeam || '', phone || '')"
        >+ company</button>
        <span
          class="shrink-0 text-[11px]"
          :class="unreadCount > 0 ? 'font-semibold text-ink-green-3' : 'text-ink-gray-6'"
          :title="fullTime"
        >{{ time }}</span>
      </div>

      <!-- What: sender + cleaned preview, two lines -->
      <div class="mt-0.5 flex items-start gap-1">
        <p
          class="min-w-0 flex-1 break-words text-xs leading-4 line-clamp-2"
          :class="unreadCount > 0 ? 'text-ink-gray-8' : 'text-ink-gray-6'"
        >
          <span v-if="sender" class="font-medium text-ink-gray-7">{{ sender }}: </span>{{ preview }}
        </p>
        <span
          v-if="openTaskCount"
          class="flex h-[18px] shrink-0 items-center gap-0.5 rounded-full bg-surface-gray-3 px-1.5 text-[10px] font-medium text-ink-gray-7"
          :title="`${openTaskCount} open task${openTaskCount === 1 ? '' : 's'}`"
        >
          <LucideCheckSquare class="h-2.5 w-2.5" />{{ openTaskCount }}
        </span>
        <span
          v-if="unreadCount > 0"
          class="flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-full bg-green-500 px-1 text-[10px] font-bold leading-none text-white"
        >{{ unreadCount > 99 ? "99+" : unreadCount }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import LucideUsers from "~icons/lucide/users";
import LucideStar from "~icons/lucide/star";
import LucideCheckSquare from "~icons/lucide/check-square";
import { useAuthStore } from "@/stores/auth";
import { formatSidebarTime, fullTimestamp, previewText, senderLabel } from "@/utils/waSidebar";

const props = defineProps<{
  jid: string;
  displayName: string;
  company?: string;
  assignedTeam?: string;
  phone?: string;
  isGroup: boolean;
  lastMessage: string;
  lastSenderName?: string;
  lastSenderUser?: string;
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
  /** Same arguments as select: the page opens the chat, then its contact editor. */
  (e: "add-company", jid: string, displayName: string, company: string, assignedTeam: string, phone: string): void;
}>();

const auth = useAuthStore();

// A chat with no saved name is titled by its number; the company says more, so
// it leads and the number follows.
const isBareNumber = computed(() => /^\+?[\d\s-]+$/.test(props.displayName || ""));
const title = computed(() =>
  props.company && isBareNumber.value ? props.company : props.displayName
);
// Skip a company that only repeats the name.
const subtitle = computed(() => {
  if (!props.company) return "";
  if (isBareNumber.value) return props.displayName;
  return props.company.trim().toLowerCase() === (props.displayName || "").trim().toLowerCase() ? "" : props.company;
});

const sender = computed(() =>
  senderLabel({
    direction: props.lastDirection,
    senderName: props.lastSenderName,
    senderUser: props.lastSenderUser,
    me: auth.userId,
    isGroup: props.isGroup,
  })
);
const preview = computed(() => previewText(props.lastMessage, props.contentType));
const time = computed(() => formatSidebarTime(props.lastMessageTime));
const fullTime = computed(() => fullTimestamp(props.lastMessageTime));

// Skip a leading "+" so numbers get a digit, not a plus sign.
const avatarLetter = computed(() => ((title.value || "").replace(/^[^\p{L}\p{N}]+/u, "") || "?")[0].toUpperCase());

const avatarColor = computed(() => {
  const colors = ["#128c7e", "#7e57c2", "#e67e22", "#c0392b", "#2980b9", "#27ae60", "#8e44ad"];
  let hash = 0;
  for (const ch of props.jid) hash = ((hash * 31) + ch.charCodeAt(0)) & 0x7fffffff;
  return colors[hash % colors.length];
});
</script>
