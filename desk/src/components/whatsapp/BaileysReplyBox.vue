<template>
  <div
    class="border-t border-outline-gray-2 bg-surface-gray-1 px-4 py-3"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop"
    :class="{ '!bg-blue-50 ring-2 ring-inset ring-blue-400': dragging }"
  >
    <!-- Pre-send error banner (no phone, line down, upload failed, …) -->
    <div
      v-if="bannerError"
      class="mb-2 flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 px-3 py-2 dark:border-red-800 dark:bg-red-900/30"
    >
      <svg class="mt-0.5 shrink-0 text-red-500" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
        <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
      </svg>
      <p class="min-w-0 flex-1 text-xs text-red-700 dark:text-red-300">{{ bannerError }}</p>
      <button class="shrink-0 text-red-400 hover:text-red-600" title="Dismiss" @click="bannerError = ''">
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
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" class="text-ink-gray-5">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <polyline points="14 2 14 8 20 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <div class="min-w-0 max-w-[140px]">
          <p class="truncate text-xs font-medium text-ink-gray-7">{{ att.file.name }}</p>
          <p class="text-[11px] text-ink-gray-5">{{ fileContentType(att.file) }}</p>
        </div>
        <button class="shrink-0 text-ink-gray-4 hover:text-ink-gray-7" title="Remove" @click="removeAttachment(att.id)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
            <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </button>
      </div>
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

    <!-- Saved replies picker -->
    <Teleport to="body">
      <div
        v-if="showReplies"
        ref="repliesPopup"
        class="fixed z-50 flex flex-col rounded-xl border border-outline-gray-2 bg-surface-white shadow-xl"
        :style="repliesPopupStyle"
      >
        <div class="border-b border-outline-gray-2 px-3 py-2 text-xs font-semibold text-ink-gray-7">Saved Replies</div>
        <div class="px-2 py-1.5">
          <input
            v-model="repliesSearch"
            ref="repliesSearchRef"
            type="text"
            placeholder="Search..."
            class="w-full rounded-lg border border-outline-gray-3 bg-surface-gray-1 px-2 py-1 text-xs text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none"
            @keydown.esc="showReplies = false"
          />
        </div>
        <div class="max-h-56 overflow-y-auto">
          <div
            v-if="!filteredReplies.length"
            class="px-3 py-4 text-center text-xs text-ink-gray-4"
          >
            {{ savedReplies.loading ? "Loading…" : "No saved replies" }}
          </div>
          <button
            v-for="reply in filteredReplies"
            :key="reply.name"
            class="flex w-full flex-col items-start gap-0.5 rounded-lg px-3 py-2 text-left hover:bg-surface-gray-1"
            @click="applyReply(reply)"
          >
            <span class="text-xs font-semibold text-ink-gray-8">{{ reply.title }}</span>
            <span class="line-clamp-2 text-[11px] text-ink-gray-5">{{ reply._plain }}</span>
          </button>
        </div>
      </div>
    </Teleport>

    <!-- @mention picker -->
    <Teleport to="body">
      <div
        v-if="showMentionPicker && filteredParticipants.length"
        ref="mentionPickerRef"
        class="fixed z-50 overflow-hidden rounded-xl border border-outline-gray-2 bg-surface-white shadow-xl"
        :style="mentionPickerStyle"
      >
        <div v-if="participantsResource.loading" class="px-3 py-3 text-center text-xs text-ink-gray-4">
          Loading members…
        </div>
        <div v-else class="max-h-52 overflow-y-auto">
          <button
            v-for="(p, i) in filteredParticipants"
            :key="p.jid"
            class="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-surface-gray-1"
            :class="{ 'bg-surface-gray-1': i === mentionIndex }"
            @mousedown.prevent="insertMention(p)"
          >
            <div class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-gray-3 text-[10px] font-bold text-ink-gray-7">
              {{ (p.name || p.phone || "?")[0].toUpperCase() }}
            </div>
            <div class="min-w-0 flex-1">
              <div class="truncate text-xs font-medium text-ink-gray-8">{{ p.name || "+" + p.phone }}</div>
              <div v-if="p.name && p.phone" class="text-[10px] text-ink-gray-4">+{{ p.phone }}</div>
            </div>
            <span v-if="p.isAdmin" class="shrink-0 rounded-full bg-blue-100 px-1.5 py-0.5 text-[10px] text-blue-600">admin</span>
          </button>
        </div>
      </div>
    </Teleport>

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

      <!-- Saved replies -->
      <button
        ref="repliesBtn"
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-outline-gray-3 text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
        :class="{ 'bg-surface-gray-1 text-ink-gray-8': showReplies }"
        title="Saved replies"
        @click.stop="toggleReplies"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          <line x1="9" y1="10" x2="15" y2="10"/>
          <line x1="9" y1="14" x2="13" y2="14"/>
        </svg>
      </button>

      <!-- AI suggest reply -->
      <button
        ref="aiBtn"
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-outline-gray-3 text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
        :class="{ 'bg-surface-gray-1 text-ink-gray-8 border-purple-300 text-purple-600': showAiSuggestion }"
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
      <input
        ref="fileInput"
        type="file"
        multiple
        class="hidden"
        accept="image/*,video/*,audio/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip"
        @change="onFileSelected"
      />

      <textarea
        ref="textareaRef"
        v-model="text"
        :disabled="sending"
        :placeholder="attachments.length ? 'Add a caption (optional)...' : isGroup ? 'Type a message… use @ to mention' : 'Type a message...'"
        rows="1"
        class="flex-1 resize-none rounded-lg border border-outline-gray-3 bg-surface-white px-3 py-2 text-sm text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none disabled:opacity-50"
        @input="onTextInput"
        @keydown="onTextKeydown"
        @paste="onPaste"
      />
      <button
        :disabled="(!text.trim() && !attachments.length) || sending"
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
    <p v-if="dragging" class="mt-1 text-center text-xs text-blue-500">Drop files to attach</p>
  </div>
</template>

<script setup lang="ts">
import { call, createListResource, createResource, toast } from "frappe-ui";
import { ref, computed, nextTick, onMounted, onBeforeUnmount, watch } from "vue";

interface Participant {
  jid: string;
  phone: string;
  name: string;
  isAdmin: boolean;
}

const props = defineProps<{
  ticketId: string;
  replyTo?: Record<string, any> | null;
  jid?: string | null;
  line?: string;
}>();

const emit = defineEmits<{
  (e: "sent"): void;
  (e: "clearReply"): void;
  // Optimistic-send lifecycle: parent renders a pending bubble immediately, then
  // resolves it to the real message_id (so the realtime reload dedupes it) or
  // removes it on a pre-send failure.
  (e: "optimistic", msg: Record<string, any>): void;
  (e: "optimistic-resolve", payload: { name: string; realName: string; message_id: string; status: string }): void;
  (e: "optimistic-remove", name: string): void;
}>();

const text = ref("");
const sending = ref(false);
const bannerError = ref("");
const dragging = ref(false);
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

watch(() => props.replyTo, (val) => {
  if (val) nextTick(() => textareaRef.value?.focus());
});
interface Attachment {
  id: string;
  file: File;
  previewUrl: string;
  isImage: boolean;
}

const attachments = ref<Attachment[]>([]);
// Every object URL we create is tracked here and revoked on unmount, so preview
// URLs that back in-flight optimistic bubbles aren't freed while still on screen.
const objectUrls = new Set<string>();

const isGroup = computed(() => props.jid?.endsWith("@g.us") ?? false);

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

// Downscale large images before upload to cut both the Frappe upload and the
// Evolution send. Non-images, GIFs, small images, or any failure fall through to
// the original file unchanged.
async function downscaleImage(file: File): Promise<File> {
  if (!file.type.startsWith("image/") || file.type === "image/gif") return file;
  const MAX_EDGE = 1600;
  try {
    const bitmap = await createImageBitmap(file);
    const longEdge = Math.max(bitmap.width, bitmap.height);
    if (longEdge <= MAX_EDGE) { bitmap.close?.(); return file; }
    const scale = MAX_EDGE / longEdge;
    const w = Math.round(bitmap.width * scale);
    const h = Math.round(bitmap.height * scale);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) { bitmap.close?.(); return file; }
    ctx.drawImage(bitmap, 0, 0, w, h);
    bitmap.close?.();
    const blob: Blob | null = await new Promise((resolve) =>
      canvas.toBlob((b) => resolve(b), "image/jpeg", 0.8)
    );
    // Only accept the re-encode if it actually shrank the payload.
    if (!blob || blob.size >= file.size) return file;
    const newName = file.name.replace(/\.[^.]+$/, "") + ".jpg";
    return new File([blob], newName, { type: "image/jpeg" });
  } catch {
    return file;
  }
}

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
  if (showAiSuggestion.value) {
    showAiSuggestion.value = false;
    return;
  }
  aiLoading.value = true;
  try {
    const result = await call("helpdesk.integrations.bot.suggest_agent_reply", {
      ticket: props.ticketId,
      channel: "wa_line",
    });
    if (!result) {
      toast.warning("Not enough conversation history to suggest a reply.");
      return;
    }
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

// ── Saved replies ─────────────────────────────────────────────────────────────
const showReplies = ref(false);
const repliesSearch = ref("");
const repliesBtn = ref<HTMLButtonElement | null>(null);
const repliesPopup = ref<HTMLElement | null>(null);
const repliesSearchRef = ref<HTMLInputElement | null>(null);
const repliesPopupStyle = ref<Record<string, string>>({});

const savedReplies = createListResource({
  doctype: "HD Saved Reply",
  fields: ["name", "title", "message"],
  orderBy: "title asc",
  pageLength: 999,
  auto: false,
  transform(rows: any[]) {
    return rows.map((r) => ({
      ...r,
      _plain: htmlToPlain(r.message || ""),
      _waText: htmlToWa(r.message || ""),
    }));
  },
});

function htmlToPlain(html: string): string {
  const div = document.createElement("div");
  div.innerHTML = html;
  return (div.innerText || div.textContent || "").trim();
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

const filteredReplies = computed(() => {
  const list: any[] = savedReplies.data || [];
  if (!repliesSearch.value.trim()) return list;
  const q = repliesSearch.value.toLowerCase();
  return list.filter(
    (r) => r.title.toLowerCase().includes(q) || r._plain.toLowerCase().includes(q)
  );
});

function positionPopup() {
  if (!repliesBtn.value) return;
  const rect = repliesBtn.value.getBoundingClientRect();
  repliesPopupStyle.value = {
    bottom: `${window.innerHeight - rect.top + 8}px`,
    left: `${rect.left}px`,
    width: "320px",
  };
}

function toggleReplies() {
  if (showReplies.value) {
    showReplies.value = false;
    return;
  }
  if (!savedReplies.data) savedReplies.reload();
  showReplies.value = true;
  nextTick(() => {
    positionPopup();
    repliesSearchRef.value?.focus();
  });
}

function applyReply(reply: any) {
  text.value = reply._waText;
  showReplies.value = false;
  nextTick(() => {
    autoResize();
    textareaRef.value?.focus();
  });
}

function onDocClick(e: MouseEvent) {
  if (
    showReplies.value &&
    !repliesPopup.value?.contains(e.target as Node) &&
    !repliesBtn.value?.contains(e.target as Node)
  ) {
    showReplies.value = false;
  }
  if (showMentionPicker.value && !mentionPickerRef.value?.contains(e.target as Node)) {
    showMentionPicker.value = false;
  }
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

watch(showReplies, (v) => { if (!v) repliesSearch.value = ""; });

// ── @mention picker ───────────────────────────────────────────────────────────
const showMentionPicker = ref(false);
const mentionQuery = ref("");
const mentionAtPos = ref(0);
const mentionIndex = ref(0);
const mentionedJids = ref<string[]>([]);
const mentionPickerRef = ref<HTMLElement | null>(null);
const mentionPickerStyle = ref<Record<string, string>>({});

const participantsResource = createResource({
  url: "helpdesk.integrations.wa.get_wa_group_participants",
  auto: false,
});

const participants = computed<Participant[]>(() => participantsResource.data || []);

const filteredParticipants = computed(() => {
  const q = mentionQuery.value.toLowerCase();
  const list = participants.value;
  if (!q) return list.slice(0, 20);
  return list
    .filter((p) =>
      (p.name || "").toLowerCase().includes(q) ||
      (p.phone || "").includes(q)
    )
    .slice(0, 20);
});

function positionMentionPicker() {
  if (!textareaRef.value) return;
  const rect = textareaRef.value.getBoundingClientRect();
  const pickerHeight = Math.min(filteredParticipants.value.length, 6) * 44 + 8;
  mentionPickerStyle.value = {
    bottom: `${window.innerHeight - rect.top + 4}px`,
    left: `${rect.left}px`,
    width: `${rect.width}px`,
    maxWidth: "340px",
  };
}

function detectMention() {
  if (!isGroup.value) return;
  const ta = textareaRef.value;
  if (!ta) return;
  const cursorPos = ta.selectionStart ?? 0;
  const before = text.value.slice(0, cursorPos);
  // Match an @ that isn't preceded by a non-space character (word boundary)
  const match = before.match(/@([^@\s]*)$/);
  if (match) {
    if (!participantsResource.data && !participantsResource.loading && props.jid) {
      participantsResource.submit({ jid: props.jid, line: props.line || "" });
    }
    mentionQuery.value = match[1].toLowerCase();
    mentionAtPos.value = before.lastIndexOf("@");
    mentionIndex.value = 0;
    showMentionPicker.value = true;
    nextTick(positionMentionPicker);
  } else {
    showMentionPicker.value = false;
  }
}

function insertMention(p: Participant) {
  const ta = textareaRef.value;
  if (!ta) return;
  const cursorPos = ta.selectionStart ?? 0;
  const displayName = p.name || (p.phone ? `+${p.phone}` : "Unknown");
  const before = text.value.slice(0, mentionAtPos.value);
  const after = text.value.slice(cursorPos);
  const insert = `@${displayName} `;
  text.value = before + insert + after;
  if (!mentionedJids.value.includes(p.jid)) {
    mentionedJids.value.push(p.jid);
  }
  showMentionPicker.value = false;
  nextTick(() => {
    const newPos = before.length + insert.length;
    ta.selectionStart = ta.selectionEnd = newPos;
    ta.focus();
    autoResize();
  });
}

// ── Input / keyboard handlers ─────────────────────────────────────────────────
function onTextInput() {
  autoResize();
  detectMention();
}

function onTextKeydown(e: KeyboardEvent) {
  // Intercept arrow/enter/esc when mention picker is open
  if (showMentionPicker.value && filteredParticipants.value.length) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      mentionIndex.value = (mentionIndex.value + 1) % filteredParticipants.value.length;
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      mentionIndex.value = (mentionIndex.value - 1 + filteredParticipants.value.length) % filteredParticipants.value.length;
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      insertMention(filteredParticipants.value[mentionIndex.value]);
      return;
    }
    if (e.key === "Escape") {
      showMentionPicker.value = false;
      return;
    }
  }
  // Normal enter = send (no shift)
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
}

// ── Send ──────────────────────────────────────────────────────────────────────
const sendReply = createResource({
  url: "helpdesk.integrations.wa.send_wa_reply",
});

// Upload one file to Frappe storage (with client-side downscale for images) and
// return its file_url. Throws on a pre-send hard failure.
async function uploadOne(file: File): Promise<string> {
  const toUpload = await downscaleImage(file);
  const uploadData = new FormData();
  uploadData.append("file", toUpload, toUpload.name);
  uploadData.append("is_private", "0");
  const csrfToken = (window as any).frappe?.csrf_token || (window as any).csrf_token || "";
  const uploadResp = await fetch("/api/method/upload_file", {
    method: "POST",
    headers: { "X-Frappe-CSRF-Token": csrfToken },
    body: uploadData,
  });
  if (!uploadResp.ok) {
    const errData = await uploadResp.json().catch(() => ({}));
    throw new Error(errData?.exc_type || "File upload failed");
  }
  const fileUrl = (await uploadResp.json())?.message?.file_url || "";
  if (!fileUrl) throw new Error("File upload returned no URL");
  return fileUrl;
}

async function send() {
  if ((!text.value.trim() && attachments.value.length === 0) || sending.value) return;
  bannerError.value = "";

  // Snapshot props immediately — before any await — so navigation to another
  // ticket can't mutate these out from under an in-flight upload.
  const ticketId = props.ticketId;
  const jid = props.jid;
  const line = props.line;

  const caption = text.value.trim();
  const replyToId = props.replyTo?.message_id || "";
  const replyToText = props.replyTo?.message || "";
  const replyToFromMe = props.replyTo?.direction === "Outgoing";
  const items = [...attachments.value];
  const jidsToMention = [...mentionedJids.value];

  // Put the composer back after a *pre-send* hard failure. `keepFirst` restores the
  // caption + mentions too (only meaningful when the first item — which carries
  // them — never made it out).
  const restore = (remaining: Attachment[], keepFirst: boolean) => {
    if (keepFirst) {
      text.value = caption;
      mentionedJids.value = jidsToMention;
    }
    attachments.value = remaining;
    autoResize();
  };

  // Optimistic clear (textarea is disabled while sending, so nothing is typed mid-send).
  // Detach the attachment list without revoking preview URLs — they back the pending
  // bubbles until each item's real message arrives.
  text.value = "";
  attachments.value = [];
  mentionedJids.value = [];
  if (fileInput.value) fileInput.value.value = "";
  if (textareaRef.value) textareaRef.value.style.height = "auto";
  sending.value = true;

  const nowIso = () => new Date().toISOString();

  // Send one unit of work: emit its pending bubble, upload if it has a file, hand
  // off to the WA API, then resolve the bubble to the real message_id. A *send*
  // failure (vs. pre-send) comes back as a saved "Failed" message that renders via
  // the realtime reload with a Retry button, so we do NOT banner that case.
  const sendUnit = async (opts: {
    tempName: string;
    content_type: string;
    message: string;
    previewUrl: string;
    replyId: string;
    replyText: string;
    replyFromMe: boolean;
    mentions: string[];
    file?: File;
  }) => {
    emit("optimistic", {
      name: opts.tempName,
      _optimistic: true,
      type: "Outgoing",
      direction: "Outgoing",
      content_type: opts.content_type,
      message: opts.message,
      attach: opts.previewUrl,
      media_url: opts.previewUrl,
      status: "Pending",
      message_id: "",
      creation: nowIso(),
      is_reply: opts.replyId ? 1 : 0,
      reply_to_message_id: opts.replyId,
      sender_name: "",
      profile_name: "",
    });
    let fileUrl = "";
    if (opts.file) fileUrl = await uploadOne(opts.file);
    const resp: any = await sendReply.submit({
      ...(jid ? { jid } : { ticket: ticketId }),
      ...(line ? { line } : {}),
      message: opts.message,
      content_type: opts.content_type,
      ...(fileUrl ? { media_url: fileUrl } : {}),
      reply_to_message_id: opts.replyId,
      reply_to_text: opts.replyText,
      reply_to_from_me: opts.replyFromMe,
      ...(opts.mentions.length ? { mentioned_jids: JSON.stringify(opts.mentions) } : {}),
    });
    // Stamp the real identity so the realtime reload dedupes against this bubble
    // instead of appending a duplicate. A rejected send returns an empty
    // message_id but still persists a "Failed" row, so we also carry its docname.
    emit("optimistic-resolve", {
      name: opts.tempName,
      realName: resp?.name || "",
      message_id: resp?.message_id || "",
      status: resp?.status || "Sent",
    });
  };

  try {
    if (items.length === 0) {
      const tempName = `temp-${makeId()}`;
      try {
        await sendUnit({
          tempName,
          content_type: "text",
          message: caption,
          previewUrl: "",
          replyId: replyToId,
          replyText: replyToText,
          replyFromMe: replyToFromMe,
          mentions: jidsToMention,
        });
      } catch (err: any) {
        emit("optimistic-remove", tempName);
        restore([], true);
        bannerError.value =
          err?.messages?.[0] || err?.message ||
          "Message couldn't be sent. Check the WhatsApp connection and try again.";
        return;
      }
      emit("sent");
      return;
    }

    // One WA Message per file, sequentially. Caption + reply + mentions ride on the
    // first file only; the rest go out bare.
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const first = i === 0;
      const tempName = `temp-${makeId()}-${i}`;
      try {
        await sendUnit({
          tempName,
          content_type: fileContentType(item.file),
          message: first ? caption : "",
          previewUrl: item.previewUrl,
          replyId: first ? replyToId : "",
          replyText: first ? replyToText : "",
          replyFromMe: first ? replyToFromMe : false,
          mentions: first ? jidsToMention : [],
          file: item.file,
        });
      } catch (err: any) {
        // Pre-send failure on this file: drop its pending bubble, restore the
        // remaining (unsent) files + caption, banner, and stop.
        emit("optimistic-remove", tempName);
        restore(items.slice(i), first);
        bannerError.value =
          err?.messages?.[0] || err?.message ||
          "Message couldn't be sent. Check the WhatsApp connection and try again.";
        return;
      }
      // The item's real message is on its way in via the realtime reload; its
      // preview URL stays valid until unmount so the resolved bubble doesn't flash.
      emit("sent");
    }
  } finally {
    sending.value = false;
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
