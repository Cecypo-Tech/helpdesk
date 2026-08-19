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
          v-if="entitlement.data?.customer"
          class="mt-3 border-t border-outline-gray-2 pt-3"
        >
          <div class="mb-1.5 flex items-baseline gap-1.5">
            <span class="text-xs font-medium text-ink-gray-5">Support</span>
            <span class="truncate text-xs text-ink-gray-4">
              {{ entitlement.data.customer }}
            </span>
          </div>

          <!-- Ticket is tagged with a product the company does not hold -->
          <div
            v-if="unheldTicketProduct"
            class="mb-1.5 flex items-center gap-2"
          >
            <span class="truncate text-sm text-ink-gray-8">
              {{ unheldTicketProduct }}
            </span>
            <span
              class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium"
              :class="toneFor('Not Entitled')"
            >
              Not Entitled
            </span>
          </div>

          <div v-if="heldProducts.length" class="space-y-1.5">
            <div
              v-for="row in heldProducts"
              :key="row.product"
              class="flex items-center gap-2"
            >
              <span
                class="truncate text-sm"
                :class="
                  row.product === entitlement.data.product
                    ? 'font-medium text-ink-gray-8'
                    : 'text-ink-gray-7'
                "
              >
                {{ row.product }}
              </span>
              <span
                class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium"
                :class="toneFor(row.status)"
              >
                {{ row.status }}
              </span>
              <span class="shrink-0 text-xs text-ink-gray-5">
                {{ expiryLabel(row) }}
              </span>
            </div>
          </div>

          <div v-else class="text-xs text-ink-gray-5">No products recorded</div>

          <!-- Account standing. Advisory only: it never gates support. An
               ERPNext outage shows "unavailable" rather than hiding the row,
               so nobody mistakes a broken lookup for a clean account. -->
          <div v-if="standingLabel" class="mt-2 flex items-center gap-2">
            <span class="shrink-0 text-xs text-ink-gray-5">Account</span>
            <span
              class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium"
              :class="standingTone"
            >
              {{ standingLabel }}
            </span>
          </div>
        </div>

        <!-- Unverified WhatsApp contact claim. Only shown while there is
             something for an agent to act on: a never-asked, verified, or
             already-rejected contact renders nothing here. -->
        <div
          v-if="claim.data && ['Asked', 'Claimed'].includes(claim.data.status)"
          class="mt-3 rounded border border-outline-gray-2 bg-surface-gray-1 p-3 text-base"
        >
          <div class="font-medium text-ink-gray-8">Unverified number</div>

          <div v-if="claim.data.status === 'Asked'" class="mt-1 text-ink-gray-6">
            Asked for a KRA PIN; no reply yet.
          </div>

          <template v-else>
            <div class="mt-1 text-ink-gray-6">
              Claims
              <!-- A message that carried a PIN and nothing else has no company
                   to show; omit the field rather than render a dangling middot. -->
              <span v-if="claim.data.claimed_company" class="text-ink-gray-8">
                {{ claim.data.claimed_company }} &middot;
              </span>
              <span class="text-ink-gray-8">{{ claim.data.claimed_tax_id }}</span>
            </div>

            <div v-if="!claim.data.matches.length" class="mt-2 text-ink-gray-6">
              No customer matches that PIN.
            </div>

            <div
              v-for="m in claim.data.matches"
              :key="m.name"
              class="mt-2 flex items-center gap-2"
            >
              <span class="text-ink-gray-8 truncate">{{ m.customer_name }}</span>
              <Button
                variant="subtle"
                label="Link"
                :disabled="claimBusy"
                @click="approve(m.name)"
              />
            </div>

            <Button
              class="mt-2"
              variant="ghost"
              label="Dismiss"
              :disabled="claimBusy"
              @click="reject()"
            />
          </template>
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
import { Avatar, Button, Tooltip, createResource, toast } from "frappe-ui";
import { storeToRefs } from "pinia";
import { computed, inject, ref, watch } from "vue";
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
});

// `TicketAgent` is reused across ticket navigation (no route `:key`), so the
// subtree never unmounts. `makeParams` alone is not reactively tracked and
// `auto: true` only fires once on first mount, so without this watcher the
// badge keeps showing the previous ticket's product/coverage. On the
// WhatsApp page the sidebar can mount before the ticket doc resolves, so the
// watcher (not a one-shot auto fetch) is also what lets it retry once the
// name becomes available.
const standing = createResource({
  url: "helpdesk.api.entitlement.get_ticket_standing",
  makeParams: () => ({ ticket: ticket.value?.doc?.name }),
});

watch(
  () => ticket.value?.doc?.name,
  (name) => {
    if (name) {
      entitlement.reload();
      // Separate request on purpose: this one crosses the network to ERPNext,
      // so the coverage panel must not wait on it.
      standing.reload();
    }
  },
  { immediate: true }
);

// Unrecognised WhatsApp sender's self-reported company/PIN claim, and the
// customers it could plausibly match. Same watch-not-auto reasoning as
// above: makeParams isn't reactive, so this would otherwise show the
// previous ticket's claim after navigation.
const claim = createResource({
  url: "helpdesk.api.verification.get_contact_claim",
  makeParams: () => ({ ticket: ticket.value?.doc?.name }),
});

watch(
  () => ticket.value?.doc?.name,
  (name) => {
    if (name) claim.fetch();
  },
  { immediate: true }
);

// Held while an approve/reject request is in flight. Both endpoints are
// idempotent server-side, but linking a contact to a customer is the most
// security-relevant action in this panel — a double click firing it twice, or
// a failure that looks identical to a success, are both worth ruling out.
const claimBusy = ref(false);

function submitClaimAction(resource, failure: string, onDone?: () => void) {
  if (claimBusy.value) return;
  claimBusy.value = true;
  return resource
    .fetch()
    .then(() => {
      claim.fetch();
      onDone?.();
    })
    .catch((e) => {
      toast.error(e?.messages?.[0] || e?.message || failure);
    })
    .finally(() => {
      claimBusy.value = false;
    });
}

// Never triggered implicitly (e.g. "only one match") — only an explicit
// agent click may link a contact to a customer.
function approve(customer: string) {
  return submitClaimAction(
    createResource({
      url: "helpdesk.api.verification.approve_contact_link",
      params: { contact: claim.data.contact, customer },
    }),
    "Could not link this contact",
    () => {
      // The endpoint backfills HD Ticket.customer, but entitlement and standing
      // are separate requests that already resolved against the empty ticket.
      // Reload them so the coverage panel replaces the claim right away rather
      // than only after the agent reloads the page.
      entitlement.reload();
      standing.reload();
    }
  );
}

function reject() {
  return submitClaimAction(
    createResource({
      url: "helpdesk.api.verification.reject_contact_claim",
      params: { contact: claim.data.contact },
    }),
    "Could not dismiss this claim"
  );
}

// Only ever shown to agents, and never quoted by the bot: a WhatsApp number
// can be a shared office handset.
const standingLabel = computed(() => {
  const d = standing.data;
  if (!d || d.status === "unknown") return null;
  if (d.status === "unavailable") return "Standing unavailable";
  if (!d.is_overdue) return "No overdue balance";
  const amount = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 0,
  }).format(d.overdue || 0);
  return `${d.currency || ""} ${amount} overdue · ${d.days_overdue}d`.trim();
});

const standingTone = computed(() => {
  const d = standing.data;
  if (!d) return "bg-surface-gray-2 text-ink-gray-5";
  if (d.status === "unavailable") return "bg-surface-gray-2 text-ink-gray-5";
  return d.is_overdue
    ? "bg-surface-red-2 text-ink-red-3"
    : "bg-surface-green-2 text-ink-green-3";
});

function toneFor(status) {
  switch (status) {
    case "Covered":
      return "bg-surface-green-2 text-ink-green-3";
    case "Expired":
      return "bg-surface-amber-2 text-ink-amber-3";
    case "Not Entitled":
      return "bg-surface-gray-3 text-ink-gray-7";
    default:
      return "bg-surface-gray-2 text-ink-gray-5";
  }
}

// A blank support_expiry means permanently covered, not missing data — say so
// rather than rendering an empty cell an agent would read as "unknown".
function expiryLabel(row) {
  if (!row.support_expiry) return "no expiry";
  const when = dayjs(row.support_expiry).format("D MMM YYYY");
  return row.expired ? `expired ${when}` : `until ${when}`;
}

// The ticket's own product first, then the rest alphabetically. Agents look
// for "what is this ticket about" before "what else do they run".
const heldProducts = computed(() => {
  const rows = [...(entitlement.data?.entitlements ?? [])];
  const current = entitlement.data?.product;
  rows.sort((a, b) => {
    if (a.product === current) return -1;
    if (b.product === current) return 1;
    return a.product.localeCompare(b.product);
  });
  return rows;
});

// A ticket tagged with a product the company does not hold. Worth surfacing:
// it is either pre-sales, an evaluation, or stale data — all things an agent
// should see rather than have silently omitted.
const unheldTicketProduct = computed(() => {
  const current = entitlement.data?.product;
  if (!current) return null;
  const held = entitlement.data?.entitlements ?? [];
  return held.some((r) => r.product === current) ? null : current;
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
