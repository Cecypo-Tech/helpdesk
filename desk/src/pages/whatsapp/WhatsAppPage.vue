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
      <BaileysConversationList
        ref="convListRef"
        :line="lineName"
        :selectedJid="selectedJid"
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
    <div
      v-show="!isMobile || mobileShowChat"
      class="flex flex-1 min-h-0 min-w-0 overflow-hidden"
    >
      <BaileysChat
        ref="baileysChat"
        :jid="selectedJid"
        :displayName="selectedDisplayName"
        :company="selectedCompany"
        :assignedTeam="selectedTeam"
        :phone="selectedPhone"
        :line="lineName"
        :showBack="isMobile && mobileShowChat"
        :tasksOpen="showTasksPanel"
        :tasksCount="tasksCount"
        :ticketsCount="ticketsCount"
        class="flex-1 min-w-0"
        @contactSaved="onContactSaved"
        @back="mobileShowChat = false"
        @toggleTasks="showTasksPanel = !showTasksPanel"
      />

      <!-- Tasks panel -->
      <WaChatTasksPanel
        v-if="showTasksPanel && selectedJid"
        :jid="selectedJid"
        :line="lineName"
        @close="showTasksPanel = false"
        @tasksChanged="reloadTasksCount"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import BaileysConversationList from "@/components/whatsapp/BaileysConversationList.vue";
import BaileysChat from "@/components/whatsapp/BaileysChat.vue";
import WaChatTasksPanel from "@/components/whatsapp/WaChatTasksPanel.vue";
import { call, createResource } from "frappe-ui";
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
const showTasksPanel = ref(false);
const mobileShowChat = ref(false);

// ── Tasks count (badge on the tasks button) ───────────────────────────────────
const tasksCountResource = createResource({
  url: "helpdesk.integrations.evolution.get_tasks_for_jid",
  auto: false,
});
const tasksCount = computed<number>(() =>
  (tasksCountResource.data as any[] | null)?.filter((t: any) => t.status !== "Done").length ?? 0
);

const ticketsCountResource = createResource({
  url: "helpdesk.integrations.evolution.get_tickets_for_jid",
  auto: false,
});
const ticketsCount = computed<number>(() =>
  (ticketsCountResource.data as any[] | null)?.length ?? 0
);

watch(selectedJid, (jid) => {
  if (jid) {
    tasksCountResource.update({ params: { jid } });
    tasksCountResource.reload();
    ticketsCountResource.update({ params: { jid } });
    ticketsCountResource.reload();
  } else {
    tasksCountResource.reset?.();
    ticketsCountResource.reset?.();
  }
});

function reloadTasksCount() {
  if (selectedJid.value) {
    tasksCountResource.update({ params: { jid: selectedJid.value } });
    tasksCountResource.reload();
    ticketsCountResource.update({ params: { jid: selectedJid.value } });
    ticketsCountResource.reload();
  }
}

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

// ── Socket handlers ──────────────────────────────────────────────────────────
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

onMounted(async () => {
  document.addEventListener("mouseup", onDocumentMouseUp);
  window.addEventListener("resize", onWindowResize);
  $socket.on("helpdesk:baileys-message", handleBaileysMessage);
  $socket.on("helpdesk:baileys-status-update", handleBaileysStatusUpdate);

  const qJid = String(route.query.jid || "");
  if (qJid) {
    try {
      const info = await call("helpdesk.integrations.evolution.get_contact_info_for_jid", { jid: qJid });
      onSelect(qJid, info.display_name || qJid, info.company || "", info.assigned_team || "", info.phone || "");
    } catch {
      onSelect(qJid, qJid, "", "", "");
    }
  }
});

onBeforeUnmount(() => {
  document.removeEventListener("mouseup", onDocumentMouseUp);
  window.removeEventListener("resize", onWindowResize);
  $socket.off("helpdesk:baileys-message", handleBaileysMessage);
  $socket.off("helpdesk:baileys-status-update", handleBaileysStatusUpdate);
});

function onSelect(jid: string, displayName: string, company: string, team: string, phone: string) {
  selectedJid.value = jid;
  selectedDisplayName.value = displayName;
  selectedCompany.value = company;
  selectedTeam.value = team;
  selectedPhone.value = phone || "";
  mobileShowChat.value = true;
}

function onContactSaved(data: { custom_name: string; company: string; assigned_team: string; phone: string }) {
  if (data.custom_name) selectedDisplayName.value = data.custom_name;
  selectedCompany.value = data.company;
  selectedTeam.value = data.assigned_team || "";
  if (data.phone) selectedPhone.value = data.phone;
  convListRef.value?.reload();
}
</script>
