<template>
  <div
    ref="containerRef"
    class="flex flex-1 min-h-0 overflow-hidden"
    @mousemove="onMouseMove"
    @mouseup="onMouseUp"
    @mouseleave="onMouseUp"
  >
    <!-- Conversation list panel -->
    <div
      v-show="!isMobile || !mobileShowChat"
      class="relative h-full shrink-0"
      :style="isMobile ? { width: '100%' } : { width: panelWidth + 'px' }"
    >
      <WhatsAppConversationList
        ref="convListRef"
        :selectedPhone="selectedPhone"
        @select="onSelect"
      />
      <!-- Resize handle (desktop only) -->
      <div
        v-if="!isMobile"
        class="absolute right-0 top-0 z-10 h-full w-1 cursor-col-resize bg-transparent transition-colors hover:bg-blue-400/50 active:bg-blue-500/70"
        @mousedown.prevent="startResize"
      />
    </div>

    <!-- Chat panel -->
    <WhatsAppBusinessChat
      v-show="!isMobile || mobileShowChat"
      ref="chatRef"
      :phone="selectedPhone"
      :displayName="selectedDisplayName"
      :showBack="isMobile && mobileShowChat"
      class="flex-1 min-w-0"
      @back="mobileShowChat = false"
    />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import WhatsAppConversationList from "@/components/whatsapp/WhatsAppConversationList.vue";
import WhatsAppBusinessChat from "@/components/whatsapp/WhatsAppBusinessChat.vue";

defineOptions({ inheritAttrs: false });

const STORAGE_KEY = "whatsapp_business_panel_width";
const MIN_WIDTH = 160;
const MAX_WIDTH = 420;
const DEFAULT_WIDTH = 240;

const containerRef = ref<HTMLElement | null>(null);
const panelWidth = ref(Number(localStorage.getItem(STORAGE_KEY)) || DEFAULT_WIDTH);
const selectedPhone = ref<string | null>(null);
const selectedDisplayName = ref<string>("");
const convListRef = ref<InstanceType<typeof WhatsAppConversationList> | null>(null);
const chatRef = ref<InstanceType<typeof WhatsAppBusinessChat> | null>(null);
const mobileShowChat = ref(false);

// ── Mobile detection ──────────────────────────────────────────────────────────
const isMobile = ref(window.innerWidth < 768);

function onWindowResize() {
  isMobile.value = window.innerWidth < 768;
}

// ── Resize handle (desktop) ───────────────────────────────────────────────────
let resizing = false;

function startResize() {
  resizing = true;
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";
}

function onMouseMove(e: MouseEvent) {
  if (!resizing || !containerRef.value) return;
  const rect = containerRef.value.getBoundingClientRect();
  const newWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, e.clientX - rect.left));
  panelWidth.value = newWidth;
}

function onMouseUp() {
  if (!resizing) return;
  resizing = false;
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
  localStorage.setItem(STORAGE_KEY, String(panelWidth.value));
}

function onDocumentMouseUp() {
  onMouseUp();
}

onMounted(() => {
  document.addEventListener("mouseup", onDocumentMouseUp);
  window.addEventListener("resize", onWindowResize);
});

onBeforeUnmount(() => {
  document.removeEventListener("mouseup", onDocumentMouseUp);
  window.removeEventListener("resize", onWindowResize);
});

function onSelect(phone: string, displayName: string) {
  selectedPhone.value = phone;
  selectedDisplayName.value = displayName;
  mobileShowChat.value = true;
}
</script>
