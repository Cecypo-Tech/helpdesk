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
        :line="lineName"
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
      ref="baileysChat"
      :jid="selectedJid"
      :displayName="selectedDisplayName"
      :company="selectedCompany"
      :assignedTeam="selectedTeam"
      :phone="selectedPhone"
      :line="lineName"
      @contactSaved="onContactSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import BaileysConversationList from "@/components/whatsapp/BaileysConversationList.vue";
import BaileysChat from "@/components/whatsapp/BaileysChat.vue";
import { globalStore } from "@/stores/globalStore";

const route = useRoute();
const lineName = computed(() => String(route.params.lineName || ""));

defineOptions({ inheritAttrs: false });

const STORAGE_KEY = "baileys_panel_width";
const MIN_WIDTH = 160;
const MAX_WIDTH = 420;
const DEFAULT_WIDTH = 240;

const { $socket } = globalStore();

const containerRef = ref<HTMLElement | null>(null);
const panelWidth = ref(Number(localStorage.getItem(STORAGE_KEY)) || DEFAULT_WIDTH);
const selectedJid = ref<string | null>(null);
const selectedDisplayName = ref<string>("");
const selectedCompany = ref<string>("");
const selectedTeam = ref<string>("");
const selectedPhone = ref<string>("");
const convListRef = ref<InstanceType<typeof BaileysConversationList> | null>(null);
const baileysChat = ref<InstanceType<typeof BaileysChat> | null>(null);

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

function handleBaileysMessage(data: { jid: string; is_incoming: boolean }) {
  convListRef.value?.reload();
  if (data.jid === selectedJid.value) {
    baileysChat.value?.refresh();
  }
}

function handleBaileysStatusUpdate(data: { message_id: string; status: string; jid: string }) {
  if (data.jid === selectedJid.value) {
    baileysChat.value?.patchMessageStatus(data.message_id, data.status);
  }
}

onMounted(() => {
  document.addEventListener("mouseup", onDocumentMouseUp);
  $socket.on("helpdesk:baileys-message", handleBaileysMessage);
  $socket.on("helpdesk:baileys-status-update", handleBaileysStatusUpdate);
});

onBeforeUnmount(() => {
  document.removeEventListener("mouseup", onDocumentMouseUp);
  $socket.off("helpdesk:baileys-message", handleBaileysMessage);
  $socket.off("helpdesk:baileys-status-update", handleBaileysStatusUpdate);
});

function onSelect(jid: string, displayName: string, company: string, team: string, phone: string) {
  selectedJid.value = jid;
  selectedDisplayName.value = displayName;
  selectedCompany.value = company;
  selectedTeam.value = team;
  selectedPhone.value = phone || "";
}

function onContactSaved(data: { custom_name: string; company: string; assigned_team: string; phone: string }) {
  if (data.custom_name) selectedDisplayName.value = data.custom_name;
  selectedCompany.value = data.company;
  selectedTeam.value = data.assigned_team || "";
  if (data.phone) selectedPhone.value = data.phone;
  convListRef.value?.reload();
}
</script>
