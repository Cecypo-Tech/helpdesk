<template>
  <div
    class="flex"
    :class="isOutgoing ? 'justify-end' : 'justify-start'"
  >
    <div
      class="max-w-[70%] rounded-lg px-3 py-2 text-sm shadow-sm"
      :class="
        isOutgoing
          ? 'bg-green-100 dark:bg-green-900 text-ink-gray-9'
          : 'bg-surface-white text-ink-gray-9 border border-outline-gray-2'
      "
    >
      <!-- Profile name for incoming -->
      <div
        v-if="!isOutgoing && message.profile_name"
        class="text-xs font-medium text-green-700 dark:text-green-400 mb-1"
      >
        {{ message.profile_name }}
      </div>
      <!-- Sender name for outgoing -->
      <div
        v-if="isOutgoing && message.sender_full_name"
        class="text-xs font-medium text-ink-gray-5 mb-1 text-right"
      >
        {{ message.sender_full_name }}
      </div>

      <!-- Image attachment -->
      <div v-if="message.content_type === 'image' && message.attach" class="mb-1">
        <img
          :src="message.attach"
          class="rounded max-w-full max-h-60 cursor-pointer"
          @click="openLightbox(message.attach, 'image')"
        />
      </div>

      <!-- Image sent (no stored URL — sent directly to WhatsApp) -->
      <div
        v-else-if="message.content_type === 'image' && !message.attach"
        class="mb-1 flex items-center gap-2 text-ink-gray-5 text-xs italic"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
        Image sent
      </div>

      <!-- Document attachment -->
      <div
        v-if="message.content_type === 'document' && message.attach"
        class="mb-1"
      >
        <a
          :href="message.attach"
          target="_blank"
          class="flex items-center gap-2 px-3 py-2 rounded bg-surface-gray-2 hover:bg-surface-gray-3 text-ink-gray-7"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M9.33 1.33H4a1.33 1.33 0 00-1.33 1.34v10.66A1.33 1.33 0 004 14.67h8a1.33 1.33 0 001.33-1.34V5.33L9.33 1.33z" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span class="text-xs truncate">{{ attachFilename || 'Download attachment' }}</span>
        </a>
      </div>

      <!-- Document sent (no stored URL) -->
      <div
        v-else-if="message.content_type === 'document' && !message.attach"
        class="mb-1 flex items-center gap-2 text-ink-gray-5 text-xs italic"
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M9.33 1.33H4a1.33 1.33 0 00-1.33 1.34v10.66A1.33 1.33 0 004 14.67h8a1.33 1.33 0 001.33-1.34V5.33L9.33 1.33z" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"/></svg>
        Document sent
      </div>

      <!-- Audio/Video -->
      <div v-if="message.content_type === 'audio' && message.attach" class="mb-1">
        <audio controls :src="message.attach" class="max-w-full" />
      </div>
      <div v-if="message.content_type === 'video' && message.attach" class="mb-1">
        <video
          :src="message.attach"
          class="rounded max-w-full max-h-60 cursor-pointer"
          @click.prevent="openLightbox(message.attach, 'video')"
        />
      </div>

      <!-- Video sent (no stored URL) -->
      <div
        v-else-if="message.content_type === 'video' && !message.attach"
        class="mb-1 flex items-center gap-2 text-ink-gray-5 text-xs italic"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
        Video sent
      </div>

      <!-- Message text -->
      <div v-if="message.message" class="whitespace-pre-wrap break-words" v-html="sanitizedMessage" />

      <!-- Footer: time + status -->
      <div class="flex items-center justify-end gap-1 mt-1">
        <span class="text-[10px] text-ink-gray-5">
          {{ formattedTime }}
        </span>
        <!-- Delivery status for outgoing -->
        <span v-if="isOutgoing" class="text-[10px]">
          <span v-if="message.status === 'read'" class="text-blue-500">✓✓</span>
          <span v-else-if="message.status === 'delivered'" class="text-ink-gray-5">✓✓</span>
          <span v-else-if="message.status === 'sent' || message.status === 'Success'" class="text-ink-gray-5">✓</span>
          <span v-else-if="message.status === 'Failed'" class="text-red-500">!</span>
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
        class="absolute top-4 right-4 text-white hover:text-gray-300"
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
import { computed, reactive } from "vue";

const props = defineProps<{
  message: Record<string, any>;
}>();

const isOutgoing = computed(() => props.message.type === "Outgoing");

const formattedTime = computed(() => {
  if (!props.message.creation) return "";
  const d = new Date(props.message.creation);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
});

const sanitizedMessage = computed(() => {
  const text = props.message.message || "";
  return text.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "");
});

// Extract filename from the attach URL (e.g. /files/abc123.pdf → abc123.pdf)
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
