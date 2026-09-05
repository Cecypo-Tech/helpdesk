<template>
  <Tabs
    :modelValue="tabIndex"
    :tabs="tabs"
    @update:modelValue="changeTabTo"
    class="[&_[role='tab']]:px-0 [&_[role='tablist']]:px-5 [&_[role='tablist']]:gap-7.5 [&_[role='tablist']]:flex-shrink-0"
  >
    <template #tab-item="{ tab }">
      <button
        class="flex items-center gap-1.5 text-base text-ink-gray-5 duration-300 ease-in-out hover:text-ink-gray-9 data-[state=active]:text-ink-gray-9 py-2.5"
      >
        <component v-if="tab.icon" :is="tab.icon" class="size-4" />
        {{ tab.label }}
        <span
          v-if="(tab.name === 'whatsapp' || tab.name === 'baileys') && waUnreadCount > 0"
          class="flex min-w-[16px] h-4 items-center justify-center rounded-full bg-green-500 px-1 text-[10px] font-bold leading-none text-white"
        >{{ waUnreadCount > 99 ? "99+" : waUnreadCount }}</span>
      </button>
    </template>
    <template #tab-panel="{ tab }">
      <WhatsAppChatTab
        v-if="tab.name === 'whatsapp'"
        ref="whatsappTabRef"
        :ticketId="String(ticket.doc?.name)"
      />
      <BaileysGroupChatTab
        v-else-if="tab.name === 'baileys'"
        ref="baileysTabRef"
        :ticketId="String(ticket.doc?.name)"
      />
      <template v-else>
        <TicketAgentActivities
          v-if="Boolean(activities.data)"
          ref="ticketAgentActivitiesRef"
          :activities="filterActivities(tab.name as TicketTab)"
          :title="tab.label"
          :ticket-status="ticket.doc.status"
          @email:reply="
            (e) => {
              communicationAreaRef.replyToEmail(e);
            }
          "
          @update="
            () => {
              activities.reload();
              ticketAgentActivitiesRef.scrollToLatestActivity();
            }
          "
        />
        <div v-else class="flex items-center justify-center flex-col mt-20">
          <LoadingIndicator :scale="8" class="text-ink-gray-5" />
          <p class="text-xl font-medium text-ink-gray-5 absolute top-[50%]">
            Loading...
          </p>
        </div>
      </template>
    </template>
  </Tabs>
  <!-- Comm Area -->
  <CommunicationArea
    v-if="activeTabName !== 'whatsapp' && activeTabName !== 'baileys'"
    ref="communicationAreaRef"
    :ticketId="String(ticket.doc?.name)"
    :to-emails="[ticket.doc?.raised_by]"
    :cc-emails="[]"
    :bcc-emails="[]"
    :key="ticket.doc?.name"
    @update="
      () => {
        activities.reload();
        ticketAgentActivitiesRef.scrollToLatestActivity();
      }
    "
  />
</template>

<script setup lang="ts">
import {
  ActivityIcon,
  CommentIcon,
  EmailIcon,
  PhoneIcon,
  WhatsAppIcon,
} from "@/components/icons";
import WhatsAppChatTab from "@/components/whatsapp/WhatsAppChatTab.vue";
import BaileysGroupChatTab from "@/components/whatsapp/BaileysGroupChatTab.vue";
import { useActiveTabManager } from "@/composables/useActiveTabManager";
import { useTelephonyStore } from "@/stores/telephony";
import { globalStore } from "@/stores/globalStore";
import {
  ActivitiesSymbol,
  FeedbackActivity,
  TabObject,
  TicketSymbol,
  TicketTab,
} from "@/types";
import { createResource, LoadingIndicator, Tabs } from "frappe-ui";
import { storeToRefs } from "pinia";
import { computed, ComputedRef, defineAsyncComponent, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import TicketAgentActivities from "../ticket/TicketAgentActivities.vue";

const CommunicationArea = defineAsyncComponent(
  () => import("@/components/CommunicationArea.vue")
);

const route = useRoute();
const ticket = inject(TicketSymbol);
const activities = inject(ActivitiesSymbol);

const ticketAgentActivitiesRef = ref(null);
const communicationAreaRef = ref(null);
const whatsappTabRef = ref(null);
const baileysTabRef = ref(null);
const telephonyStore = useTelephonyStore();
const { isCallingEnabled } = storeToRefs(telephonyStore);

const hasBaileys = computed(() => Boolean(ticket.value?.doc?.baileys_jid));
const hasWhatsApp = computed(() => !hasBaileys.value);

// Fetch WABA ticket info to know if this is a WhatsApp-originated ticket.
// Never pass computed refs or functions to `params`/`auto` — frappe-ui serialises
// the options object and Vue reactive objects cause a circular-JSON error.
const wabaTicketInfo = createResource({
  url: "helpdesk.integrations.wa.get_whatsapp_ticket_info",
});

watch(
  () => ticket.value?.doc?.name,
  (name) => {
    if (name && !hasBaileys.value) {
      wabaTicketInfo.fetch({ ticket: String(name) });
    }
  },
  { immediate: true }
);

// Unread-count badge on the WhatsApp tab label (WABA or WA Line, whichever applies).
const waUnreadCount = ref(0);
const waUnreadResource = createResource({
  url: "helpdesk.integrations.wa.get_ticket_wa_unread_count",
  onSuccess: (count: number) => {
    waUnreadCount.value = count || 0;
  },
});

function reloadWaUnreadCount() {
  const name = ticket.value?.doc?.name;
  if (name) waUnreadResource.fetch({ ticket: String(name) });
}

watch(() => ticket.value?.doc?.name, reloadWaUnreadCount, { immediate: true });

function onWaMessageForBadge(data?: { jid?: string; ticket?: string; origin?: string }) {
  // Only refetch when the WhatsApp tab isn't the one currently open —
  // if it's open, the chat component already marks messages read.
  if (activeTabName.value === "whatsapp" || activeTabName.value === "baileys") return;

  // Both events are broadcast to every client for every WhatsApp message, so
  // without this check each agent with any ticket open refetched this badge on
  // all WhatsApp traffic in the system. Only this ticket's own conversation can
  // change its count: WA Line messages carry the JID, WABA messages the ticket.
  const doc = ticket.value?.doc;
  if (!doc) return;
  if (doc.baileys_jid) {
    if (data?.jid && data.jid !== doc.baileys_jid) return;
  } else {
    // An incoming WABA row is announced before it has a ticket ("insert");
    // the count can only change once it is linked ("ingest"), which carries it.
    if (data?.origin === "insert" && !data?.ticket) return;
    if (data?.ticket && String(data.ticket) !== String(doc.name)) return;
  }
  reloadWaUnreadCount();
}

onMounted(() => {
  const { $socket } = globalStore();
  $socket.on("helpdesk:whatsapp-message", onWaMessageForBadge);
  $socket.on("helpdesk:baileys-message", onWaMessageForBadge);
});

onBeforeUnmount(() => {
  const { $socket } = globalStore();
  $socket.off("helpdesk:whatsapp-message", onWaMessageForBadge);
  $socket.off("helpdesk:baileys-message", onWaMessageForBadge);
});

const tabs: ComputedRef<TabObject[]> = computed(() => {
  const _tabs: TabObject[] = [
    {
      name: "activity",
      label: "Activity",
      icon: ActivityIcon,
    },
    {
      name: "email",
      label: "Emails",
      icon: EmailIcon,
    },
    {
      name: "comment",
      label: "Comments",
      icon: CommentIcon,
    },
  ];

  if (isCallingEnabled.value) {
    _tabs.push({
      name: "call",
      label: "Calls",
      icon: PhoneIcon,
    });
  }

  if (hasWhatsApp.value) {
    _tabs.push({
      name: "whatsapp",
      label: "WhatsApp",
      icon: WhatsAppIcon,
    });
  }

  if (hasBaileys.value) {
    _tabs.push({
      name: "baileys",
      label: "WhatsApp",
      icon: WhatsAppIcon,
    });
  }

  return _tabs;
});

// For baileys tickets: default to the baileys tab immediately (no async needed)
const { tabIndex, changeTabTo } = useActiveTabManager(tabs, () =>
  hasBaileys.value ? "baileys" : null
);

// For WABA tickets: auto-select the whatsapp tab once ticketInfo resolves
watch(
  () => wabaTicketInfo.data,
  (data) => {
    if (!data?.has_whatsapp || !data?.via_frappe_whatsapp) return;
    if (route.hash) return;
    const idx = tabs.value.findIndex((t) => t.name === "whatsapp");
    if (idx !== -1) changeTabTo(idx);
  }
);

// Auto-scroll to bottom when switching to a chat tab
watch(tabIndex, (idx) => {
  const tabName = tabs.value[idx]?.name;
  if (tabName === "whatsapp") {
    nextTick(() => (whatsappTabRef.value as any)?.scrollToBottom?.());
    waUnreadCount.value = 0;
  } else if (tabName === "baileys") {
    nextTick(() => (baileysTabRef.value as any)?.scrollToBottom?.());
    waUnreadCount.value = 0;
  }
});

const activeTabName = computed(() => {
  const currentTabs = tabs.value;
  return currentTabs[tabIndex.value]?.name || "activity";
});

// TODO: refactor for pagination
// can be done once we sort out the backend
const _activities = computed(() => {
  if (!activities.value?.data) {
    return [];
  }

  const emailProps = activities.value?.data?.communications.map(
    (email, idx: number) => {
      return {
        subject: email.subject,
        content: email.content,
        sender: { name: email.user.email, full_name: email.user.name },
        to: email.recipients,
        type: "email",
        key: email.creation,
        cc: email.cc,
        bcc: email.bcc,
        creation: email.communication_date || email.creation,
        attachments: email.attachments,
        name: email.name,
        deliveryStatus: email.delivery_status,
        isFirstEmail: idx === 0,
      };
    }
  );

  const commentProps = activities.value.data.comments.map((comment) => {
    return {
      name: comment.name,
      type: "comment",
      key: comment.creation,
      commentedBy: comment.commented_by,
      commenter: comment.user.name,
      creation: comment.creation,
      content: comment.content,
      attachments: comment.attachments,
    };
  });

  const historyProps = [
    ...activities.value.data.history,
    ...activities.value.data.views,
  ].map((h) => {
    return {
      type: "history",
      key: h.creation,
      content: h.action ? h.action : "viewed this",
      creation: h.creation,
      user: h.user.name + " ",
    };
  });

  const callProps = activities.value.data.calls.map((call) => {
    return {
      ...call,
      type: "call",
      name: call.name,
      key: call.creation,
      call_type: call.type,
      content: `${call.caller || "Unknown"} made a call to ${
        call.receiver || "Unknown"
      }`,
      duration: call.duration ? call.duration + "s" : "0s",
    };
  });

  const sorted = [
    ...emailProps,
    ...commentProps,
    ...historyProps,
    ...callProps,
  ].sort((a, b) => new Date(a.creation) - new Date(b.creation));
  const data = [];
  let i = 0;

  while (i < sorted.length) {
    const currentActivity = sorted[i];

    if (currentActivity.type === "history") {
      currentActivity.relatedActivities = [currentActivity];
      for (let j = i + 1; j < sorted.length + 1; j++) {
        const nextActivity = sorted[j];

        if (
          nextActivity &&
          nextActivity.user === currentActivity.user &&
          nextActivity.content !== "viewed this" &&
          !nextActivity.content.includes("assigned") &&
          !nextActivity.content.includes("unassigned")
        ) {
          currentActivity.relatedActivities.push(nextActivity);
        } else {
          data.push(currentActivity);
          i = j - 1;
          break;
        }
      }
    } else {
      data.push(currentActivity);
    }
    i++;
  }
  // add feedback data at the last always
  // name is email
  // full_name is name

  if (ticket.value.doc.feedback_rating === 0) {
    return data;
  }
  let feedbackActivity: FeedbackActivity[] = [
    {
      type: "feedback",
      key: "feedback-activity",
      feedback_rating: ticket.value?.doc.feedback_rating,
      feedback_extra: ticket.value?.doc.feedback_extra,
      feedback: ticket.value?.doc.feedback,
      sender: {
        name: ticket.value?.doc.raised_by,
        full_name: ticket.value?.doc.contact,
      },
    },
  ];
  data.push(...feedbackActivity);

  return data;
});

function filterActivities(eventType: TicketTab) {
  if (eventType === "activity") {
    return _activities.value;
  }
  return _activities.value.filter((activity) => activity.type === eventType);
}
</script>

<style scoped></style>
