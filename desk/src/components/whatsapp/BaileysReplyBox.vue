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
        :placeholder="attachment ? 'Add a caption (optional)...' : isGroup ? 'Type a message… use @ to mention' : 'Type a message...'"
        rows="1"
        class="flex-1 resize-none rounded-lg border border-outline-gray-3 bg-surface-white px-3 py-2 text-sm text-ink-gray-9 placeholder:text-ink-gray-4 focus:border-outline-gray-4 focus:outline-none disabled:opacity-50"
        @input="onTextInput"
        @keydown="onTextKeydown"
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
import { createListResource, createResource } from "frappe-ui";
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
const attachment = ref<File | null>(null);
const attachmentPreview = ref<string>("");

const isGroup = computed(() => props.jid?.endsWith("@g.us") ?? false);

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
}

onMounted(() => document.addEventListener("click", onDocClick));
onBeforeUnmount(() => document.removeEventListener("click", onDocClick));

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

async function send() {
  if ((!text.value.trim() && !attachment.value) || sending.value) return;
  bannerError.value = "";

  const caption = text.value.trim();
  const replyToId = props.replyTo?.message_id || "";
  const replyToText = props.replyTo?.message || "";
  const replyToFromMe = props.replyTo?.direction === "Outgoing";
  const file = attachment.value;
  const ct = contentType.value;
  const jidsToMention = [...mentionedJids.value];

  // Restore the composer exactly as it was — used only when a *pre-send* hard error
  // (upload failed, line down, API disabled) means the message never left.
  const restore = () => {
    text.value = caption;
    if (file) setFile(file);
    mentionedJids.value = jidsToMention;
    autoResize();
  };

  // Optimistic clear (textarea is disabled while sending, so nothing is typed mid-send).
  text.value = "";
  clearAttachment();
  mentionedJids.value = [];
  if (textareaRef.value) textareaRef.value.style.height = "auto";
  sending.value = true;

  try {
    let fileUrl = "";
    if (file) {
      // Step 1: upload the attachment to Frappe storage
      const uploadData = new FormData();
      uploadData.append("file", file, file.name);
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
      fileUrl = (await uploadResp.json())?.message?.file_url || "";
      if (!fileUrl) throw new Error("File upload returned no URL");
    }

    // Step 2: hand off to the WA API. A *send* failure (vs. pre-send) comes back as a
    // saved message with status "Failed" — the chat then shows a red ! bubble with a
    // Retry button, so we deliberately do NOT raise a banner for that case.
    await sendReply.submit({
      ...(props.jid ? { jid: props.jid } : { ticket: props.ticketId }),
      ...(props.line ? { line: props.line } : {}),
      message: caption,
      content_type: file ? ct : "text",
      ...(fileUrl ? { media_url: fileUrl } : {}),
      reply_to_message_id: replyToId,
      reply_to_text: replyToText,
      reply_to_from_me: replyToFromMe,
      ...(jidsToMention.length ? { mentioned_jids: JSON.stringify(jidsToMention) } : {}),
    });
    emit("sent");
  } catch (err: any) {
    // Pre-send hard failure: put the message back and tell the agent why.
    restore();
    bannerError.value =
      err?.messages?.[0] ||
      err?.message ||
      "Message couldn't be sent. Check the WhatsApp connection and try again.";
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
