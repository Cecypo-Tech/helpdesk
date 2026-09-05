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

    <!--
      Held outgoing text. The thread shows it as a pending bubble already; this
      strip is where the countdown, "Send now" and undo live.
    -->
    <div
      v-if="heldText"
      class="mb-2 flex items-stretch overflow-hidden rounded-lg border-l-2 border-amber-400 bg-surface-gray-1"
    >
      <div class="min-w-0 flex-1 px-3 py-1.5">
        <p class="text-xs font-medium text-amber-600">
          {{ __("Sending in {0}s", [heldSecondsLeft]) }}
        </p>
        <!-- line-clamp rather than truncate: merged chunks are multi-line, and
             truncate's white-space:nowrap would fight whitespace-pre-wrap. -->
        <p class="line-clamp-2 whitespace-pre-wrap text-xs text-ink-gray-5">{{ heldText }}</p>
      </div>
      <button
        class="shrink-0 px-2 text-xs font-medium text-ink-gray-6 hover:text-ink-gray-9"
        :title="__('Send now')"
        @click="flushHeld"
      >
        {{ __("Send now") }}
      </button>
      <!-- Pulls the text back into the composer rather than dropping it, so the
           hold doubles as a few seconds of undo on a typo. -->
      <button
        class="shrink-0 px-2 text-ink-gray-4 hover:text-ink-gray-7"
        :title="__('Undo send')"
        @click="recallHeld"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    </div>

    <div
      v-if="!replyWindowOpen"
      class="mb-2 flex items-center gap-1.5 text-xs text-ink-gray-5"
    >
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="shrink-0"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      {{ __("The 24-hour reply window has closed. Only approved templates can be sent until the customer replies.") }}
    </div>

    <div class="flex items-end gap-2">
      <!-- Attach button -->
      <button
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-outline-gray-3 text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7 disabled:cursor-not-allowed disabled:opacity-50"
        title="Attach file"
        :disabled="!replyWindowOpen"
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
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-outline-gray-3 text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7 disabled:cursor-not-allowed disabled:opacity-50"
        title="Saved Replies"
        :disabled="!replyWindowOpen"
        @click="showSavedReplies = true"
      >
        <SavedReplyIcon class="h-4 w-4" />
      </button>

      <!-- AI suggest reply -->
      <button
        ref="aiBtn"
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-outline-gray-3 text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
        :class="{ 'bg-surface-gray-1 border-purple-300 text-purple-600': showAiSuggestion }"
        :disabled="aiLoading || !replyWindowOpen"
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

      <!-- Templates button -->
      <button
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
        :class="
          replyWindowOpen
            ? 'border-outline-gray-3'
            : 'border-green-600 bg-green-600 text-white hover:bg-green-700 hover:text-white'
        "
        :title="
          replyWindowOpen
            ? 'WhatsApp templates'
            : 'Reply window closed — send a template'
        "
        @click="flushHeld(); showTemplates = true"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2"/>
          <line x1="3" y1="9" x2="21" y2="9"/>
          <line x1="9" y1="21" x2="9" y2="9"/>
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
        :disabled="sending || !replyWindowOpen"
        :placeholder="
          !replyWindowOpen
            ? '24-hour reply window closed — send a template to re-engage'
            : attachments.length
              ? 'Add a caption (optional)...'
              : 'Type a message...'
        "
        rows="1"
        class="flex-1 resize-none rounded-lg border border-outline-gray-3 bg-surface-gray-2 px-3 py-2 text-sm text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none disabled:opacity-50"
        @input="autoResize"
        @keydown.enter.exact.prevent="send"
        @paste="onPaste"
      />
      <button
        :disabled="(!text.trim() && !attachments.length) || sending || !replyWindowOpen"
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

  <WhatsAppTemplateModal
    v-if="showTemplates"
    v-model="showTemplates"
    :ticketId="ticketId"
    @sent="$emit('sent'); $emit('delivered')"
  />
</template>

<script setup lang="ts">
import { call, createResource, toast } from "frappe-ui";
import { ref, computed, nextTick, onMounted, onBeforeUnmount, watch } from "vue";
import SavedReplyIcon from "@/components/icons/SavedReplyIcon.vue";
import SavedRepliesSelectorModal from "@/components/SavedRepliesSelectorModal.vue";
import WhatsAppTemplateModal from "@/components/whatsapp/WhatsAppTemplateModal.vue";
import { OutboundHold } from "@/utils/outboundHold";
import { makePendingBubble, type PendingBubble, type ResolvePayload } from "@/utils/waOptimistic";
import { useAuthStore } from "@/stores/auth";

const props = withDefaults(
  defineProps<{
    ticketId: string;
    replyTo?: Record<string, any> | null;
    replyWindowOpen?: boolean;
    // Seconds to hold an outgoing message so chunks merge into one billable
    // send. Comes from WhatsApp Helpdesk Settings via get_whatsapp_ticket_info.
    // Defaults to 0 so an API response without the key never adds latency.
    outboundHoldSeconds?: number;
  }>(),
  { replyWindowOpen: true, outboundHoldSeconds: 0 }
);

const emit = defineEmits<{
  // Fired the moment the agent hits send, so the composer can clear. The
  // message does not exist yet at this point — nothing may fetch on it.
  (e: "sent"): void;
  // Fired once the server has stored a message. The thread no longer refetches
  // on it (the bubble is already there); the ticket info may have changed.
  (e: "delivered"): void;
  (e: "clearReply"): void;
  // Optimistic-send lifecycle, same contract as BaileysReplyBox: the parent
  // shows a pending bubble at once, then resolves it to the stored row's
  // identity, or removes it (undo), or keeps it as Failed with a Retry.
  (e: "optimistic", bubble: PendingBubble): void;
  (e: "optimistic-resolve", payload: ResolvePayload): void;
  (e: "optimistic-remove", name: string): void;
}>();

const auth = useAuthStore();
const senderName = computed(() => auth.userName || "");

const text = ref("");
const sending = ref(false);
const dragging = ref(false);
const showSavedReplies = ref(false);
const showTemplates = ref(false);
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

// Best-effort: the browser may cut the request short on unload, but losing a
// held message this way is far less likely than never attempting to send it.
function flushBeforeUnload() {
  outboundHold.flush();
}

onMounted(() => {
  document.addEventListener("click", onDocClick);
  window.addEventListener("beforeunload", flushBeforeUnload);
});
onBeforeUnmount(() => {
  document.removeEventListener("click", onDocClick);
  window.removeEventListener("beforeunload", flushBeforeUnload);
  // Held text belongs to the customer — send it rather than let it die with
  // the component when the agent switches ticket.
  outboundHold.destroy();
  if (holdTicker !== null) clearInterval(holdTicker);
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
  onSuccess() {
    emit("delivered");
  },
  onError(e: any) {
    toast.error(e?.messages?.[0] || "Failed to send message");
  },
});

// What a failed bubble needs to be sent again, keyed by its temp name. The
// composer has already been cleared by then, so this is the only copy.
type FailedSend =
  | { kind: "text"; params: Record<string, any> }
  | { kind: "media"; item: Attachment; caption: string; replyToMsgId: string; ticket: string };
const failedSends = new Map<string, FailedSend>();

function submitText(name: string, params: Record<string, any>): Promise<void> {
  return sendReply
    .submit(params)
    .then((resp: any) => {
      failedSends.delete(name);
      emit("optimistic-resolve", {
        name,
        realName: resp?.name || "",
        message_id: resp?.message_id || "",
        status: resp?.status || "Success",
      });
    })
    .catch(() => {
      // Already toasted by onError. The bubble stays, marked Failed, with Retry.
      failedSends.set(name, { kind: "text", params });
      emit("optimistic-resolve", { name, realName: "", message_id: "", status: "Failed" });
    });
}

// ── Outbound hold ─────────────────────────────────────────────────────────────
// Meta bills every business message from 1 Oct 2026, so a reply the agent split
// across four bubbles is billed four times. Holding the first one briefly and
// merging whatever follows turns that burst into a single charge.

const heldText = ref("");
const heldSecondsLeft = ref(0);
let holdTicker: ReturnType<typeof setInterval> | null = null;

// The pending bubble that stands for whatever is held. Chunks merged into the
// hold update the same bubble; the flush resolves it; undo removes it.
let heldBubbleName: string | null = null;
let heldReplyTo = "";

// The most recent held send, so the media path can wait for it and keep the
// conversation in the order the agent typed it.
let heldSend: Promise<unknown> | null = null;

const outboundHold = new OutboundHold({
  holdMs: 0,
  onFlush({ ticket, message, reply_to_message_id }) {
    const name = heldBubbleName || `temp-${makeId()}`;
    heldBubbleName = null;
    heldReplyTo = "";
    syncHeld();
    // The bubble already shows this text; re-emitting with the merged message
    // is what makes it exact when several chunks were folded together.
    emit("optimistic", makePendingBubble({
      name,
      content_type: "text",
      message,
      reply_to_message_id,
      sender_full_name: senderName.value,
    }));
    // `ticket` comes from the payload, not props: the agent may already have
    // moved to another conversation by the time the window closes.
    heldSend = submitText(name, {
      ticket,
      message,
      content_type: "text",
      ...(reply_to_message_id ? { reply_to_message_id } : {}),
    });
  },
});

watch(
  () => props.outboundHoldSeconds,
  (s) => outboundHold.setHoldMs(Math.max(0, s || 0) * 1000),
  { immediate: true }
);

// If the component is reused across a ticket switch, send what is held rather
// than leaving it to expire — the payload already carries its own ticket, so
// this is about getting it out promptly and clearing a strip that would
// otherwise show the previous conversation's text.
watch(() => props.ticketId, () => flushHeld());

// Mirrors the buffer into refs and runs the countdown only while it has content.
function syncHeld() {
  heldText.value = outboundHold.pending;
  const deadline = outboundHold.deadline;
  heldSecondsLeft.value = deadline
    ? Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
    : 0;

  if (outboundHold.isHolding && holdTicker === null) {
    holdTicker = setInterval(syncHeld, 250);
  } else if (!outboundHold.isHolding && holdTicker !== null) {
    clearInterval(holdTicker);
    holdTicker = null;
  }
}

function flushHeld() {
  outboundHold.flush();
  syncHeld();
}

// Undo: put the held text back in the composer instead of discarding it.
function recallHeld() {
  const recalled = outboundHold.pending;
  outboundHold.cancel();
  syncHeld();
  if (heldBubbleName) {
    emit("optimistic-remove", heldBubbleName);
    heldBubbleName = null;
    heldReplyTo = "";
  }
  if (!recalled) return;
  text.value = text.value ? `${recalled}\n${text.value}` : recalled;
  autoResize();
  nextTick(() => textareaRef.value?.focus());
}

// Send a failed bubble again. The parent has already put it back to Pending.
async function retry(name: string) {
  const failed = failedSends.get(name);
  if (!failed) return;
  failedSends.delete(name);
  if (failed.kind === "text") {
    await submitText(name, failed.params);
  } else {
    await sendMediaUnit(failed.item, failed.caption, failed.replyToMsgId, name, failed.ticket);
  }
}

// The parent flushes through this when the customer replies — if they are
// already waiting on us, there is nothing left to batch.
defineExpose({ flush: flushHeld, retry });

// Upload+send one attachment via send_wa_media. Caption + reply target ride on
// the first file only; the rest go out bare, one WhatsApp Message per file.
// The preview URL is deliberately not revoked here: it backs the bubble until
// the stored row's own attach arrives, and everything is revoked on unmount.
async function sendMediaUnit(
  item: Attachment,
  caption: string,
  replyToMsgId: string,
  name: string,
  ticket: string
): Promise<boolean> {
  emit("optimistic", makePendingBubble({
    name,
    content_type: fileContentType(item.file),
    message: caption,
    attach: item.previewUrl,
    reply_to_message_id: replyToMsgId,
    sender_full_name: senderName.value,
  }));

  const formData = new FormData();
  formData.append("file", item.file, item.file.name);
  formData.append("ticket", ticket);
  formData.append("message", caption);
  formData.append("content_type", fileContentType(item.file));
  if (replyToMsgId) formData.append("reply_to_message_id", replyToMsgId);

  const fail = (label: string) => {
    toast.error(label);
    failedSends.set(name, { kind: "media", item, caption, replyToMsgId, ticket });
    emit("optimistic-resolve", { name, realName: "", message_id: "", status: "Failed" });
    return false;
  };

  try {
    const response = await fetch("/api/method/helpdesk.integrations.wa.send_wa_media", {
      method: "POST",
      headers: { "X-Frappe-CSRF-Token": (window as any).csrf_token ?? "" },
      body: formData,
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      return fail(err?.exc_type || "Media send failed");
    }
    const body = await response.json().catch(() => ({}));
    const stored = body?.message || {};
    failedSends.delete(name);
    emit("optimistic-resolve", {
      name,
      realName: stored.name || "",
      message_id: stored.message_id || "",
      status: stored.status || "Success",
    });
    return true;
  } catch {
    return fail("Media send failed");
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
    // The pending bubble goes in before the hold sees the text, so a hold of
    // zero (which flushes synchronously inside append) finds it to resolve.
    // Chunks folded into an open hold grow the same bubble.
    if (!heldBubbleName) {
      heldBubbleName = `temp-${makeId()}`;
      heldReplyTo = replyToMsgId;
    }
    const merged = outboundHold.pending ? `${outboundHold.pending}\n${caption}` : caption;
    emit("optimistic", makePendingBubble({
      name: heldBubbleName,
      content_type: "text",
      message: merged,
      reply_to_message_id: heldReplyTo,
      sender_full_name: senderName.value,
    }));
    // Goes through the hold, which submits it once the window closes (or
    // immediately when the hold is configured to 0).
    outboundHold.append(caption, props.ticketId, replyToMsgId);
    syncHeld();
    return;
  }

  // Media takes its own upload path, so anything still held has to go out
  // ahead of it or the conversation reads out of order. Issuing the text send
  // first is not enough — the two requests would be in flight together and
  // could be stored in either order, so wait for it to land.
  flushHeld();

  sending.value = true;
  let anyStored = false;
  try {
    if (heldSend) {
      // `await` tolerates a non-thenable, so this holds whatever submit()
      // returns. A failed text send has already toasted; the attachment
      // still goes out.
      try {
        await heldSend;
      } catch {
        // handled by sendReply.onError
      }
      heldSend = null;
    }
    for (let i = 0; i < items.length; i++) {
      const ok = await sendMediaUnit(
        items[i],
        i === 0 ? caption : "",
        i === 0 ? replyToMsgId : "",
        `temp-${makeId()}-${i}`,
        props.ticketId
      );
      if (!ok) {
        // The failed file has its bubble with Retry; the ones after it never
        // left, so they go back into the composer rather than vanishing.
        attachments.value = [...items.slice(i + 1), ...attachments.value];
        break;
      }
      anyStored = true;
    }
  } finally {
    sending.value = false;
    // Even a partial run stored rows the thread has not seen.
    if (anyStored) emit("delivered");
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
