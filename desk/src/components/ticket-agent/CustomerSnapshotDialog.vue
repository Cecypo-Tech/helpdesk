<template>
  <button
    v-if="snap"
    type="button"
    class="flex items-center"
    :class="hasOverdue ? 'text-ink-red-3' : 'text-ink-gray-4'"
    :title="hasOverdue ? 'Overdue invoices' : 'Account summary'"
    @click="show = true"
  >
    <LucideInfo class="size-4" />
  </button>
  <Dialog v-model="show" :options="{ title: title, size: '3xl' }">
    <template #body-content>
      <div v-if="snap" class="flex flex-col gap-4 text-base text-ink-gray-8">
        <div class="flex flex-wrap gap-6">
          <div v-for="t in totals" :key="t.label">
            <div class="text-sm text-ink-gray-5">{{ t.label }}</div>
            <div class="font-semibold" :class="t.tone">{{ t.value }}</div>
          </div>
        </div>
        <div v-if="!snap.invoices?.length" class="text-ink-gray-5">
          No unpaid invoices.
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="text-ink-gray-5 text-left">
              <tr>
                <th class="py-1 pr-3">Invoice</th>
                <th class="py-1 pr-3">Date</th>
                <th class="py-1 pr-3">Due</th>
                <th class="py-1 pr-3 text-right">Days overdue</th>
                <th class="py-1 pr-3 text-right">Total</th>
                <th class="py-1 pr-3 text-right">Paid</th>
                <th class="py-1 text-right">Outstanding</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="inv in snap.invoices"
                :key="inv.name"
                class="border-t border-outline-gray-1"
              >
                <td class="py-1 pr-3">
                  <a
                    class="underline"
                    :href="`${erpUrl}/app/sales-invoice/${inv.name}`"
                    target="_blank"
                    rel="noopener"
                    >{{ inv.name }}</a
                  >
                </td>
                <td class="py-1 pr-3">{{ inv.posting_date }}</td>
                <td class="py-1 pr-3">{{ inv.due_date }}</td>
                <td
                  class="py-1 pr-3 text-right"
                  :class="inv.days_overdue > 0 ? 'text-ink-red-3' : ''"
                >
                  {{ inv.days_overdue || "" }}
                </td>
                <td class="py-1 pr-3 text-right">{{ money(inv.grand_total) }}</td>
                <td class="py-1 pr-3 text-right">{{ money(inv.paid) }}</td>
                <td class="py-1 text-right">
                  {{ money(inv.outstanding_amount) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup lang="ts">
import { Dialog, createResource } from "frappe-ui";
import { computed, ref, watch } from "vue";
import LucideInfo from "~icons/lucide/info";

// Agent-only figures from ERPNext (powerpack's customer snapshot). Hidden when
// ERPNext can't answer or the ticket has no linked ERPNext customer.
const props = defineProps<{ ticket?: string | number; customer?: string }>();
const show = ref(false);

const res = createResource({
  url: "helpdesk.api.entitlement.get_ticket_customer_snapshot",
  makeParams: () => ({ ticket: props.ticket }),
});

watch(
  () => [props.ticket, props.customer],
  () => (props.ticket && props.customer ? res.reload() : res.reset()),
  { immediate: true }
);

const snap = computed(() =>
  res.data?.status === "ok" ? res.data.snapshot : null
);
const erpUrl = computed(() => (res.data?.erp_url || "").replace(/\/$/, ""));
const hasOverdue = computed(() =>
  (snap.value?.invoices || []).some((i) => i.days_overdue > 0)
);
const title = computed(() => snap.value?.customer_name || props.customer);

function money(v: number | null | undefined) {
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(v || 0);
}

const totals = computed(() => {
  const s = snap.value;
  if (!s) return [];
  const cur = s.currency ? `${s.currency} ` : "";
  return [
    { label: "Outstanding", value: cur + money(s.outstanding_total), tone: "" },
    {
      label: `Overdue (${s.overdue_count || 0})`,
      value: cur + money(s.overdue_total),
      tone: s.overdue_total > 0 ? "text-ink-red-3" : "",
    },
    { label: "Advances", value: cur + money(s.advances_total), tone: "" },
    { label: "Net position", value: cur + money(s.net_position), tone: "" },
  ];
});
</script>
