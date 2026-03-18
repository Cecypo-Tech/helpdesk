<template>
  <div class="flex" :class="isOutgoing ? 'justify-end' : 'justify-start'">
    <!-- Inner wrapper: limits width, anchors the action bar + emoji picker -->
    <div class="group relative max-w-[70%]">

      <!-- Action bar — shown on hover or while emoji picker is open -->
      <div
        class="absolute bottom-full mb-1 z-10 flex items-center gap-1 transition-opacity duration-100"
        :class="[
          isOutgoing ? 'right-0 flex-row-reverse' : 'left-0',
          showEmojiPicker ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
        ]"
      >
        <!-- Reply button -->
        <button
          class="flex h-7 w-7 items-center justify-center rounded-full border border-outline-gray-2 bg-surface-white text-ink-gray-5 shadow-sm hover:bg-surface-gray-1 hover:text-ink-gray-8"
          title="Reply"
          @click.stop="$emit('reply', message)"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>
          </svg>
        </button>
        <!-- React button -->
        <button
          class="flex h-7 w-7 items-center justify-center rounded-full border border-outline-gray-2 bg-surface-white text-ink-gray-5 shadow-sm hover:bg-surface-gray-1 hover:text-ink-gray-8"
          title="React"
          @click.stop="toggleEmojiPicker"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>
          </svg>
        </button>
      </div>

      <!-- Emoji picker panel -->
      <Teleport to="body">
        <div
          v-if="showEmojiPicker"
          class="fixed inset-0 z-40"
          @click="showEmojiPicker = false"
        />
      </Teleport>
      <div
        v-if="showEmojiPicker"
        class="absolute bottom-full z-50 mb-9 flex items-center gap-0.5 rounded-full border border-outline-gray-2 bg-surface-white px-2 py-1 shadow-lg"
        :class="isOutgoing ? 'right-0' : 'left-0'"
        @click.stop
      >
        <button
          v-for="emoji in REACTION_EMOJIS"
          :key="emoji"
          class="cursor-pointer p-1 text-lg transition-transform hover:scale-125"
          @click="pickEmoji(emoji)"
        >{{ emoji }}</button>
      </div>

      <!-- Bubble -->
      <div
        class="rounded-lg px-3 py-2 text-sm shadow-sm"
        :class="
          isOutgoing
            ? 'bg-green-100 dark:bg-green-900 text-ink-gray-9'
            : 'bg-surface-white text-ink-gray-9 border border-outline-gray-2'
        "
      >
        <!-- Profile name for incoming -->
        <div
          v-if="!isOutgoing && message.profile_name"
          class="mb-1 text-xs font-medium text-green-700 dark:text-green-400"
        >
          {{ message.profile_name }}
        </div>
        <!-- Sender name for outgoing -->
        <div
          v-if="isOutgoing && message.sender_full_name"
          class="mb-1 text-right text-xs font-medium text-ink-gray-5"
        >
          {{ message.sender_full_name }}
        </div>

        <!-- Reply context block -->
        <div
          v-if="message.is_reply"
          class="mb-2 cursor-default rounded border-l-2 px-2 py-1 text-xs"
          :class="isOutgoing
            ? 'border-green-500 bg-green-50 dark:border-green-400 dark:bg-green-800/50'
            : 'border-blue-400 bg-surface-gray-1'"
        >
          <div class="mb-0.5 font-medium" :class="isOutgoing ? 'text-green-700 dark:text-green-400' : 'text-blue-600'">
            {{ replyToSenderName }}
          </div>
          <div class="truncate text-ink-gray-5">{{ replyPreview }}</div>
        </div>

        <!-- Image attachment -->
        <div v-if="message.content_type === 'image' && message.attach" class="mb-1">
          <img
            :src="message.attach"
            class="max-h-60 max-w-full cursor-pointer rounded"
            @click="openLightbox(message.attach, 'image')"
          />
        </div>

        <!-- Image sent (no stored URL) -->
        <div
          v-else-if="message.content_type === 'image' && !message.attach"
          class="mb-1 flex items-center gap-2 text-xs italic text-ink-gray-5"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
          Image sent
        </div>

        <!-- Document attachment -->
        <div v-if="message.content_type === 'document' && message.attach" class="mb-1">
          <a
            :href="message.attach"
            target="_blank"
            class="flex items-center gap-2 rounded bg-surface-gray-2 px-3 py-2 text-ink-gray-7 hover:bg-surface-gray-3"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M9.33 1.33H4a1.33 1.33 0 00-1.33 1.34v10.66A1.33 1.33 0 004 14.67h8a1.33 1.33 0 001.33-1.34V5.33L9.33 1.33z" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <span class="truncate text-xs">{{ attachFilename || 'Download attachment' }}</span>
          </a>
        </div>

        <!-- Document sent (no stored URL) -->
        <div
          v-else-if="message.content_type === 'document' && !message.attach"
          class="mb-1 flex items-center gap-2 text-xs italic text-ink-gray-5"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M9.33 1.33H4a1.33 1.33 0 00-1.33 1.34v10.66A1.33 1.33 0 004 14.67h8a1.33 1.33 0 001.33-1.34V5.33L9.33 1.33z" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"/></svg>
          Document sent
        </div>

        <!-- Audio -->
        <div v-if="message.content_type === 'audio' && message.attach" class="mb-1">
          <audio controls :src="message.attach" class="max-w-full" />
        </div>

        <!-- Video -->
        <div v-if="message.content_type === 'video' && message.attach" class="mb-1">
          <video
            :src="message.attach"
            class="max-h-60 max-w-full cursor-pointer rounded"
            @click.prevent="openLightbox(message.attach, 'video')"
          />
        </div>

        <!-- Video sent (no stored URL) -->
        <div
          v-else-if="message.content_type === 'video' && !message.attach"
          class="mb-1 flex items-center gap-2 text-xs italic text-ink-gray-5"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
          Video sent
        </div>

        <!-- Message text -->
        <div v-if="message.message" class="whitespace-pre-wrap break-words" v-html="sanitizedMessage" />

        <!-- Footer: time + status -->
        <div class="mt-1 flex items-center justify-end gap-1">
          <span class="text-[10px] text-ink-gray-5">{{ formattedTime }}</span>
          <span v-if="isOutgoing" class="text-[10px]">
            <span v-if="message.status === 'read'" class="text-blue-500">✓✓</span>
            <span v-else-if="message.status === 'delivered'" class="text-ink-gray-5">✓✓</span>
            <span v-else-if="message.status === 'sent' || message.status === 'Success'" class="text-ink-gray-5">✓</span>
            <span v-else-if="message.status === 'Failed'" class="text-red-500">!</span>
          </span>
        </div>
      </div>

      <!-- Reaction badges (below bubble) -->
      <div
        v-if="aggregatedReactions.length"
        class="mt-0.5 flex flex-wrap gap-1"
        :class="isOutgoing ? 'justify-end' : 'justify-start'"
      >
        <span
          v-for="r in aggregatedReactions"
          :key="r.emoji"
          class="flex items-center gap-0.5 rounded-full border border-outline-gray-2 bg-surface-white px-1.5 py-0.5 text-xs shadow-sm"
          :class="r.hasOwn ? 'border-blue-300 bg-blue-50' : ''"
        >
          {{ r.emoji }}<span v-if="r.count > 1" class="ml-0.5 text-ink-gray-5">{{ r.count }}</span>
        </span>
      </div>

    </div>
  </div>

  <!-- Lightbox -->
  <Teleport to="body">
    <div
      v-if="lightbox.open"
      class="fixed inset-0 z-[9999] flex items-center justify-center bg-black/80"
      @click.self="closeLightbox"
    >
      <button
        class="absolute right-4 top-4 text-white hover:text-gray-300"
        @click="closeLightbox"
      >
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
      <img
        v-if="lightbox.type === 'image'"
        :src="lightbox.src"
        class="max-h-[90vh] max-w-[90vw] rounded object-contain"
      />
      <video
        v-else-if="lightbox.type === 'video'"
        :src="lightbox.src"
        controls
        autoplay
        class="max-h-[90vh] max-w-[90vw] rounded"
      />
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";

const REACTION_EMOJIS = ["👍", "❤️", "😂", "😮", "😢", "🙏"];

const props = defineProps<{
  message: Record<string, any>;
  reactions?: Array<{ emoji: string; type: string }>;
  replyToMessage?: Record<string, any> | null;
}>();

const emit = defineEmits<{
  (e: "reply", message: Record<string, any>): void;
  (e: "react", emoji: string, targetMessageId: string): void;
}>();

const isOutgoing = computed(() => props.message.type === "Outgoing");

const showEmojiPicker = ref(false);

function toggleEmojiPicker() {
  showEmojiPicker.value = !showEmojiPicker.value;
}

function pickEmoji(emoji: string) {
  showEmojiPicker.value = false;
  emit("react", emoji, props.message.message_id || "");
}

// Aggregate reactions: {emoji → {emoji, count, hasOwn}}
const aggregatedReactions = computed(() => {
  const map: Record<string, { emoji: string; count: number; hasOwn: boolean }> = {};
  for (const r of props.reactions ?? []) {
    if (!r.emoji) continue;
    if (!map[r.emoji]) map[r.emoji] = { emoji: r.emoji, count: 0, hasOwn: false };
    map[r.emoji].count++;
    if (r.type === "Outgoing") map[r.emoji].hasOwn = true;
  }
  return Object.values(map);
});

// Reply context helpers
const replyToSenderName = computed(() => {
  const m = props.replyToMessage;
  if (!m) return "…";
  return m.type === "Outgoing"
    ? (m.sender_full_name || "You")
    : (m.profile_name || "Customer");
});

const replyPreview = computed(() => {
  const m = props.replyToMessage;
  if (!m) return "Original message not available";
  if (m.content_type === "image") return "📷 Photo";
  if (m.content_type === "video") return "🎥 Video";
  if (m.content_type === "audio") return "🎤 Audio";
  if (m.content_type === "document") return "📎 Document";
  return (m.message || "").slice(0, 80) || "Message";
});

const formattedTime = computed(() => {
  if (!props.message.creation) return "";
  const d = new Date(props.message.creation);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
});

const sanitizedMessage = computed(() => {
  const text = props.message.message || "";
  return text.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "");
});

const attachFilename = computed(() => {
  const url = props.message.attach;
  if (!url) return null;
  return url.split("/").pop() || null;
});

// Lightbox
const lightbox = reactive({ open: false, src: "", type: "image" as "image" | "video" });

function openLightbox(src: string, type: "image" | "video") {
  lightbox.src = src;
  lightbox.type = type;
  lightbox.open = true;
}

function closeLightbox() {
  lightbox.open = false;
  lightbox.src = "";
}
</script>
