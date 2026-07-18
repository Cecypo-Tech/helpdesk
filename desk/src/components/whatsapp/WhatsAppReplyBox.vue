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

    <!-- Attachment previews (one chip per file) -->
    <div v-if="attachments.length" class="mb-2 flex flex-wrap gap-2">
      <div
        v-for="att in attachments"
        :key="att.id"
        class="flex items-center gap-2 rounded-lg border border-outline-gray-3 bg-surface-gray-1 px-2 py-1.5"
      >
        <img
          v-if="att.isImage"
          :src="att.previewUrl"
          class="h-10 w-10 rounded object-cover"
          alt="preview"
        />
        <div v-else class="flex h-10 w-10 items-center justify-center rounded bg-surface-gray-2">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" class="text-ink-gray-5">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <polyline points="14 2 14 8 20 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <div class="min-w-0 max-w-[140px]">
          <p class="truncate text-xs font-medium text-ink-gray-7">{{ att.file.name }}</p>
          <p class="text-[11px] text-ink-gray-5">{{ fileContentType(att.file) }}</p>
        </div>
        <button class="shrink-0 text-ink-gray-4 hover:text-ink-gray-7" title="Remove" @click="removeAttachment(att.id)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </button>
      </div>
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
        multiple
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

      <!-- AI suggest reply -->
      <button
        ref="aiBtn"
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-outline-gray-3 text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
        :class="{ 'bg-surface-gray-1 border-purple-300 text-purple-600': showAiSuggestion }"
        :disabled="aiLoading"
        title="AI suggest reply"
        @click.stop="toggleAiSuggestion"
      >
        <svg v-if="!aiLoading" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.582a.5.5 0 0 1 0 .963L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>
        </svg>
        <svg v-else class="animate-spin" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <circle cx="12" cy="12" r="9" stroke-dasharray="42" stroke-dashoffset="12"/>
        </svg>
      </button>

      <!-- AI suggestion popover -->
      <Teleport to="body">
        <div
          v-if="showAiSuggestion"
          ref="aiPopup"
          class="fixed z-50 flex flex-col rounded-xl border border-outline-gray-2 bg-surface-white shadow-xl"
          :style="aiPopupStyle"
        >
          <div class="flex items-center justify-between border-b border-outline-gray-2 px-3 py-2">
            <span class="flex items-center gap-1.5 text-xs font-semibold text-ink-gray-7">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.582a.5.5 0 0 1 0 .963L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>
              </svg>
              AI Suggestion
            </span>
            <button class="text-ink-gray-4 hover:text-ink-gray-7" @click="showAiSuggestion = false">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>
          <div class="px-3 py-2.5 text-xs text-ink-gray-8 whitespace-pre-wrap max-h-40 overflow-y-auto leading-relaxed">{{ aiSuggestion }}</div>
          <div class="flex gap-2 border-t border-outline-gray-2 px-3 py-2">
            <button
              class="flex-1 rounded-lg bg-green-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-green-700"
              @click="applyAiSuggestion"
            >Use this</button>
            <button
              class="rounded-lg border border-outline-gray-3 px-3 py-1.5 text-xs text-ink-gray-6 hover:bg-surface-gray-1"
              @click="showAiSuggestion = false"
            >Dismiss</button>
          </div>
        </div>
      </Teleport>

      <textarea
        ref="textareaRef"
        v-model="text"
        :disabled="sending"
        :placeholder="attachments.length ? 'Add a caption (optional)...' : 'Type a message...'"
        rows="1"
        class="flex-1 resize-none rounded-lg border border-outline-gray-3 bg-surface-gray-2 px-3 py-2 text-sm text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none disabled:opacity-50"
        @input="autoResize"
        @keydown.enter.exact.prevent="send"
        @paste="onPaste"
      />
      <button
        :disabled="(!text.trim() && !attachments.length) || sending"
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
    <p v-if="dragging" class="mt-1 text-center text-xs text-blue-500">Drop files to attach</p>
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
import { call, createResource, toast } from "frappe-ui";
import { ref, computed, nextTick, onMounted, onBeforeUnmount } from "vue";
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
interface Attachment {
  id: string;
  file: File;
  previewUrl: string;
  isImage: boolean;
}

const attachments = ref<Attachment[]>([]);
// Every object URL we create is tracked here and revoked on unmount, so we
// never leak preview URLs for files that get removed or sent.
const objectUrls = new Set<string>();

function makeId() {
  return (globalThis.crypto as any)?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

function makeObjectUrl(file: File): string {
  const url = URL.createObjectURL(file);
  objectUrls.add(url);
  return url;
}

function revokeObjectUrl(url: string) {
  if (url && objectUrls.has(url)) {
    URL.revokeObjectURL(url);
    objectUrls.delete(url);
  }
}

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

// ── AI suggestion ─────────────────────────────────────────────────────────────
const aiBtn = ref<HTMLButtonElement | null>(null);
const aiPopup = ref<HTMLElement | null>(null);
const showAiSuggestion = ref(false);
const aiSuggestion = ref("");
const aiLoading = ref(false);
const aiPopupStyle = ref<Record<string, string>>({});

function positionAiPopup() {
  if (!aiBtn.value) return;
  const rect = aiBtn.value.getBoundingClientRect();
  aiPopupStyle.value = {
    bottom: `${window.innerHeight - rect.top + 8}px`,
    left: `${rect.left}px`,
    width: "320px",
  };
}

async function toggleAiSuggestion() {
  if (aiLoading.value) return;
  if (showAiSuggestion.value) { showAiSuggestion.value = false; return; }
  aiLoading.value = true;
  try {
    const result = await call("helpdesk.integrations.bot.suggest_agent_reply", {
      ticket: props.ticketId,
      channel: "waba",
    });
    if (!result) { toast.warning("Not enough conversation history to suggest a reply."); return; }
    aiSuggestion.value = result;
    showAiSuggestion.value = true;
    nextTick(positionAiPopup);
  } catch {
    toast.error("Could not generate a suggestion. Check the AI settings.");
  } finally {
    aiLoading.value = false;
  }
}

function applyAiSuggestion() {
  text.value = aiSuggestion.value;
  showAiSuggestion.value = false;
  nextTick(() => { autoResize(); textareaRef.value?.focus(); });
}

function onDocClick(e: MouseEvent) {
  if (
    showAiSuggestion.value &&
    !aiPopup.value?.contains(e.target as Node) &&
    !aiBtn.value?.contains(e.target as Node)
  ) {
    showAiSuggestion.value = false;
  }
}

onMounted(() => document.addEventListener("click", onDocClick));
onBeforeUnmount(() => {
  document.removeEventListener("click", onDocClick);
  for (const url of objectUrls) URL.revokeObjectURL(url);
  objectUrls.clear();
});

// ── File handling ─────────────────────────────────────────────────────────────
function fileContentType(file: File): "image" | "video" | "audio" | "document" {
  const mime = file.type;
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  return "document";
}

function addFiles(files: FileList | File[] | null | undefined) {
  if (!files) return;
  for (const file of Array.from(files)) {
    const isImage = file.type.startsWith("image/");
    attachments.value.push({
      id: makeId(),
      file,
      previewUrl: isImage ? makeObjectUrl(file) : "",
      isImage,
    });
  }
}

function removeAttachment(id: string) {
  const idx = attachments.value.findIndex((a) => a.id === id);
  if (idx === -1) return;
  const [removed] = attachments.value.splice(idx, 1);
  if (removed?.previewUrl) revokeObjectUrl(removed.previewUrl);
}

function clearAttachments() {
  for (const a of attachments.value) revokeObjectUrl(a.previewUrl);
  attachments.value = [];
  if (fileInput.value) fileInput.value.value = "";
}

function onFileSelected(e: Event) {
  addFiles((e.target as HTMLInputElement).files);
  // Allow re-selecting the same file(s) again later.
  if (fileInput.value) fileInput.value.value = "";
}

function onDrop(e: DragEvent) {
  dragging.value = false;
  addFiles(e.dataTransfer?.files);
}

function onPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items;
  if (!items) return;
  const pasted: File[] = [];
  for (const item of items) {
    if (item.kind === "file") {
      const file = item.getAsFile();
      if (file) pasted.push(file);
    }
  }
  if (pasted.length) {
    e.preventDefault();
    addFiles(pasted);
  }
}

const sendReply = createResource({
  url: "helpdesk.integrations.wa.send_wa_reply",
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send message");
  },
});

// Upload+send one attachment via send_wa_media. Caption + reply target ride on
// the first file only; the rest go out bare, one WhatsApp Message per file.
async function sendMediaUnit(item: Attachment, caption: string, replyToMsgId: string): Promise<boolean> {
  const formData = new FormData();
  formData.append("file", item.file, item.file.name);
  formData.append("ticket", props.ticketId);
  formData.append("message", caption);
  formData.append("content_type", fileContentType(item.file));
  if (replyToMsgId) formData.append("reply_to_message_id", replyToMsgId);

  try {
    const response = await fetch("/api/method/helpdesk.integrations.wa.send_wa_media", {
      method: "POST",
      headers: { "X-Frappe-CSRF-Token": (window as any).csrf_token ?? "" },
      body: formData,
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      toast.error(err?.exc_type || "Media send failed");
      return false;
    }
    return true;
  } catch {
    toast.error("Media send failed");
    return false;
  } finally {
    revokeObjectUrl(item.previewUrl);
  }
}

async function send() {
  if ((!text.value.trim() && attachments.value.length === 0) || sending.value) return;

  const replyToMsgId = props.replyTo?.message_id || "";
  const caption = text.value.trim();
  const items = [...attachments.value];

  // Clear input immediately so the agent can start typing the next message.
  text.value = "";
  attachments.value = [];
  if (fileInput.value) fileInput.value.value = "";
  if (textareaRef.value) textareaRef.value.style.height = "auto";
  emit("sent");

  if (items.length === 0) {
    // Submit without awaiting — errors surface via onError toast
    sendReply.submit({
      ticket: props.ticketId,
      message: caption,
      content_type: "text",
      ...(replyToMsgId ? { reply_to_message_id: replyToMsgId } : {}),
    });
    return;
  }

  sending.value = true;
  try {
    for (let i = 0; i < items.length; i++) {
      const ok = await sendMediaUnit(items[i], i === 0 ? caption : "", i === 0 ? replyToMsgId : "");
      if (!ok) break;
    }
  } finally {
    sending.value = false;
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
