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
        <div v-if="snap.invoices?.length" class="flex items-center gap-3">
          <Button variant="subtle" @click="toReply">
            <template #prefix><LucideMessageSquare class="size-4" /></template>
            Copy to WhatsApp reply
          </Button>
          <span v-if="!billingContact" class="text-sm text-ink-amber-3">
            Not a billing contact — check before sending amounts.
          </span>
        </div>
        <div v-if="snap.invoices?.length" class="overflow-x-auto">
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
import { Button, Dialog, toast } from "frappe-ui";
import { computed, ref } from "vue";
import LucideInfo from "~icons/lucide/info";
import LucideMessageSquare from "~icons/lucide/message-square";
import { invoiceMessage } from "@/utils/invoiceMessage";
import { sendToReply, useCustomerPending } from "@/composables/useCustomerPending";

// Agent-only figures from ERPNext (powerpack's customer snapshot). Hidden when
// ERPNext can't answer or the ticket has no linked ERPNext customer.
const props = defineProps<{ ticket?: string | number; customer?: string }>();
const show = ref(false);

// Reloaded by TicketDetailsTab once a customer change is saved.
const { snapshot: snap, erpUrl, hasOverdue, billingContact } = useCustomerPending(
  () => props.ticket
);

// Into the reply box (when the WhatsApp tab is open) and the clipboard (when it isn't).
async function toReply() {
  const text = invoiceMessage(snap.value, title.value);
  if (props.ticket) sendToReply(props.ticket, text);
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // clipboard needs https or localhost; the reply box still has it
  }
  show.value = false;
  toast.success("Added to the WhatsApp reply (also copied)");
}
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
