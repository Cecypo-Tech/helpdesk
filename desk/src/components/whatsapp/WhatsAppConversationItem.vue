<template>
  <div
    class="flex cursor-pointer items-start gap-2.5 border-b border-l-2 border-outline-gray-2 px-3 py-2.5 hover:bg-surface-gray-2"
    :class="[selected ? 'bg-surface-gray-2' : '', statusBorderClass]"
    @click="$emit('select', phone, displayName)"
  >
    <!-- Avatar -->
    <div class="mt-0.5 shrink-0">
      <div
        class="flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold text-white"
        :style="{ background: avatarColor }"
      >
        {{ avatarLetter }}
      </div>
    </div>

    <div class="min-w-0 flex-1">
      <!-- Who: name · company, one truncating line -->
      <div class="flex items-baseline gap-1.5">
        <span class="min-w-0 flex-1 truncate text-sm">
          <span class="font-semibold text-ink-gray-9">{{ title }}</span>
          <span v-if="subtitle" class="text-ink-gray-6"> · {{ subtitle }}</span>
        </span>
        <span
          class="shrink-0 text-[11px]"
          :class="hasUnread ? 'font-semibold text-ink-green-3' : 'text-ink-gray-6'"
          :title="fullTime"
        >{{ time }}</span>
      </div>
      <!-- What: sender + cleaned preview, two lines -->
      <div class="mt-0.5 flex items-start gap-1">
        <p
          class="min-w-0 flex-1 break-words text-xs leading-4 line-clamp-2"
          :class="hasUnread ? 'text-ink-gray-8' : 'text-ink-gray-6'"
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
          v-if="hasUnread"
          class="mt-1 h-2 w-2 shrink-0 rounded-full bg-green-500"
          title="Unread"
        />
      </div>
      <div
        v-if="ticketStatus || ticketPriority || assignedTo"
        class="mt-0.5 flex items-center gap-1 text-[11px] text-ink-gray-6"
      >
        <IndicatorIcon
          v-if="ticketStatus"
          class="size-2 shrink-0"
          :class="ticketStatusStore.getStatus(ticketStatus)?.parsed_color"
        />
        <span v-if="ticketStatus" class="truncate">{{ ticketStatus }}</span>
        <span v-if="ticketStatus && ticketPriority">·</span>
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
import LucideCheckSquare from "~icons/lucide/check-square";
import { useAuthStore } from "@/stores/auth";
import { formatSidebarTime, fullTimestamp, previewText, senderLabel } from "@/utils/waSidebar";

const ticketStatusStore = useTicketStatusStore();

const props = defineProps<{
  phone: string;
  displayName: string;
  lastMessage: string;
  lastMessageTime: string;
  lastDirection: string;
  contentType?: string;
  lastSenderName?: string;
  lastSenderUser?: string;
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

const auth = useAuthStore();

// A contact with no saved name is titled by its number; the company says more,
// so it leads and the number follows.
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
  })
);
const preview = computed(() => previewText(props.lastMessage, props.contentType || "text"));
const time = computed(() => formatSidebarTime(props.lastMessageTime));
const fullTime = computed(() => fullTimestamp(props.lastMessageTime));

// Skip a leading "+" so numbers get a digit, not a plus sign.
const avatarLetter = computed(
  () => ((title.value || "").replace(/^[^\p{L}\p{N}]+/u, "") || "?")[0].toUpperCase()
);

const avatarColor = computed(() => {
  const colors = ["#128c7e", "#7e57c2", "#e67e22", "#c0392b", "#2980b9", "#27ae60", "#8e44ad"];
  let hash = 0;
  for (const ch of props.phone) hash = ((hash * 31) + ch.charCodeAt(0)) & 0x7fffffff;
  return colors[hash % colors.length];
});
</script>
