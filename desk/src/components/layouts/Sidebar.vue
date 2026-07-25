<template>
  <div
    class="flex select-none flex-col border-r border-outline-gray-2 bg-surface-gray-1 p-2 text-base duration-300 ease-in-out"
    :style="{
      'min-width': width,
      'max-width': width,
    }"
  >
    <UserMenu class="mb-2" :options="profileSettings" />
    <SidebarLink
      v-if="!isCustomerPortal"
      :label="__('Search')"
      class="my-0.5"
      :icon="LucideSearch"
      :on-click="() => openCommandPalette()"
      :is-expanded="isExpanded"
    >
      <template #right>
        <span class="flex items-center gap-0.5 font-medium text-gray-600">
          <component :is="device.modifierIcon" class="h-3 w-3" />
          <span>K</span>
        </span>
      </template>
    </SidebarLink>
    <SidebarLink
      v-if="!isCustomerPortal"
      class="relative my-0.5 min-h-7"
      :label="__('Dashboard')"
      :icon="LucideLayoutDashboard"
      :to="'Dashboard'"
      :is-active="isActiveTab('Dashboard')"
      :is-expanded="isExpanded"
    />
    <div class="mb-4" v-if="!isCustomerPortal">
      <div
        v-if="notificationStore.unread"
        class="absolute size-1.5 translate-x-6 translate-y-1 rounded-full bg-blue-400 left-1"
        theme="gray"
        variant="solid"
      />
      <SidebarLink
        class="relative my-0.5"
        :label="__('Notifications')"
        :icon="LucideBell"
        :on-click="() => notificationStore.toggle()"
        :is-expanded="isExpanded"
      >
        <template #right>
          <Badge
            v-if="isExpanded && notificationStore.unread"
            :label="
              notificationStore.unread > 9 ? '9+' : notificationStore.unread
            "
            theme="gray"
            variant="subtle"
          />
        </template>
      </SidebarLink>
    </div>
    <!-- WhatsApp lines section (WA API) -->
    <div v-if="!isCustomerPortal && waLines.length" class="mb-1">
      <!-- Collapsed mode: single icon with green dot when any unread -->
      <div v-if="!isExpanded" class="relative my-0.5">
        <SidebarLink
          :label="__('WhatsApp')"
          :icon="WhatsAppIcon"
          :is-expanded="false"
          :is-active="route.path.startsWith('/whatsapp')"
          :to="waLines.length
                ? { name: 'WhatsAppChat', params: { lineName: waLines[0].name } }
                : 'WhatsAppAnalytics'"
        />
        <span
          v-if="waUnread > 0"
          class="absolute left-1 top-1 size-1.5 rounded-full bg-green-500"
        />
      </div>

      <!-- Expanded mode: collapsible section header + per-line links -->
      <template v-else>
        <div
          class="flex cursor-pointer items-center gap-1.5 px-2 mt-3 mb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-gray-5 select-none"
          @click="waExpanded = !waExpanded"
        >
          <FeatherIcon
            name="chevron-right"
            class="h-3 w-3 text-ink-gray-5 transition-transform duration-200"
            :class="{ 'rotate-90': waExpanded }"
          />
          <span class="flex-1">{{ __("WhatsApp") }}</span>
          <Badge
            v-if="waUnread > 0"
            :label="waUnread > 99 ? '99+' : String(waUnread)"
            theme="green"
            variant="subtle"
            class="text-[10px]"
          />
        </div>
        <nav v-if="waExpanded" class="flex flex-col">
          <SidebarLink
            v-for="line in waLines"
            :key="line.name"
            :icon="WhatsAppIcon"
            :label="line.display_label"
            :to="{ name: 'WhatsAppChat', params: { lineName: line.name } }"
            :is-expanded="true"
            :is-active="route.params.lineName === line.name"
            class="my-0.5 pl-5"
          >
            <template #right>
              <Badge
                v-if="line.unread > 0"
                :label="line.unread > 99 ? '99+' : String(line.unread)"
                theme="green"
                variant="subtle"
              />
            </template>
          </SidebarLink>
          <SidebarLink
            :icon="LucideBarChart2"
            :label="__('Analytics')"
            :to="{ name: 'WhatsAppAnalytics' }"
            :is-expanded="true"
            :is-active="route.name === 'WhatsAppAnalytics'"
            class="my-0.5 pl-5"
          />
        </nav>
      </template>
    </div>
    <div class="overflow-y-auto overflow-x-hidden">
      <div v-for="view in allViews" :key="view.label">
        <div
          v-if="!view.hideLabel && !isExpanded && view.views?.length"
          class="mx-2 my-2 h-1 border-b"
        />
        <Section
          :label="view.label"
          :hideLabel="view.hideLabel"
          :opened="view.opened"
        >
          <template #header="{ opened, hide, toggle }">
            <div
              v-if="!hide"
              class="flex cursor-pointer gap-1.5 px-1 text-base font-medium text-ink-gray-5 transition-all duration-300 ease-in-out"
              :class="
                !isExpanded
                  ? 'ml-0 h-0 overflow-hidden opacity-0'
                  : 'mt-4 h-7 w-auto opacity-100'
              "
              @click="toggle()"
            >
              <FeatherIcon
                name="chevron-right"
                class="h-4 text-ink-gray-9 transition-all duration-300 ease-in-out"
                :class="{ 'rotate-90': opened }"
              />
              <span>{{ __(view.label) }}</span>
            </div>
          </template>
          <nav class="flex flex-col">
            <SidebarLink
              v-for="link in view.views"
              :icon="link.icon"
              :label="link.label"
              :to="link.to"
              :key="link.label"
              :is-expanded="isExpanded"
              :is-active="isActiveTab(link.to)"
              class="my-0.5 emoji"
              :onClick="link.onClick"
            >
              <template #right>
                <Badge
                  v-if="isExpanded && linkBadge(link)"
                  :label="linkBadge(link) > 99 ? '99+' : String(linkBadge(link))"
                  theme="gray"
                  variant="subtle"
                />
              </template>
            </SidebarLink>
          </nav>
        </Section>
      </div>
    </div>
    <div class="grow" />
    <div class="flex flex-col gap-2">
      <TrialBanner
        v-if="isFCSite && !isCustomerPortal"
        :isSidebarCollapsed="!isExpanded"
      />
      <GettingStartedBanner
        v-if="showOnboardingBanner"
        :isSidebarCollapsed="!isExpanded"
        appName="helpdesk"
      />
      <SidebarLink
        v-if="isOnboardingStepsCompleted && !isCustomerPortal"
        :icon="HelpIcon"
        :label="__('Help')"
        :is-expanded="isExpanded"
        @click="
          () => {
            showHelpModal = minimize ? true : !showHelpModal;
            minimize = !showHelpModal;
          }
        "
      />

      <SidebarLink
        :icon="isDark ? LucideSun : LucideMoon"
        :is-active="false"
        :is-expanded="isExpanded"
        :label="isDark ? __('Light mode') : __('Dark mode')"
        :on-click="toggleDark"
      />
      <SidebarLink
        :icon="isExpanded ? LucideArrowLeftFromLine : LucideArrowRightFromLine"
        :is-active="false"
        :is-expanded="isExpanded"
        :label="isExpanded ? __('Collapse') : __('Expand')"
        :on-click="() => (isExpanded = !isExpanded)"
      />
    </div>
    <TrialBanner
      v-if="isFCSite && !isCustomerPortal"
      :isSidebarCollapsed="!isExpanded"
    />
    <SettingsModal v-model="showSettingsModal" />
    <ShortcutsModal v-model="showShortcutsModal" />
    <HelpModal
      v-if="showHelpModal"
      v-model="showHelpModal"
      v-model:articles="articles"
      appName="helpdesk"
      title="Frappe Helpdesk"
      :logo="logo"
      docsLink="https://docs.frappe.io/helpdesk"
      :afterSkip="(step: string) => capture('onboarding_step_skipped_' + step)"
      :afterSkipAll="() => capture('onboarding_steps_skipped')"
      :afterReset="(step: string) => capture('onboarding_step_reset_' + step)"
      :afterResetAll="() => capture('onboarding_steps_reset')"
    />
    <IntermediateStepModal
      v-model="showIntermediateModal"
      :currentStep="currentStep"
    />
    <CP v-model="showCommandPalette" />
  </div>
</template>

<script setup lang="ts">
import HDLogo from "@/assets/logos/HDLogo.vue";
import { Section, SidebarLink } from "@/components";
import Apps from "@/components/Apps.vue";
import CP from "@/components/command-palette/CP.vue";
import { FrappeCloudIcon, InviteCustomer } from "@/components/icons";
import ShortcutsModal from "@/components/modals/ShortcutsModal.vue";
import SettingsModal from "@/components/Settings/SettingsModal.vue";
import UserMenu from "@/components/UserMenu.vue";
import { useDevice } from "@/composables";
import { confirmLoginToFrappeCloud } from "@/composables/fc";
import { useScreenSize } from "@/composables/screen";
import { currentView, useView } from "@/composables/useView";
import { showNewContactModal } from "@/pages/desk/contact/dialogState";
import {
  showAssignmentModal,
  showCommentBox,
  showEmailBox,
} from "@/pages/ticket/modalStates";
import { useAuthStore } from "@/stores/auth";
import { useNotificationStore } from "@/stores/notification";
import { useSidebarStore } from "@/stores/sidebar";
import { capture } from "@/telemetry";
import { isCustomerPortal } from "@/utils";
import { call, createResource } from "frappe-ui";
import {
  GettingStartedBanner,
  HelpModal,
  IntermediateStepModal,
  minimize,
  showHelpModal,
  TrialBanner,
  useOnboarding,
} from "frappe-ui/frappe";

import { HelpIcon } from "frappe-ui/icons";
import { storeToRefs } from "pinia";
import { computed, h, markRaw, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  agentPortalSidebarOptions,
  customerPortalSidebarOptions,
} from "./layoutSettings";

import { useShortcut } from "@/composables/shortcuts";
import { useTelephonyStore } from "@/stores/telephony";
import { useWaLinesStore } from "@/stores/waLines";
import { globalStore } from "@/stores/globalStore";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";
import { __ } from "@/translation";
import LucideArrowLeftFromLine from "~icons/lucide/arrow-left-from-line";
import LucideArrowRightFromLine from "~icons/lucide/arrow-right-from-line";
import LucideBarChart2 from "~icons/lucide/bar-chart-2";
import LucideMoon from "~icons/lucide/moon";
import LucideSun from "~icons/lucide/sun";
import LucideBell from "~icons/lucide/bell";
import FileText from "~icons/lucide/file-text";
import Globe from "~icons/lucide/globe";
import LucideKeyboard from "~icons/lucide/keyboard";
import LucideLayoutDashboard from "~icons/lucide/layout-dashboard";
import LucideMail from "~icons/lucide/mail";
import MailOpen from "~icons/lucide/mail-open";
import MessageCircle from "~icons/lucide/message-circle";
import LucideSearch from "~icons/lucide/search";
import Ticket from "~icons/lucide/ticket";
import Timer from "~icons/lucide/timer";
import UserPen from "~icons/lucide/user-pen";
import LucideUserPlus from "~icons/lucide/user-plus";

import { useStorage } from "@vueuse/core";
import {
  setActiveSettingsTab,
  showSettingsModal,
} from "../Settings/settingsModal";

const { isMobileView } = useScreenSize();

// Dark mode — persisted in localStorage, applied via data-theme on <html>
const isDark = useStorage("hd-dark-mode", false);
function applyTheme(dark: boolean) {
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
}
applyTheme(isDark.value);
function toggleDark() {
  isDark.value = !isDark.value;
  applyTheme(isDark.value);
}

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const notificationStore = useNotificationStore();
const { isExpanded, width } = storeToRefs(useSidebarStore());
const device = useDevice();
const telephonyStore = useTelephonyStore();
const { isCallingEnabled } = storeToRefs(telephonyStore);

const waLinesStore = useWaLinesStore();
const { lines: waLines, totalUnread: waUnread } = storeToRefs(waLinesStore);
const waExpanded = useStorage("wa-sidebar-expanded", true);

const showShortcutsModal = ref(false);
const showCommandPalette = ref(false);

const { pinnedViews, publicViews } = useView();

const isFCSite = ref(window.is_fc_site);

const allViews = computed(() => {
  let items = isCustomerPortal.value
    ? customerPortalSidebarOptions
    : agentPortalSidebarOptions;

  if (!isCallingEnabled.value) {
    items = items.filter((item) => item.label !== __("Call Logs"));
  }

  const options = [
    {
      label: __("All Views"),
      hideLabel: true,
      opened: true,
      views: items,
    },
  ];
  if (publicViews.value?.length && !isCustomerPortal.value) {
    options.push({
      label: __("Public Views"),
      opened: true,
      hideLabel: false,
      views: parseViews(publicViews.value),
    });
  }
  if (pinnedViews.value?.length) {
    options.push({
      label: __("Private Views"),
      opened: true,
      hideLabel: false,
      views: parseViews(pinnedViews.value),
    });
  }
  return options;
});

function parseViews(views) {
  return views.map((view) => {
    return {
      label: view.label,
      icon: view.icon,
      to: {
        name: view.route_name,
        query: { view: view.name },
      },
      onClick: () => {
        currentView.value = {
          label: view.label,
          icon: view.icon,
        };
      },
    };
  });
}

const customerPortalDropdown = computed(() => [
  {
    label: __("Log out"),
    icon: "log-out",
    onClick: () => authStore.logout(),
  },
]);

const agentPortalDropdown = computed(() => [
  {
    component: markRaw(Apps),
  },
  {
    label: __("Customer portal"),
    icon: "users",
    onClick: () => {
      const path = router.resolve({ name: "TicketsCustomer" });
      window.open(path.href);
    },
  },
  {
    icon: "life-buoy",
    label: __("Support"),
    onClick: () => window.open("https://t.me/frappedesk"),
  },
  {
    icon: "book-open",
    label: __("Docs"),
    onClick: () => window.open("https://docs.frappe.io/helpdesk"),
  },
  {
    label: __("Login to Frappe Cloud"),
    icon: FrappeCloudIcon,
    onClick: () => confirmLoginToFrappeCloud(),
    condition: () => !isMobileView.value && window.is_fc_site,
  },
  {
    label: __("Shortcuts"),
    icon: h(LucideKeyboard),
    onClick: () => (showShortcutsModal.value = true),
  },
  {
    label: __("Settings"),
    icon: "settings",
    onClick: () => (showSettingsModal.value = true),
  },
  {
    group: __("Danger"),
    hideLabel: true,
    items: [
      {
        label: __("Log out"),
        icon: "log-out",
        onClick: () => authStore.logout(),
      },
    ],
  },
]);

const profileSettings = computed(() => {
  return isCustomerPortal.value
    ? customerPortalDropdown.value
    : agentPortalDropdown.value;
});

function isActiveTab(to: any) {
  if (route.query.view) {
    return route.query.view == to?.query?.view;
  }
  return route.name === to;
}

function openCommandPalette() {
  showCommandPalette.value = true;
}

const logo = h(
  HDLogo,
  {
    class: "h-12 w-12",
  },
  null
);

const showOnboardingBanner = computed(() => {
  return (
    !isCustomerPortal.value &&
    !isOnboardingStepsCompleted.value &&
    authStore.isManager
  );
});

const steps = [
  {
    name: "setup_email_account",
    title: __("Connect your support email"),
    completed: false,
    icon: markRaw(LucideMail),
    onClick: () => {
      minimize.value = true;
      showSettingsModal.value = true;
      setActiveSettingsTab("Email Accounts");
    },
  },
  {
    name: "invite_agents",
    title: __("Invite agents"),
    completed: false,
    icon: markRaw(LucideUserPlus),
    onClick: () => {
      minimize.value = true;
      showSettingsModal.value = true;
      setActiveSettingsTab("Invite Agents");
    },
  },
  {
    name: "setup_sla",
    title: __("Setup SLA"),
    completed: false,
    icon: markRaw(Timer),
    onClick: () => {
      setActiveSettingsTab("SLA Policies");
      showSettingsModal.value = true;
      minimize.value = true;
    },
  },
  {
    name: "create_first_ticket",
    title: __("Create a ticket"),
    completed: false,
    icon: markRaw(Ticket),
    onClick: () => {
      router.push({ name: "TicketAgentNew" });
      minimize.value = true;
    },
  },
  {
    name: "assign_to_agent",
    title: __("Assign a ticket to an agent"),
    completed: false,
    icon: markRaw(UserPen),
    onClick: async () => {
      await handleFirstTicketNavigation();
      showAssignmentModal.value = true;
      minimize.value = true;
    },
  },
  {
    name: "reply_on_ticket",
    title: __("Reply on a ticket"),
    completed: false,
    icon: markRaw(MailOpen),
    onClick: async () => {
      await handleFirstTicketNavigation();
      showEmailBox.value = true;
      showCommentBox.value = false;
      minimize.value = true;
    },
  },
  {
    name: "comment_on_ticket",
    title: __("Add a comment on a ticket"),
    completed: false,
    icon: markRaw(MessageCircle),
    onClick: async () => {
      await handleFirstTicketNavigation();
      showCommentBox.value = true;
      showEmailBox.value = false;
      minimize.value = true;
    },
  },
  {
    name: "first_article",
    title: __("Create an article"),
    completed: false,
    icon: markRaw(FileText),
    onClick: async () => {
      const generalCategory = await getGeneralCategory();
      router.push({
        name: "NewArticle",
        query: {
          title: __("General"),
        },
        params: { id: generalCategory },
      });
      minimize.value = true;
    },
  },
  {
    name: "add_invite_contact",
    title: __("Create & invite a contact"),
    completed: false,
    icon: markRaw(InviteCustomer),
    onClick: () => {
      minimize.value = true;
      currentStep.value = {
        title: __("Create & invite a contact"),
        buttonLabel: __("Create"),
        videoURL: "/assets/helpdesk/desk/videos/createInviteContact.mp4",
        onClick: async () => {
          showIntermediateModal.value = false;
          router.push({ name: "ContactList" });
          showNewContactModal.value = true;
        },
      };
      showIntermediateModal.value = true;
    },
  },
  {
    name: "explore_customer_portal",
    title: __("Explore customer portal"),
    completed: false,
    icon: markRaw(Globe),
    onClick: () => {
      window.open("/helpdesk/my-tickets", "_blank");
      updateOnboardingStep("explore_customer_portal");
      minimize.value = true;
    },
  },
];

const articles = ref([
  {
    title: "Introduction",
    opened: false,
    subArticles: [
      { name: "introduction", title: "Introduction" },
      { name: "setting-up", title: "Setting up" },
    ],
  },
  {
    title: "Getting Started",
    opened: false,
    subArticles: [
      {
        name: "lesson-1-your-first-ticket",
        title: "Creating a ticket",
      },
      {
        name: "lesson-2understanding-ticket-view",
        title: "Understanding ticket view",
      },
      {
        name: "lesson-3-agents-teams",
        title: "Agents & Teams",
      },
      {
        name: "customers-contacts",
        title: "Customers & Contacts",
      },
      {
        name: "lesson-4-knowledge-base",
        title: "Knowledge Base",
      },
      {
        name: "customer-portal",
        title: "Customer Portal",
      },
    ],
  },
  {
    title: "Masters",
    opened: false,
    subArticles: [
      { name: "ticket", title: "Ticket" },
      { name: "agent", title: "Agent" },
      { name: "team", title: "Team" },
      { name: "contact", title: "Contact" },
      { name: "customer", title: "Customer" },
      { name: "knowledge-base", title: "Knowledge Base" },
      { name: "saved-replies", title: "Saved Replies" },
      { name: "service-level-agreement", title: "Service Level Agreement" },
      { name: "ticket-type", title: "Ticket Type" },
      { name: "ticket-priority", title: "Ticket Priority" },
    ],
  },
  {
    title: "Customizations",
    opened: false,
    subArticles: [
      { name: "custom-actions", title: "Custom Actions" },
      { name: "field-dependency", title: "Field Dependency" },
      { name: "custom-views", title: "Custom Views" },
      {
        name: "settings",
        title: "Settings",
      },
    ],
  },
  {
    title: "Frappe Helpdesk Mobile",
    opened: false,
    subArticles: [
      { name: "pwa-installation", title: "Mobile App Installation" },
    ],
  },
]);

const showIntermediateModal = ref(false);
const currentStep = ref({});

const { isOnboardingStepsCompleted, setUp, updateOnboardingStep } =
  useOnboarding("helpdesk");

async function handleFirstTicketNavigation() {
  const ticket = await getFirstTicket();

  if (ticket) {
    router.push({
      name: "TicketAgent",
      params: { ticketId: ticket },
    });
  } else {
    router.push({ name: "TicketAgentNew" });
  }
}

async function getFirstTicket() {
  let ticket = localStorage.getItem("firstTicket");
  if (ticket) return ticket;
  return await call("helpdesk.api.onboarding.get_first_ticket");
}

async function getGeneralCategory() {
  let generalCategory = localStorage.getItem("generalCategoryId");
  if (!generalCategory) {
    generalCategory = await call(
      "helpdesk.api.onboarding.get_general_category_id"
    );
    if (!generalCategory) return;
    localStorage.setItem("generalCategoryId", generalCategory);
  }
  return generalCategory;
}

function setUpOnboarding() {
  if (!authStore.isManager) return;
  setUp(steps);
  showHelpModal.value = false; // don't auto-open on every load
  useShortcut({ key: "h", meta: true }, () => {
    showHelpModal.value = !showHelpModal.value;
  });
}

const openCounts = createResource({
  url: "helpdesk.api.general.get_my_open_counts",
  auto: true,
});

function linkBadge(link: any): number | null {
  if (isCustomerPortal.value) return null;
  const to = link.to;
  const routeName = typeof to === "string" ? to : to?.name;
  if (routeName === "TicketsAgent") return openCounts.data?.tickets || null;
  if (routeName === "TasksAgent") return openCounts.data?.tasks || null;
  if (routeName === "WhatsAppBusinessChat") return openCounts.data?.whatsapp || null;
  return null;
}

// Bump the WhatsApp line badge straight from the event payload. This used to
// call waLinesStore.reload(), which re-ran an aggregate over the whole message
// table — on every agent's sidebar, on every page, for every message in either
// direction. Outgoing messages never affect an unread count, so they are
// ignored outright.
function onBaileysMessage(data: { line?: string; is_incoming?: boolean }) {
  if (!data?.is_incoming || !data.line) return;
  waLinesStore.bumpUnread(data.line, 1);
}

// A dropped connection means missed bumps, and the agent may have read messages
// in another tab or on their phone while this one sat in the background.
function onVisibilityChange() {
  if (document.visibilityState === "visible") waLinesStore.reconcile();
}

onMounted(() => {
  setUpOnboarding();
  if (isCustomerPortal.value) return;
  useShortcut({ key: ",", meta: true }, () => {
    showSettingsModal.value = !showSettingsModal.value;
  });
  const { $socket } = globalStore();
  $socket.on("helpdesk:baileys-message", onBaileysMessage);
  $socket.on("connect", waLinesStore.reconcile);
  document.addEventListener("visibilitychange", onVisibilityChange);
});

onBeforeUnmount(() => {
  const { $socket } = globalStore();
  $socket.off("helpdesk:baileys-message", onBaileysMessage);
  $socket.off("connect", waLinesStore.reconcile);
  document.removeEventListener("visibilitychange", onVisibilityChange);
});
</script>
