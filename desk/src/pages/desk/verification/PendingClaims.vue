<template>
  <div class="flex flex-col">
    <LayoutHeader>
      <template #left-header>
        <div class="text-lg font-medium text-ink-gray-9">
          {{ __("Verifications") }}
        </div>
      </template>
      <template #right-header>
        <Button
          :label="__('Refresh')"
          theme="gray"
          variant="subtle"
          :loading="claims.loading"
          @click="reload"
        >
          <template #prefix>
            <LucideRefreshCw class="h-4 w-4" />
          </template>
        </Button>
      </template>
    </LayoutHeader>

    <div class="flex-1 overflow-auto px-5 py-4">
      <p class="mb-4 max-w-[65ch] text-p-sm text-ink-gray-6">
        {{
          __(
            "Unrecognised WhatsApp numbers that replied with a KRA PIN. Open a claim to see the conversation it came from — linking happens there, on the ticket."
          )
        }}
      </p>

      <div
        v-if="claims.loading && !claims.data"
        class="text-p-sm text-ink-gray-5"
      >
        {{ __("Loading…") }}
      </div>

      <!-- An empty queue is the normal state, so it says so plainly rather
           than looking like something failed to load. -->
      <div
        v-else-if="!rows.length"
        class="flex flex-col items-center justify-center gap-2 rounded border border-outline-gray-2 py-16"
      >
        <LucideCheckCircle2 class="size-8 text-ink-gray-3" />
        <p class="text-base text-ink-gray-7">{{ __("Nothing waiting") }}</p>
        <p class="text-p-sm text-ink-gray-5">
          {{ __("Claims appear here when a customer sends their PIN.") }}
        </p>
      </div>

      <div v-else class="flex flex-col gap-2">
        <button
          v-for="row in rows"
          :key="row.contact"
          class="flex w-full items-start gap-4 rounded border border-outline-gray-2 bg-surface-white p-3 text-left hover:bg-surface-gray-1 focus:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-3"
          :disabled="!row.ticket"
          @click="openTicket(row)"
        >
          <div class="min-w-0 flex-1">
            <div class="truncate font-medium text-ink-gray-8">
              {{ row.contact }}
            </div>
            <div class="mt-0.5 text-p-sm text-ink-gray-6">
              {{ __("Claims") }}
              <span v-if="row.claimed_company" class="text-ink-gray-7">
                {{ row.claimed_company }} &middot;
              </span>
              <span class="font-mono text-ink-gray-8">{{
                row.claimed_tax_id
              }}</span>
            </div>

            <!-- The match is the whole reason a claim is actionable, so it is
                 stated on the row. No match is worth seeing too: it means the
                 PIN is not in the ERPNext mirror, which is a data problem
                 rather than something to approve. -->
            <div
              v-if="row.matches.length"
              class="mt-1 text-p-sm text-ink-gray-7"
            >
              &rarr;
              {{ row.matches.map((m) => m.customer_name).join(", ") }}
            </div>
            <div v-else class="mt-1 text-p-sm text-ink-gray-5">
              {{ __("No customer matches that PIN") }}
            </div>
          </div>

          <div class="flex shrink-0 items-center gap-2 pt-0.5">
            <!-- dayjs.tz, not dayjs: the value is a server datetime, and
                 dayjs.ts sets the user's timezone as the default. Read as
                 browser-local it renders as a future time ("in 2 hours"). -->
            <span v-if="row.asked_on" class="text-p-sm text-ink-gray-5">
              {{ dayjs.tz(row.asked_on).fromNow() }}
            </span>
            <span
              v-if="row.ticket"
              class="whitespace-nowrap text-p-sm text-ink-gray-6"
            >
              #{{ row.ticket }}
            </span>
            <LucideChevronRight
              v-if="row.ticket"
              class="size-4 text-ink-gray-5"
            />
          </div>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import LayoutHeader from "@/components/LayoutHeader.vue";
import { useVerificationStore } from "@/stores/verification";
import { __ } from "@/translation";
import { dayjs } from "@/dayjs";
import { Button, createResource } from "frappe-ui";
import { computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import LucideChevronRight from "~icons/lucide/chevron-right";
import LucideCheckCircle2 from "~icons/lucide/check-circle-2";
import LucideRefreshCw from "~icons/lucide/refresh-cw";

const router = useRouter();
const verificationStore = useVerificationStore();

const claims = createResource({
  url: "helpdesk.api.verification.get_pending_claims",
  auto: true,
});

const rows = computed(() => claims.data || []);

function reload() {
  claims.reload();
  // The badge is a separate, cheaper endpoint, so it has to be told too —
  // otherwise clearing the queue leaves a stale count in the sidebar.
  verificationStore.refresh();
}

onMounted(reload);

function openTicket(row) {
  if (!row.ticket) return;
  router.push({ name: "TicketAgent", params: { ticketId: row.ticket } });
}
</script>
