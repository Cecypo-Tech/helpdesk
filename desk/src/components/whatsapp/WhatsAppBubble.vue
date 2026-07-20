<template>
  <div class="flex" :class="isOutgoing ? 'justify-end' : 'justify-start'" v-bind="$attrs">
    <!-- Inner wrapper: limits width, anchors the action bar + emoji picker -->
    <div class="group relative max-w-[70%]">

      <!-- Action bar — shown on hover or while emoji picker is open -->
      <div
        v-if="!isDeleted"
        class="pointer-events-none absolute top-0 z-10 flex items-center gap-1 transition-opacity duration-100"
        :class="[
          isOutgoing ? 'right-full mr-2' : 'left-full ml-2',
          showEmojiPicker ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
        ]"
      >
        <!-- Reply button -->
        <button
          class="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-full border border-outline-gray-2 bg-surface-white text-ink-gray-5 shadow-sm hover:bg-surface-gray-1 hover:text-ink-gray-8"
          title="Reply"
          @click.stop="$emit('reply', message)"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>
          </svg>
        </button>
        <!-- Copy button -->
        <button
          v-if="message.message"
          class="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-full border border-outline-gray-2 bg-surface-white text-ink-gray-5 shadow-sm hover:bg-surface-gray-1 hover:text-ink-gray-8"
          :title="copied ? 'Copied!' : 'Copy text'"
          @click.stop="copyText"
        >
          <svg v-if="!copied" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
          </svg>
          <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-green-600">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
        </button>
        <!-- Edit button (outgoing text only, WA Line only) -->
        <button
          v-if="allowEdit && isOutgoing && message.content_type === 'text' && message.message_id"
          class="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-full border border-outline-gray-2 bg-surface-white text-ink-gray-5 shadow-sm hover:bg-surface-gray-1 hover:text-ink-gray-8"
          title="Edit message"
          @click.stop="startEdit"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>
        <!-- React button -->
        <button
          class="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-full border border-outline-gray-2 bg-surface-white text-ink-gray-5 shadow-sm hover:bg-surface-gray-1 hover:text-ink-gray-8"
          title="React"
          @click.stop="toggleEmojiPicker"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>
          </svg>
        </button>
        <!-- Delete button (own outgoing only — WhatsApp only deletes what you sent) -->
        <button
          v-if="allowDelete && isOutgoing && message.message_id"
          class="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-full border border-outline-gray-2 bg-surface-white text-ink-gray-5 shadow-sm hover:bg-surface-gray-1 hover:text-red-600"
          title="Delete for everyone"
          @click.stop="$emit('delete', message.name)"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
          </svg>
        </button>
      </div>

      <!-- Emoji picker panel — no backdrop, uses document click listener to close -->
      <div
        v-if="showEmojiPicker"
        ref="emojiPickerRef"
        class="absolute bottom-full mb-2 flex items-center gap-0.5 rounded-full border border-outline-gray-2 bg-surface-white px-2 py-1 shadow-lg"
        :class="isOutgoing ? 'right-0' : 'left-0'"
        style="z-index: 9990;"
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
        class="rounded-lg px-3 py-2 text-sm shadow-sm transition-colors"
        :class="
          isOutgoing
            ? 'bg-green-100 dark:bg-green-900 text-ink-gray-9 group-hover:bg-green-200 dark:group-hover:bg-green-800'
            : 'bg-surface-white text-ink-gray-9 border border-outline-gray-2 group-hover:bg-surface-gray-1'
        "
      >
        <!-- Profile name for incoming (color-coded per sender for group chats) -->
        <div
          v-if="!isOutgoing && message.profile_name"
          class="mb-1 flex items-center gap-1.5 text-xs font-medium"
        >
          <span :style="{ color: senderColor }">{{ message.profile_name }}</span>
          <button
            v-if="isGroup && message.sender_phone"
            class="inline-flex items-center gap-0.5 rounded px-1 py-0.5 font-normal text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-7"
            :title="phoneCopied ? 'Copied!' : `Copy +${message.sender_phone}`"
            @click.stop="copySenderPhone"
          >
            <span class="tabular-nums">+{{ message.sender_phone }}</span>
            <svg v-if="!phoneCopied" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
            <svg v-else width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-green-600">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
          </button>
        </div>
        <!-- Sender name for outgoing -->
        <div
          v-if="isOutgoing && message.sender_full_name"
          class="mb-1 text-right text-xs font-medium text-ink-gray-5"
        >
          {{ message.sender_full_name }}
        </div>

        <!-- Deleted for everyone: the content is gone, so nothing below renders -->
        <div
          v-if="isDeleted"
          class="flex items-center gap-1.5 text-sm italic text-ink-gray-5"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>
          </svg>
          <span>{{ isOutgoing ? "You deleted this message" : "This message was deleted" }}</span>
        </div>

        <template v-else>
        <!-- Reply context block -->
        <div
          v-if="message.is_reply"
          class="mb-2 cursor-pointer overflow-hidden rounded border-l-2 text-xs transition-opacity hover:opacity-80"
          @click.stop="$emit('scrollToReply', message.reply_to_message_id)"
          :class="isOutgoing
            ? 'border-green-500 bg-green-50 dark:border-green-400 dark:bg-green-800/50'
            : 'border-blue-400 bg-surface-gray-1'"
        >
          <!-- Image reply: show thumbnail on right -->
          <div v-if="replyToMessage?.content_type === 'image' && replyToMessage?.attach" class="flex items-stretch">
            <div class="flex-1 px-2 py-1">
              <div class="mb-0.5 font-medium" :class="isOutgoing ? 'text-green-700 dark:text-green-400' : 'text-blue-600'">
                {{ replyToSenderName }}
              </div>
              <div class="flex items-center gap-1 text-ink-gray-5">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                Photo
              </div>
            </div>
            <img
              :src="replyToMessage.thumbnail_url || replyToMessage.attach"
              loading="lazy"
              decoding="async"
              class="h-14 w-14 shrink-0 object-cover"
            />
          </div>
          <!-- Default: text preview -->
          <div v-else class="px-2 py-1">
            <div v-if="replyToSenderName" class="mb-0.5 font-medium" :class="isOutgoing ? 'text-green-700 dark:text-green-400' : 'text-blue-600'">
              {{ replyToSenderName }}
            </div>
            <div class="truncate text-ink-gray-5" :class="{ italic: !replyToMessage }">{{ replyPreview }}</div>
          </div>
        </div>

        <!-- Image attachment -->
        <div v-if="message.content_type === 'image' && message.attach" class="mb-1">
          <div v-if="mediaBroken" class="flex items-center gap-2 rounded bg-surface-gray-2 px-3 py-2 text-xs italic text-ink-gray-5">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
            Media expired
          </div>
          <div v-else-if="mediaRefetching" class="flex items-center gap-2 px-1 py-1 text-xs text-ink-gray-5">
            <svg class="animate-spin" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
            Loading…
          </div>
          <img
            v-else
            :src="thumbSrc"
            loading="lazy"
            decoding="async"
            class="max-h-60 max-w-full cursor-pointer rounded"
            @click="openLightbox(mediaSrc, 'image')"
            @error="handleImageError"
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
          <audio controls :src="mediaSrc" class="max-w-full" @error="handleMediaError" />
        </div>
        <div
          v-else-if="message.content_type === 'audio' && !message.attach"
          class="mb-1 flex items-center gap-2 text-xs italic text-ink-gray-5"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/></svg>
          Voice message
        </div>

        <!-- Video -->
        <div v-if="message.content_type === 'video' && message.attach" class="mb-1">
          <div v-if="mediaBroken" class="flex items-center gap-2 rounded bg-surface-gray-2 px-3 py-2 text-xs italic text-ink-gray-5">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
            Media expired
          </div>
          <div v-else-if="mediaRefetching" class="flex items-center gap-2 px-1 py-1 text-xs text-ink-gray-5">
            <svg class="animate-spin" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
            Loading…
          </div>
          <video
            v-else
            :src="mediaSrc"
            :poster="thumbnailUrl || undefined"
            controls
            preload="none"
            class="max-h-60 max-w-full cursor-pointer rounded"
            @click.stop="openLightbox(mediaSrc, 'video')"
            @error="handleMediaError"
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

        <!-- Sticker -->
        <div v-if="message.content_type === 'sticker'" class="mb-1">
          <img v-if="message.attach" :src="message.attach" class="h-24 w-24 object-contain" />
          <span v-else class="text-2xl">🎭</span>
        </div>

        <!-- Location -->
        <div v-if="message.content_type === 'location'" class="mb-1 flex items-center gap-2 rounded bg-surface-gray-2 px-3 py-2 text-xs text-ink-gray-7">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
          <span>{{ message.message || "Location" }}</span>
        </div>

        <!-- Contact card -->
        <div v-if="message.content_type === 'contact'" class="mb-1 flex items-center gap-2 rounded bg-surface-gray-2 px-3 py-2 text-xs text-ink-gray-7">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
          <span>{{ message.message || "Contact" }}</span>
        </div>

        <!-- Poll -->
        <div v-if="message.content_type === 'poll'" class="mb-1 flex items-center gap-2 rounded bg-surface-gray-2 px-3 py-2 text-xs text-ink-gray-7">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
          <span>{{ message.message || "Poll" }}</span>
        </div>

        <!-- Message text / inline editor -->
        <template v-if="editing">
          <textarea
            ref="editTextareaRef"
            v-model="editText"
            class="w-full resize-none rounded border border-outline-gray-3 bg-surface-white px-2 py-1 text-sm text-ink-gray-9 focus:outline-none dark:bg-surface-gray-1 dark:text-ink-gray-9"
            rows="3"
            @keydown.ctrl.enter="saveEdit"
            @keydown.esc="cancelEdit"
          />
          <div class="mt-1 flex justify-end gap-1.5">
            <button
              class="text-xs text-ink-gray-5 hover:text-ink-gray-8"
              @click.stop="cancelEdit"
            >Cancel</button>
            <button
              class="text-xs font-medium text-blue-600 hover:underline"
              @click.stop="saveEdit"
            >Save</button>
          </div>
        </template>
        <div v-else-if="message.message" class="whitespace-pre-wrap break-words" v-html="formattedMessage" />
        </template>

        <!-- Footer: time + status -->
        <div class="mt-1 flex items-center justify-end gap-1">
          <span class="text-[10px] text-ink-gray-5">{{ formattedTime }}</span>
          <!-- Edited badge + history tooltip -->
          <span
            v-if="message.is_edited && !isDeleted"
            class="relative text-[10px] italic text-ink-gray-4 cursor-default select-none"
            @mouseenter="showEditHistory = true"
            @mouseleave="showEditHistory = false"
          >
            · edited
            <div
              v-if="showEditHistory && editHistoryList.length"
              class="absolute bottom-full right-0 mb-1 z-30 w-56 rounded border border-outline-gray-2 bg-surface-white shadow-md overflow-y-auto"
              style="max-height: 140px;"
            >
              <div
                v-for="h in editHistoryList"
                :key="h.edited_at"
                class="border-b border-outline-gray-1 px-2 py-1.5 last:border-0 text-[11px] text-ink-gray-7"
              >
                <div class="mb-0.5 text-ink-gray-4 font-medium">{{ formatEditTime(h.edited_at) }} · {{ h.edited_by }}</div>
                <div class="whitespace-pre-wrap break-words">{{ h.old_message || '(empty)' }}</div>
              </div>
            </div>
          </span>
          <!-- Delivery state is meaningless once a message is deleted, and Retry
               would re-send something the sender deliberately removed. -->
          <span v-if="isOutgoing && !isDeleted" class="flex items-center gap-1 text-[10px] leading-none">
            <!-- pending: clock -->
            <svg v-if="statusKind === 'pending'" class="text-ink-gray-4" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><title>Sending…</title><circle cx="12" cy="12" r="9"/><polyline points="12 7.5 12 12 15 13.5"/></svg>
            <!-- sent: single grey tick -->
            <span v-else-if="statusKind === 'sent'" class="text-ink-gray-5" title="Sent">✓</span>
            <!-- delivered: double grey tick -->
            <span v-else-if="statusKind === 'delivered'" class="text-ink-gray-5" title="Delivered">✓✓</span>
            <!-- read: double blue tick -->
            <span v-else-if="statusKind === 'read'" class="text-blue-500" title="Read">✓✓</span>
            <!-- failed: red warning + optional one-tap retry -->
            <template v-else-if="statusKind === 'failed'">
              <span class="font-bold text-red-500" title="Not delivered">!</span>
              <button
                v-if="allowRetry"
                class="font-medium text-red-500 hover:underline"
                @click.stop="$emit('retry', message.name)"
              >Retry</button>
            </template>
          </span>
        </div>
      </div>

      <!-- Reaction badges (below bubble) — z-20 so they paint above the next message's action bar (z-10) -->
      <div
        v-if="aggregatedReactions.length && !isDeleted"
        class="relative z-20 mt-0.5 flex flex-wrap gap-1"
        :class="isOutgoing ? 'justify-end' : 'justify-start'"
      >
        <button
          v-for="r in aggregatedReactions"
          :key="r.emoji"
          class="relative flex cursor-pointer items-center gap-0.5 rounded-full border border-outline-gray-2 bg-surface-white px-1.5 py-0.5 text-xs shadow-sm hover:border-outline-gray-3"
          :class="r.hasOwn ? 'border-blue-300 bg-blue-50' : ''"
          :title="r.hasOwn ? 'Remove your reaction' : `React with ${r.emoji}`"
          @click.stop="toggleReaction(r)"
          @mouseenter="(e) => showTooltip(e, r.senders)"
          @mouseleave="hideTooltip"
        >
          {{ r.emoji }}<span v-if="r.count > 1" class="ml-0.5 text-ink-gray-5">{{ r.count }}</span>
        </button>
      </div>

    </div>
  </div>

  <!-- Reaction sender tooltip (teleported to body to escape overflow:hidden parents) -->
  <Teleport to="body">
    <div
      v-if="tooltip.visible"
      class="pointer-events-none fixed z-[9999] whitespace-nowrap rounded px-2.5 py-1.5 text-[11px] text-white shadow-lg"
      style="background: rgba(30,30,30,0.95); transform: translateX(-50%) translateY(-100%)"
      :style="{ top: tooltip.y + 'px', left: tooltip.x + 'px' }"
    >
      {{ tooltip.text }}
    </div>
  </Teleport>

  <!-- Lightbox -->
  <Teleport to="body">
    <div
      v-if="lightbox.open"
      ref="lightboxRef"
      class="fixed inset-0 z-[9999] flex flex-col bg-black/92 outline-none"
      tabindex="0"
      @wheel.prevent="onLightboxWheel"
      @keydown.esc="closeLightbox"
      @keydown.equal.prevent="zoomIn"
      @keydown.minus.prevent="zoomOut"
      @keydown.0.prevent="resetZoom"
    >
      <!-- Top bar -->
      <div class="flex shrink-0 items-center justify-between px-4 py-2.5">
        <span class="text-xs text-white/50 tabular-nums">{{ Math.round(lightbox.scale * 100) }}%</span>
        <div class="flex items-center gap-1">
          <!-- Open in new tab -->
          <a
            :href="lightbox.src"
            target="_blank"
            rel="noopener"
            class="flex h-8 w-8 items-center justify-center rounded-full text-white/70 hover:bg-white/10 hover:text-white"
            title="Open original"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
              <polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
            </svg>
          </a>
          <!-- Fullscreen toggle -->
          <button
            class="flex h-8 w-8 items-center justify-center rounded-full text-white/70 hover:bg-white/10 hover:text-white"
            :title="isFullscreen ? 'Exit fullscreen' : 'Fullscreen'"
            @click="toggleFullscreen"
          >
            <svg v-if="!isFullscreen" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>
              <line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>
            </svg>
            <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="8 3 3 3 3 8"/><polyline points="21 8 21 3 16 3"/>
              <polyline points="3 16 3 21 8 21"/><polyline points="16 21 21 21 21 16"/>
            </svg>
          </button>
          <!-- Close -->
          <button
            class="flex h-8 w-8 items-center justify-center rounded-full text-white/70 hover:bg-white/10 hover:text-white"
            title="Close (Esc)"
            @click="closeLightbox"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
      </div>

      <!-- Media area -->
      <div
        class="flex flex-1 items-center justify-center overflow-hidden select-none"
        :class="lightbox.scale > 1 ? (lightbox.dragging ? 'cursor-grabbing' : 'cursor-grab') : 'cursor-zoom-in'"
        @click.self="closeLightbox"
        @mousedown="onDragStart"
        @mousemove="onDragMove"
        @mouseup="onDragEnd"
        @mouseleave="onDragEnd"
        @dblclick="onLightboxDblClick"
      >
        <img
          v-if="lightbox.type === 'image'"
          :src="lightbox.src"
          draggable="false"
          :style="{
            transform: `translate(${lightbox.offsetX}px, ${lightbox.offsetY}px) scale(${lightbox.scale})`,
            transformOrigin: 'center center',
            transition: lightbox.dragging ? 'none' : 'transform 0.15s ease',
            maxHeight: '100%',
            maxWidth: '100%',
          }"
          class="rounded object-contain"
          @click.stop
        />
        <video
          v-else-if="lightbox.type === 'video'"
          :src="lightbox.src"
          controls
          autoplay
          class="max-h-full max-w-full rounded"
        />
      </div>

      <!-- Zoom controls -->
      <div v-if="lightbox.type === 'image'" class="flex shrink-0 items-center justify-center gap-2 py-2.5">
        <button
          class="flex h-7 w-7 items-center justify-center rounded-full border border-white/20 text-white/70 hover:bg-white/10 hover:text-white disabled:opacity-30"
          :disabled="lightbox.scale <= 0.25"
          title="Zoom out (−)"
          @click="zoomOut"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </button>
        <button
          class="w-14 rounded border border-white/20 py-0.5 text-center text-xs text-white/70 hover:bg-white/10 hover:text-white tabular-nums"
          title="Reset zoom (0)"
          @click="resetZoom"
        >{{ Math.round(lightbox.scale * 100) }}%</button>
        <button
          class="flex h-7 w-7 items-center justify-center rounded-full border border-white/20 text-white/70 hover:bg-white/10 hover:text-white disabled:opacity-30"
          :disabled="lightbox.scale >= 4"
          title="Zoom in (+)"
          @click="zoomIn"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        </button>
        <span class="ml-2 text-[10px] text-white/30">scroll to zoom · drag to pan · dbl-click to toggle</span>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from "vue";
import { call, toast } from "frappe-ui";

const REACTION_EMOJIS = ["👍", "❤️", "😂", "😮", "😢", "🙏"];

const props = withDefaults(defineProps<{
  message: Record<string, any>;
  reactions?: Array<{ emoji: string; type: string; sender: string }>;
  replyToMessage?: Record<string, any> | null;
  isGroup?: boolean;
  mentionMap?: Record<string, string>;
  allowRetry?: boolean;
  allowEdit?: boolean;
  allowDelete?: boolean;
}>(), { isGroup: false, mentionMap: () => ({}), allowRetry: false, allowEdit: false, allowDelete: false });

const emit = defineEmits<{
  (e: "reply", message: Record<string, any>): void;
  (e: "react", emoji: string, targetMessageId: string): void;
  (e: "scrollToReply", messageId: string): void;
  (e: "edit", messageName: string, newText: string): void;
  (e: "retry", messageName: string): void;
  (e: "delete", messageName: string): void;
}>();

// A message the sender removed for everyone. Its content is gone: no text, media,
// reactions or actions survive it.
const isDeleted = computed(() => !!props.message.is_deleted);

const isOutgoing = computed(() => props.message.type === "Outgoing");

// Normalise the delivery status across both integrations (WA Line uses capitalised
// Pending/Sent/Delivered/Read/Failed; frappe_whatsapp uses lowercase + "Success").
// Returns one of: "pending" | "sent" | "delivered" | "read" | "failed" | "".
const statusKind = computed(() => {
  const s = String(props.message.status || "").toLowerCase();
  if (s === "read" || s === "played") return "read";
  if (s === "delivered") return "delivered";
  if (s === "sent" || s === "success" || s === "server_ack") return "sent";
  if (s === "failed" || s === "error") return "failed";
  if (s === "pending" || s === "queued") return "pending";
  return "";
});

const SENDER_COLORS = ["#e53935","#8e24aa","#1e88e5","#00897b","#f4511e","#6d4c41","#546e7a","#d81b60"];
const senderColor = computed(() => {
  const name = props.message.profile_name || props.message.sender_jid || "";
  let hash = 0;
  for (const ch of name) hash = ((hash * 31) + ch.charCodeAt(0)) & 0x7fffffff;
  return SENDER_COLORS[hash % SENDER_COLORS.length];
});

const showEmojiPicker = ref(false);
const editing = ref(false);
const editText = ref("");
const editTextareaRef = ref<HTMLTextAreaElement | null>(null);
const showEditHistory = ref(false);
const emojiPickerRef = ref<HTMLElement | null>(null);

function onDocumentClick(e: MouseEvent) {
  if (emojiPickerRef.value && !emojiPickerRef.value.contains(e.target as Node)) {
    showEmojiPicker.value = false;
  }
}

watch(showEmojiPicker, (val) => {
  if (val) {
    // Defer so the click that opened the picker doesn't immediately close it
    setTimeout(() => document.addEventListener("click", onDocumentClick), 0);
  } else {
    document.removeEventListener("click", onDocumentClick);
  }
});

onBeforeUnmount(() => {
  document.removeEventListener("click", onDocumentClick);
  document.removeEventListener("fullscreenchange", onFullscreenChange);
});

function toggleEmojiPicker() {
  showEmojiPicker.value = !showEmojiPicker.value;
}

// WhatsApp sends an empty reaction to remove one, and picking the emoji you
// already reacted with is a removal rather than a no-op.
function pickEmoji(emoji: string) {
  showEmojiPicker.value = false;
  const alreadyMine = aggregatedReactions.value.some(
    (r) => r.hasOwn && r.emoji === emoji
  );
  emit("react", alreadyMine ? "" : emoji, props.message.message_id || "");
}

// Clicking a badge you're part of removes your reaction; clicking anyone else's
// adds yours with that emoji (replacing whatever you had).
function toggleReaction(r: { emoji: string; hasOwn: boolean }) {
  emit("react", r.hasOwn ? "" : r.emoji, props.message.message_id || "");
}


// Aggregate reactions: {emoji → {emoji, count, hasOwn, senders[]}}
const aggregatedReactions = computed(() => {
  const map: Record<string, { emoji: string; count: number; hasOwn: boolean; senders: string[] }> = {};
  for (const r of props.reactions ?? []) {
    if (!r.emoji) continue;
    if (!map[r.emoji]) map[r.emoji] = { emoji: r.emoji, count: 0, hasOwn: false, senders: [] };
    map[r.emoji].count++;
    if (r.type === "Outgoing") map[r.emoji].hasOwn = true;
    if (r.sender) map[r.emoji].senders.push(r.sender);
  }
  return Object.values(map);
});

// Reply context helpers
const replyToSenderName = computed(() => {
  const m = props.replyToMessage;
  // No resolved target yet (still fetching, or genuinely not stored).
  if (!m) return "";
  return m.type === "Outgoing"
    ? (m.sender_full_name || "You")
    : (m.profile_name || "Customer");
});

const replyPreview = computed(() => {
  const m = props.replyToMessage;
  if (!m) return "Replied to an earlier message";
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

const editHistoryList = computed(() => {
  const history = props.message.edit_history || [];
  return [...history].sort((a: any, b: any) =>
    new Date(b.edited_at).getTime() - new Date(a.edited_at).getTime()
  );
});

function formatEditTime(ts: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const tooltip = reactive({ visible: false, text: "", x: 0, y: 0 });

function showTooltip(e: MouseEvent, senders: string[]) {
  if (!senders.length) return;
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
  tooltip.text = senders.join(", ");
  tooltip.x = rect.left + rect.width / 2;
  tooltip.y = rect.top - 8;
  tooltip.visible = true;
}

function hideTooltip() {
  tooltip.visible = false;
}

const copied = ref(false);
let copyTimer: ReturnType<typeof setTimeout> | null = null;

function copyText() {
  navigator.clipboard.writeText(props.message.message || "").then(() => {
    copied.value = true;
    if (copyTimer) clearTimeout(copyTimer);
    copyTimer = setTimeout(() => { copied.value = false; }, 1500);
  });
}

function startEdit() {
  editText.value = props.message.message || "";
  editing.value = true;
  nextTick(() => editTextareaRef.value?.focus());
}

function saveEdit() {
  if (!editText.value.trim()) return;
  emit("edit", props.message.name, editText.value.trim());
  editing.value = false;
  editText.value = "";
}

function cancelEdit() {
  editing.value = false;
  editText.value = "";
}

const phoneCopied = ref(false);
let phoneCopyTimer: ReturnType<typeof setTimeout> | null = null;

function copySenderPhone() {
  const phone = props.message.sender_phone;
  if (!phone) return;
  navigator.clipboard.writeText(`+${phone}`).then(() => {
    phoneCopied.value = true;
    toast.success(`+${phone} copied`);
    if (phoneCopyTimer) clearTimeout(phoneCopyTimer);
    phoneCopyTimer = setTimeout(() => { phoneCopied.value = false; }, 1500);
  });
}

const formattedMessage = computed(() => {
  let text = props.message.message || "";
  // Strip script tags
  text = text.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "");
  // Escape HTML first
  text = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  // Resolve @mention digits → @Name using the participant map
  if (props.mentionMap && Object.keys(props.mentionMap).length) {
    text = text.replace(/@(\d{7,20})/g, (_match, digits) => {
      const name = props.mentionMap![digits];
      return name
        ? `<span class="font-medium text-blue-600 dark:text-blue-400">@${name}</span>`
        : `@${digits}`;
    });
  }
  // WhatsApp formatting: *bold*, _italic_, ~strikethrough~, ```monospace```
  text = text.replace(/```([\s\S]*?)```/g, "<code class=\"rounded bg-surface-gray-2 px-1 font-mono text-[0.85em]\">$1</code>");
  text = text.replace(/\*([^*\n]+)\*/g, "<strong>$1</strong>");
  text = text.replace(/_([^_\n]+)_/g, "<em>$1</em>");
  text = text.replace(/~([^~\n]+)~/g, "<del>$1</del>");
  // Clickable URLs
  text = text.replace(
    /(https?:\/\/[^\s<>"]+)/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer" class="underline text-blue-600 dark:text-blue-400 break-all">$1</a>',
  );
  return text;
});

const attachFilename = computed(() => {
  const url = props.message.attach;
  if (!url) return null;
  return url.split("/").pop() || null;
});

// Unified reactive media src — used for image, video, audio
const mediaSrc = ref<string>(props.message.attach || "");
watch(() => props.message.attach, (v) => {
  const newVal = v || "";
  if (newVal !== mediaSrc.value) {
    mediaSrc.value = newVal;
    mediaBroken.value = false;  // reset if media_url was updated externally
  }
});

// Downscaled preview generated on ingest. Absent for media that predates the
// thumbnail backfill, and for anything whose thumb failed to generate — both are
// normal, so every consumer falls back to the full-size original.
const thumbnailUrl = computed<string>(() => props.message.thumbnail_url || "");
const thumbFailed = ref(false);
const thumbSrc = computed<string>(() =>
  !thumbFailed.value && thumbnailUrl.value ? thumbnailUrl.value : mediaSrc.value
);

// A broken thumbnail means the preview is missing, not that the original is gone,
// so retry with the original before falling through to the media-refetch path.
function handleImageError() {
  if (!thumbFailed.value && thumbnailUrl.value) {
    thumbFailed.value = true;
    return;
  }
  handleMediaError();
}

const mediaBroken = ref(false);
const mediaRefetching = ref(false);

async function handleMediaError() {
  if (mediaRefetching.value || mediaBroken.value) return;
  mediaRefetching.value = true;
  try {
    const newUrl = await call("helpdesk.integrations.wa.refetch_media_for_message", {
      message_name: props.message.name,
    }) as string;
    if (newUrl && newUrl !== mediaSrc.value) {
      mediaSrc.value = newUrl;
      mediaBroken.value = false;
    } else {
      mediaBroken.value = true;
    }
  } catch {
    mediaBroken.value = true;
  } finally {
    mediaRefetching.value = false;
  }
}

// Lightbox
const lightboxRef = ref<HTMLElement | null>(null);
const isFullscreen = ref(false);
const lightbox = reactive({
  open: false, src: "", type: "image" as "image" | "video",
  scale: 1, offsetX: 0, offsetY: 0,
  dragging: false, dragStartX: 0, dragStartY: 0,
});

function openLightbox(src: string, type: "image" | "video") {
  lightbox.src = src;
  lightbox.type = type;
  lightbox.scale = 1;
  lightbox.offsetX = 0;
  lightbox.offsetY = 0;
  lightbox.open = true;
  nextTick(() => lightboxRef.value?.focus());
}

function closeLightbox() {
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  lightbox.open = false;
  lightbox.src = "";
  isFullscreen.value = false;
}

function zoomIn() {
  lightbox.scale = Math.min(4, parseFloat((lightbox.scale + 0.25).toFixed(2)));
}

function zoomOut() {
  lightbox.scale = Math.max(0.25, parseFloat((lightbox.scale - 0.25).toFixed(2)));
  if (lightbox.scale <= 1) { lightbox.offsetX = 0; lightbox.offsetY = 0; }
}

function resetZoom() {
  lightbox.scale = 1;
  lightbox.offsetX = 0;
  lightbox.offsetY = 0;
}

function onLightboxWheel(e: WheelEvent) {
  const delta = e.deltaY < 0 ? 0.15 : -0.15;
  lightbox.scale = Math.max(0.25, Math.min(4, parseFloat((lightbox.scale + delta).toFixed(2))));
  if (lightbox.scale <= 1) { lightbox.offsetX = 0; lightbox.offsetY = 0; }
}

function onLightboxDblClick() {
  if (lightbox.scale !== 1) {
    resetZoom();
  } else {
    lightbox.scale = 2;
  }
}

function onDragStart(e: MouseEvent) {
  if (lightbox.scale <= 1) return;
  lightbox.dragging = true;
  lightbox.dragStartX = e.clientX - lightbox.offsetX;
  lightbox.dragStartY = e.clientY - lightbox.offsetY;
}

function onDragMove(e: MouseEvent) {
  if (!lightbox.dragging) return;
  lightbox.offsetX = e.clientX - lightbox.dragStartX;
  lightbox.offsetY = e.clientY - lightbox.dragStartY;
}

function onDragEnd() {
  lightbox.dragging = false;
}

async function toggleFullscreen() {
  if (!lightboxRef.value) return;
  if (document.fullscreenElement) {
    await document.exitFullscreen().catch(() => {});
    isFullscreen.value = false;
  } else {
    await lightboxRef.value.requestFullscreen().catch(() => {});
    isFullscreen.value = true;
  }
}

function onFullscreenChange() {
  isFullscreen.value = !!document.fullscreenElement;
}

document.addEventListener("fullscreenchange", onFullscreenChange);
</script>
