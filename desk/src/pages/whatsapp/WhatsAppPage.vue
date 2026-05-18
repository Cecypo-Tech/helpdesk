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
      class="relative h-full shrink-0"
      :style="{ width: panelWidth + 'px' }"
    >
      <BaileysConversationList
        ref="convListRef"
        :selectedJid="selectedJid"
        @select="onSelect"
      />
      <!-- Resize handle -->
      <div
        class="absolute right-0 top-0 z-10 h-full w-1 cursor-col-resize bg-transparent transition-colors hover:bg-blue-400/50 active:bg-blue-500/70"
        @mousedown.prevent="startResize"
      />
    </div>

    <BaileysChat
      :jid="selectedJid"
      :displayName="selectedDisplayName"
      :company="selectedCompany"
      :assignedTeam="selectedTeam"
      @contactSaved="onContactSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import BaileysConversationList from "@/components/whatsapp/BaileysConversationList.vue";
import BaileysChat from "@/components/whatsapp/BaileysChat.vue";

defineOptions({ inheritAttrs: false });

const STORAGE_KEY = "baileys_panel_width";
const MIN_WIDTH = 160;
const MAX_WIDTH = 420;
const DEFAULT_WIDTH = 240;

const containerRef = ref<HTMLElement | null>(null);
const panelWidth = ref(Number(localStorage.getItem(STORAGE_KEY)) || DEFAULT_WIDTH);
const selectedJid = ref<string | null>(null);
const selectedDisplayName = ref<string>("");
const selectedCompany = ref<string>("");
const selectedTeam = ref<string>("");
const convListRef = ref<InstanceType<typeof BaileysConversationList> | null>(null);

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

// Stop resize if mouse released outside the container
function onDocumentMouseUp() {
  onMouseUp();
}

onMounted(() => {
  document.addEventListener("mouseup", onDocumentMouseUp);
});

onBeforeUnmount(() => {
  document.removeEventListener("mouseup", onDocumentMouseUp);
});

function onSelect(jid: string, displayName: string, company: string, team: string) {
  selectedJid.value = jid;
  selectedDisplayName.value = displayName;
  selectedCompany.value = company;
  selectedTeam.value = team;
}

function onContactSaved(data: { custom_name: string; company: string; assigned_team: string }) {
  if (data.custom_name) selectedDisplayName.value = data.custom_name;
  selectedCompany.value = data.company;
  selectedTeam.value = data.assigned_team || "";
  convListRef.value?.reload();
}
</script>
