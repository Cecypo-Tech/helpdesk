<template>
  <div
    class="border-t border-outline-gray-2 px-4 py-3"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
    :class="{ 'bg-blue-50 ring-2 ring-inset ring-blue-400': dragging }"
  >
    <!-- Reply-to banner -->
    <div
      v-if="replyTo"
      class="mb-2 flex items-stretch overflow-hidden rounded-lg border-l-2 border-blue-400 bg-surface-gray-1"
    >
      <div class="min-w-0 flex-1 px-3 py-1.5">
        <p class="text-xs font-medium text-blue-600">
          {{ replyTo.type === 'Outgoing' ? (replyTo.sender_full_name || 'You') : (replyTo.profile_name || 'Customer') }}
        </p>
        <p v-if="replyTo.content_type === 'image' && replyTo.attach" class="flex items-center gap-1 text-xs text-ink-gray-5">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
          Photo
        </p>
        <p v-else class="truncate text-xs text-ink-gray-5">{{ replyPreview }}</p>
      </div>
      <!-- Image thumbnail -->
      <img
        v-if="replyTo.content_type === 'image' && replyTo.attach"
        :src="replyTo.attach"
        class="h-14 w-14 shrink-0 object-cover"
      />
      <button class="shrink-0 px-2 text-ink-gray-4 hover:text-ink-gray-7" @click="$emit('clearReply')">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>

    <!-- Attachment preview -->
    <div v-if="attachment" class="mb-2 flex items-center gap-2 rounded-lg border border-outline-gray-3 bg-surface-gray-1 px-3 py-2">
      <img
        v-if="isImage"
        :src="attachmentPreview"
        class="h-12 w-12 rounded object-cover"
        alt="preview"
      />
      <div v-else class="flex h-12 w-12 items-center justify-center rounded bg-surface-gray-2">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" class="text-ink-gray-5">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <polyline points="14 2 14 8 20 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="min-w-0 flex-1">
        <p class="truncate text-xs font-medium text-ink-gray-7">{{ attachmentName }}</p>
        <p class="text-[11px] text-ink-gray-5">{{ contentType }}</p>
      </div>
      <button class="text-ink-gray-4 hover:text-ink-gray-7" @click="clearAttachment">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </button>
    </div>

    <div class="flex items-end gap-2">
      <!-- Attach button -->
      <button
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-outline-gray-3 text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
        title="Attach file"
        @click="fileInput?.click()"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
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

      <!-- Saved Replies button -->
      <button
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-outline-gray-3 text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
        title="Saved Replies"
        @click="showSavedReplies = true"
      >
        <SavedReplyIcon class="h-4 w-4" />
      </button>

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
        <svg v-if="!sending" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M14.67 1.33L7.33 8.67M14.67 1.33l-4.34 13.34-3-6-6-3 13.34-4.34z" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <svg v-else class="animate-spin" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
          <circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="2" stroke-dasharray="28" stroke-dashoffset="8" stroke-linecap="round"/>
        </svg>
      </button>
    </div>
    <p v-if="dragging" class="mt-1 text-center text-xs text-blue-500">Drop file to attach</p>
  </div>

  <SavedRepliesSelectorModal
    v-if="showSavedReplies"
    v-model="showSavedReplies"
    doctype="HD Ticket"
    :ticketId="ticketId"
    @apply="applySavedReply"
  />
</template>

<script setup lang="ts">
import { createResource, toast } from "frappe-ui";
import { ref, computed, nextTick } from "vue";
import SavedReplyIcon from "@/components/icons/SavedReplyIcon.vue";
import SavedRepliesSelectorModal from "@/components/SavedRepliesSelectorModal.vue";

const props = defineProps<{
  ticketId: string;
  replyTo?: Record<string, any> | null;
}>();

const emit = defineEmits<{
  (e: "sent"): void;
  (e: "clearReply"): void;
}>();

const text = ref("");
const sending = ref(false);
const dragging = ref(false);
const showSavedReplies = ref(false);
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

// Attachment state
const attachment = ref<File | null>(null);
const attachmentPreview = ref<string>("");
const attachmentName = computed(() => attachment.value?.name ?? "");

const contentType = computed(() => {
  if (!attachment.value) return "text";
  const mime = attachment.value.type;
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  return "document";
});

const isImage = computed(() => contentType.value === "image");

// Preview of the message being replied to
const replyPreview = computed(() => {
  const m = props.replyTo;
  if (!m) return "";
  if (m.content_type === "image") return "📷 Photo";
  if (m.content_type === "video") return "🎥 Video";
  if (m.content_type === "audio") return "🎤 Audio";
  if (m.content_type === "document") return "📎 Document";
  return (m.message || "").slice(0, 80) || "Message";
});

function setFile(file: File) {
  attachment.value = file;
  if (file.type.startsWith("image/")) {
    const reader = new FileReader();
    reader.onload = (e) => {
      attachmentPreview.value = e.target?.result as string;
    };
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
      if (file) {
        e.preventDefault();
        setFile(file);
        return;
      }
    }
  }
}

const sendReply = createResource({
  url: "helpdesk.integrations.wa.send_wa_reply",
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send message");
  },
});

async function send() {
  if ((!text.value.trim() && !attachment.value) || sending.value) return;

  const replyToMsgId = props.replyTo?.message_id || "";

  if (attachment.value) {
    // For media: show a brief "uploading" lock only until the request is fired,
    // then restore the input so the agent can keep typing.
    sending.value = true;
    const formData = new FormData();
    formData.append("file", attachment.value, attachment.value.name);
    formData.append("ticket", props.ticketId);
    formData.append("message", text.value.trim());
    formData.append("content_type", contentType.value);
    if (replyToMsgId) formData.append("reply_to_message_id", replyToMsgId);

    // Clear input immediately so agent can start typing next message
    text.value = "";
    clearAttachment();
    if (textareaRef.value) textareaRef.value.style.height = "auto";
    sending.value = false;
    emit("sent");

    // Fire upload in background
    fetch("/api/method/helpdesk.integrations.wa.send_wa_media", {
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
    // Text: clear immediately, fire API in background
    const msgText = text.value.trim();
    const args: Record<string, any> = {
      ticket: props.ticketId,
      message: msgText,
      content_type: "text",
    };
    if (replyToMsgId) args.reply_to_message_id = replyToMsgId;

    text.value = "";
    if (textareaRef.value) textareaRef.value.style.height = "auto";
    emit("sent");

    // Submit without awaiting — errors surface via onError toast
    sendReply.submit(args);
  }
}

function htmlToWa(html: string): string {
  const div = document.createElement("div");
  div.innerHTML = html;
  function walk(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent || "";
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const el = node as Element;
    const tag = el.tagName.toLowerCase();
    const inner = Array.from(el.childNodes).map(walk).join("");
    if (tag === "strong" || tag === "b") return inner ? `*${inner}*` : "";
    if (tag === "em" || tag === "i") return inner ? `_${inner}_` : "";
    if (tag === "s" || tag === "strike" || tag === "del") return inner ? `~${inner}~` : "";
    if (tag === "code") return inner ? `\`${inner}\`` : "";
    if (tag === "br") return "\n";
    if (tag === "p" || tag === "div") return inner ? `${inner}\n` : "";
    if (tag === "li") return `• ${inner}\n`;
    return inner;
  }
  return walk(div).replace(/\n{3,}/g, "\n\n").trim();
}

function applySavedReply(content: string) {
  text.value = htmlToWa(content);
  showSavedReplies.value = false;
  nextTick(() => autoResize());
}

function autoResize() {
  nextTick(() => {
    if (textareaRef.value) {
      textareaRef.value.style.height = "auto";
      textareaRef.value.style.height =
        Math.min(textareaRef.value.scrollHeight, 120) + "px";
    }
  });
}
</script>
