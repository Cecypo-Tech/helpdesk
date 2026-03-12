<template>
  <div
    class="flex"
    :class="isOutgoing ? 'justify-end' : 'justify-start'"
  >
    <div
      class="max-w-[70%] rounded-lg px-3 py-2 text-sm shadow-sm"
      :class="
        isOutgoing
          ? 'bg-green-100 text-ink-gray-9'
          : 'bg-surface-white text-ink-gray-9 border border-outline-gray-2'
      "
    >
      <!-- Profile name for incoming -->
      <div
        v-if="!isOutgoing && message.profile_name"
        class="text-xs font-medium text-green-700 mb-1"
      >
        {{ message.profile_name }}
      </div>

      <!-- Image attachment -->
      <div v-if="message.content_type === 'image' && message.attach" class="mb-1">
        <img
          :src="message.attach"
          class="rounded max-w-full max-h-60 cursor-pointer"
          @click="openAttachment(message.attach)"
        />
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
          <span class="text-xs truncate">Download attachment</span>
        </a>
      </div>

      <!-- Audio/Video -->
      <div v-if="message.content_type === 'audio' && message.attach" class="mb-1">
        <audio controls :src="message.attach" class="max-w-full" />
      </div>
      <div v-if="message.content_type === 'video' && message.attach" class="mb-1">
        <video controls :src="message.attach" class="rounded max-w-full max-h-60" />
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
</template>

<script setup lang="ts">
import { computed } from "vue";

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
  // Basic sanitization - strip script tags but preserve HTML from frappe
  return text.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "");
});

function openAttachment(url: string) {
  window.open(url, "_blank");
}
</script>
