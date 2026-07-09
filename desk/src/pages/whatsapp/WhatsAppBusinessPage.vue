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
      :activeTicketId="activeTicketId"
      :activeTicketLoading="activeTicketResource.loading"
      :showBack="isMobile && mobileShowChat"
      class="flex-1 min-w-0"
      @back="mobileShowChat = false"
    />

    <!-- Ticket sidepanel (Details / Contact / Tasks) — desktop only -->
    <TicketSidebar v-if="!isMobile && activeTicketId" :key="activeTicketId" />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide, ref } from "vue";
import { createResource } from "frappe-ui";
import WhatsAppConversationList from "@/components/whatsapp/WhatsAppConversationList.vue";
import WhatsAppBusinessChat from "@/components/whatsapp/WhatsAppBusinessChat.vue";
import TicketSidebar from "@/components/ticket-agent/TicketSidebar.vue";
import { useTicket } from "@/composables/useTicket";
import { globalStore } from "@/stores/globalStore";
import {
  ActivitiesSymbol,
  AssigneeSymbol,
  Customizations,
  CustomizationSymbol,
  RecentSimilarTicketsSymbol,
  Resource,
  TicketContactSymbol,
  TicketSymbol,
} from "@/types";

defineOptions({ inheritAttrs: false });

const STORAGE_KEY = "whatsapp_business_panel_width";
const MIN_WIDTH = 160;
const MAX_WIDTH = 420;
const DEFAULT_WIDTH = 240;

const { $socket } = globalStore();

const containerRef = ref<HTMLElement | null>(null);
const panelWidth = ref(Number(localStorage.getItem(STORAGE_KEY)) || DEFAULT_WIDTH);
const selectedPhone = ref<string | null>(null);
const selectedDisplayName = ref<string>("");
const convListRef = ref<InstanceType<typeof WhatsAppConversationList> | null>(null);
const chatRef = ref<InstanceType<typeof WhatsAppBusinessChat> | null>(null);
const mobileShowChat = ref(false);

// ── Ticket context (Details/Contact/Tasks sidebar + status dropdown) ─────────
const activeTicketResource = createResource({
  url: "helpdesk.integrations.wa.get_active_whatsapp_ticket_for_phone",
  auto: false,
});
const activeTicketId = computed<string | null>(() => activeTicketResource.data?.ticket || null);

function reloadActiveTicket() {
  if (!selectedPhone.value) return;
  activeTicketResource.update({ params: { phone: selectedPhone.value } });
  activeTicketResource.reload();
}

const customizations: Resource<Customizations> = createResource({
  url: "helpdesk.helpdesk.doctype.hd_ticket.api.get_ticket_customizations",
  cache: ["HD Ticket", "customizations"],
  auto: true,
});

const ticketComposable = computed(() =>
  activeTicketId.value ? useTicket(activeTicketId.value) : null
);

provide(TicketSymbol, computed(() => ticketComposable.value?.ticket));
provide(AssigneeSymbol, computed(() => ticketComposable.value?.assignees));
provide(TicketContactSymbol, computed(() => ticketComposable.value?.contact));
provide(RecentSimilarTicketsSymbol, computed(() => ticketComposable.value?.recentSimilarTickets));
provide(ActivitiesSymbol, computed(() => ticketComposable.value?.activities));
provide(CustomizationSymbol, computed(() => customizations));

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

function handleWhatsAppMessage() {
  // A new/updated ticket for the selected phone may have flipped which
  // ticket is "active" (e.g. the previous one just resolved).
  reloadActiveTicket();
}

onMounted(() => {
  document.addEventListener("mouseup", onDocumentMouseUp);
  window.addEventListener("resize", onWindowResize);
  $socket.on("helpdesk:whatsapp-message", handleWhatsAppMessage);
});

onBeforeUnmount(() => {
  document.removeEventListener("mouseup", onDocumentMouseUp);
  window.removeEventListener("resize", onWindowResize);
  $socket.off("helpdesk:whatsapp-message", handleWhatsAppMessage);
});

function onSelect(phone: string, displayName: string) {
  selectedPhone.value = phone;
  selectedDisplayName.value = displayName;
  mobileShowChat.value = true;
  reloadActiveTicket();
}
</script>
