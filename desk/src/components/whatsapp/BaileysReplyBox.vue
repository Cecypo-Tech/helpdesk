<template>
  <div
    class="border-t border-outline-gray-2 px-4 py-3"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
    :class="{ 'bg-blue-50 ring-2 ring-inset ring-blue-400': dragging }"
  >
    <!-- Attachment preview -->
    <div v-if="attachment" class="mb-2 flex items-center gap-2 rounded-lg border border-outline-gray-3 bg-surface-gray-1 px-3 py-2">
      <img
        v-if="isImage"
        :src="attachmentPreview"
        class="h-12 w-12 rounded object-cover"
        alt="preview"
      />
      <div v-else class="flex h-12 w-12 items-center justify-center rounded bg-surface-gray-2">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" class="text-ink-gray-5">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <polyline points="14 2 14 8 20 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="min-w-0 flex-1">
        <p class="truncate text-xs font-medium text-ink-gray-7">{{ attachment.name }}</p>
        <p class="text-[11px] text-ink-gray-5">{{ contentType }}</p>
      </div>
      <button class="text-ink-gray-4 hover:text-ink-gray-7" @click="clearAttachment">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
          <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </button>
    </div>

    <!-- Reply-to preview -->
    <div
      v-if="replyTo"
      class="mb-2 flex items-start gap-2 rounded-lg border-l-4 border-green-500 bg-surface-gray-1 px-3 py-2"
    >
      <div class="min-w-0 flex-1">
        <p class="text-[11px] font-medium text-green-600">
          {{ replyTo.direction === "Outgoing" ? "You" : (replyTo.sender_name || "Customer") }}
        </p>
        <p class="truncate text-xs text-ink-gray-6">{{ replyTo.message || "(media)" }}</p>
      </div>
      <button
        class="shrink-0 text-ink-gray-4 hover:text-ink-gray-7"
        @click="emit('clearReply')"
        title="Cancel reply"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
          <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </button>
    </div>

    <div class="flex items-end gap-2">
      <!-- Attach -->
      <button
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-outline-gray-3 text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
        title="Attach file"
        @click="fileInput?.click()"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
          <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
      <input
        ref="fileInput"
        type="file"
        class="hidden"
        accept="image/*,video/*,audio/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip"
        @change="onFileSelected"
      />

      <textarea
        ref="textareaRef"
        v-model="text"
        :disabled="sending"
        :placeholder="attachment ? 'Add a caption (optional)...' : 'Type a message...'"
        rows="1"
        class="flex-1 resize-none rounded-lg border border-outline-gray-3 bg-surface-gray-2 px-3 py-2 text-sm text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none disabled:opacity-50"
        @input="autoResize"
        @keydown.enter.exact.prevent="send"
        @paste="onPaste"
      />
      <button
        :disabled="(!text.trim() && !attachment) || sending"
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-green-600 text-white hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50"
        @click="send"
      >
        <svg v-if="!sending" width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M14.67 1.33L7.33 8.67M14.67 1.33l-4.34 13.34-3-6-6-3 13.34-4.34z" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <svg v-else class="animate-spin" width="16" height="16" viewBox="0 0 16 16" fill="none">
          <circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="2" stroke-dasharray="28" stroke-dashoffset="8" stroke-linecap="round"/>
        </svg>
      </button>
    </div>
    <p v-if="dragging" class="mt-1 text-center text-xs text-blue-500">Drop file to attach</p>
  </div>
</template>

<script setup lang="ts">
import { createResource, toast } from "frappe-ui";
import { ref, computed, nextTick } from "vue";

const props = defineProps<{
  ticketId: string;
  replyTo?: Record<string, any> | null;
  jid?: string | null;
}>();

const emit = defineEmits<{
  (e: "sent"): void;
  (e: "clearReply"): void;
}>();

const text = ref("");
const sending = ref(false);
const dragging = ref(false);
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const attachment = ref<File | null>(null);
const attachmentPreview = ref<string>("");

const contentType = computed(() => {
  if (!attachment.value) return "text";
  const mime = attachment.value.type;
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  return "document";
});

const isImage = computed(() => contentType.value === "image");

function setFile(file: File) {
  attachment.value = file;
  if (file.type.startsWith("image/")) {
    const reader = new FileReader();
    reader.onload = (e) => { attachmentPreview.value = e.target?.result as string; };
    reader.readAsDataURL(file);
  } else {
    attachmentPreview.value = "";
  }
}

function clearAttachment() {
  attachment.value = null;
  attachmentPreview.value = "";
  if (fileInput.value) fileInput.value.value = "";
}

function onFileSelected(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (file) setFile(file);
}

function onDrop(e: DragEvent) {
  dragging.value = false;
  const file = e.dataTransfer?.files?.[0];
  if (file) setFile(file);
}

function onPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.kind === "file") {
      const file = item.getAsFile();
      if (file) { e.preventDefault(); setFile(file); return; }
    }
  }
}

const sendReply = createResource({
  url: "helpdesk.integrations.baileys.send_baileys_reply",
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send message");
  },
});

async function send() {
  if ((!text.value.trim() && !attachment.value) || sending.value) return;

  if (attachment.value) {
    sending.value = true;
    const formData = new FormData();
    formData.append("file", attachment.value, attachment.value.name);
    if (props.jid) {
      formData.append("jid", props.jid);
    } else {
      formData.append("ticket", props.ticketId);
    }
    formData.append("message", text.value.trim());
    formData.append("content_type", contentType.value);

    text.value = "";
    clearAttachment();
    if (textareaRef.value) textareaRef.value.style.height = "auto";
    sending.value = false;
    emit("sent");

    fetch("/api/method/helpdesk.integrations.baileys.send_baileys_media", {
      method: "POST",
      headers: { "X-Frappe-CSRF-Token": (window as any).csrf_token ?? "" },
      body: formData,
    }).then(async (response) => {
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        toast.error(err?.exc_type || "Media send failed");
      }
    }).catch(() => {
      toast.error("Media send failed");
    });
  } else {
    const msgText = text.value.trim();
    // Capture reply context BEFORE emit("sent") clears replyingTo in parent
    const replyToId = props.replyTo?.message_id || "";
    const replyToText = props.replyTo?.message || "";
    const replyToFromMe = props.replyTo?.direction === "Outgoing";
    text.value = "";
    if (textareaRef.value) textareaRef.value.style.height = "auto";
    emit("sent");
    sendReply.submit({
      ...(props.jid ? { jid: props.jid } : { ticket: props.ticketId }),
      message: msgText,
      content_type: "text",
      reply_to_message_id: replyToId,
      reply_to_text: replyToText,
      reply_to_from_me: replyToFromMe,
    });
  }
}

function autoResize() {
  nextTick(() => {
    if (textareaRef.value) {
      textareaRef.value.style.height = "auto";
      textareaRef.value.style.height = Math.min(textareaRef.value.scrollHeight, 120) + "px";
    }
  });
}
</script>
