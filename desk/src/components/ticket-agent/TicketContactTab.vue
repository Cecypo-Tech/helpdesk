<template>
  <!-- TODO: handle ellipsis -->
  <div>
    <!-- Contact -->
    <div v-if="!contact.loading">
      <div class="flex gap-3 items-center px-5 py-2.5">
        <Avatar
          :label="contact.data.name"
          :image="contact.data.image"
          size="2xl"
        />
        <p class="text-ink-gray-8 font-medium text-xl max-w-full truncate">
          {{ contact.data.name }}
        </p>
      </div>
      <div class="px-5 text-ink-gray-5 pb-2">
        <!-- Company -->
        <div class="flex gap-2 items-center p-1.5" v-if="contact.data.company_name">
          <LucideBuilding2 class="size-4 shrink-0" />
          <p class="text-p-sm text-ink-gray-6 truncate">
            {{ contact.data.company_name }}
          </p>
        </div>
        <!-- Designation -->
        <div class="flex gap-2 items-center p-1.5" v-if="contact.data.designation">
          <LucideBriefcase class="size-4 shrink-0" />
          <p class="text-p-sm text-ink-gray-6 truncate">
            {{ contact.data.designation }}
          </p>
        </div>
        <!-- Email Id -->
        <div class="flex gap-2 items-center p-1.5">
          <EmailIcon class="size-4 shrink-0" />
          <p class="text-p-sm text-ink-gray-6 truncate">
            {{ contact.data.email_id }}
          </p>
          <CopyIcon
            class="size-4 shrink-0 cursor-pointer"
            @click="
              copyToClipboard(
                contact.data.email_id,
                `'${contact.data.email_id}' copied to clipboard`
              )
            "
          />
        </div>
        <!-- Mobile Number -->
        <div
          class="flex gap-2 items-center p-1.5"
          v-if="contact.data.mobile_no || contact.data.phone"
        >
          <PhoneIcon class="size-4 shrink-0" />
          <p class="text-p-sm text-ink-gray-6 truncate">
            {{ contact.data.mobile_no || contact.data.phone }}
          </p>
          <CopyIcon
            class="size-4 shrink-0 cursor-pointer"
            @click="
              copyToClipboard(
                contact.data.mobile_no || contact.data.phone,
                `'${contact.data.mobile_no || contact.data.phone}' copied to clipboard`
              )
            "
          />
        </div>
        <!-- Support Coverage -->
        <div
          v-if="entitlement.data?.product"
          class="mt-3 border-t border-outline-gray-2 pt-3"
        >
          <div class="mb-1.5 text-xs font-medium text-ink-gray-5">Support</div>
          <div class="flex items-center gap-2">
            <span class="truncate text-sm text-ink-gray-8">
              {{ entitlement.data.product }}
            </span>
            <span
              class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium"
              :class="statusTone"
            >
              {{ entitlement.data.status }}
            </span>
          </div>
          <div
            v-if="entitlement.data.support_expiry"
            class="mt-1 text-xs text-ink-gray-5"
          >
            Support until {{ entitlement.data.support_expiry }}
          </div>
        </div>
      </div>
    </div>

    <!-- Recent / Similar Tickets -->
    <template v-if="!recentSimilarTickets.loading">
      <div class="px-5 border-t pb-2.5" v-for="section in sections">
        <Section
          :key="section.label"
          :label="section.label"
          :hideLabel="section.hideLabel"
          :opened="section.opened"
        >
          <template #header="{ opened, hide, toggle }">
            <div class="flex gap-2.5 items-center py-[13px] justify-between">
              <Tooltip :text="section.tooltipMessage">
                <span
                  class="text-ink-gray-8 font-medium text-base cursor-pointer select-none"
                  @click="toggle"
                >
                  {{ section.label }}
                </span>
              </Tooltip>
              <LucideChevronDown
                class="size-4 text-ink-gray-6 cursor-pointer"
                :class="{ 'rotate-180': opened }"
                @click="toggle"
              />
            </div>
          </template>
          <ul>
            <li
              v-for="ticket in section.tickets"
              :key="ticket.name"
              class="py-2.5 cursor-pointer"
              @click="openTicket(ticket.name)"
            >
              <p class="text-base text-ink-gray-8 max-w-[60%] truncate">
                {{ ticket.subject }}
              </p>
              <div class="flex items-end justify-between">
                <p class="text-base text-ink-gray-5">
                  {{ formatDate(ticket.creation) + " &#183; " }}
                  <span class="transition duration-400 hover:underline">
                    {{ "#" + ticket.name }}
                  </span>
                </p>
                <p
                  class="px-1.5 py-[3px] text-sm rounded-sm max-w-[80px] text-center truncate h-5"
                  :class="getStatusColor(ticket.status)"
                >
                  {{ ticket.status }}
                </p>
              </div>
            </li>
          </ul>
        </Section>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { useTelephonyStore } from "@/stores/telephony";
import { useTicketStatusStore } from "@/stores/ticketStatus";
import {
  RecentSimilarTicketsSymbol,
  TicketContactSymbol,
  TicketSymbol,
} from "@/types";
import { copyToClipboard } from "@/utils";
import dayjs from "dayjs";
import { Avatar, Tooltip, createResource } from "frappe-ui";
import { storeToRefs } from "pinia";
import { computed, inject } from "vue";
import { CopyIcon } from "../icons";
import EmailIcon from "../icons/EmailIcon.vue";
import PhoneIcon from "../icons/PhoneIcon.vue";
import Section from "../Section.vue";
const telephonyStore = useTelephonyStore();
const { isCallingEnabled } = storeToRefs(telephonyStore);

const contact = inject(TicketContactSymbol);
const recentSimilarTickets = inject(RecentSimilarTicketsSymbol);
const ticket = inject(TicketSymbol);
const dateFormat = window.date_format;

const entitlement = createResource({
  url: "helpdesk.api.entitlement.get_ticket_entitlement",
  makeParams: () => ({ ticket: ticket.value?.doc?.name }),
  auto: true,
});

const statusTone = computed(() => {
  switch (entitlement.data?.status) {
    case "Covered":
      return "bg-surface-green-2 text-ink-green-3";
    case "Expired":
      return "bg-surface-amber-2 text-ink-amber-3";
    case "Not Entitled":
      return "bg-surface-gray-3 text-ink-gray-7";
    default:
      return "bg-surface-gray-2 text-ink-gray-5";
  }
});

const { getStatus, colorMap } = useTicketStatusStore();

function getStatusColor(status: string) {
  let { color } = getStatus(status);

  if (colorMap[color]) {
    return colorMap[color];
  } else {
    return colorMap["Default"];
  }
}

const sections = computed(() => {
  if (recentSimilarTickets.value.loading || !recentSimilarTickets.value.data) {
    return [];
  }
  const recentTickets = recentSimilarTickets.value?.data?.recent_tickets || [];
  const similarTickets =
    recentSimilarTickets.value?.data?.similar_tickets || [];
  const _sections = [];
  if (recentTickets.length) {
    _sections.push({
      label: "Recent Tickets",
      tooltipMessage: "Tickets recently raised by this contact/customer",
      hideLabel: false,
      opened: true,
      tickets: recentTickets,
    });
  }
  if (similarTickets.length) {
    _sections.push({
      label: "Similar Tickets",
      tooltipMessage: "Tickets with similar queries",
      hideLabel: false,
      opened: true,
      tickets: similarTickets,
    });
  }
  return _sections;
});

function formatDate(date: string) {
  return dayjs(date).format(dateFormat.toUpperCase());
}

function openTicket(name: string) {
  let url = window.location.origin + "/helpdesk/tickets/" + name;

  window.open(url, "_blank");
}
// v-if="(false && contact.data.mobile_no) || contact.data.phone"
</script>

<style scoped></style>
