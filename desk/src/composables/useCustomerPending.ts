import { createResource } from "frappe-ui";
import { computed, ref, watch } from "vue";

// One request per ticket, shared by the Customer field, the (i) dialog and the
// WhatsApp reply boxes, so they all turn red together and refresh together.
// ponytail: never evicted; fine for a session's worth of tickets.
const resources = new Map<string, any>();

function resourceFor(ticket: string) {
  if (!resources.has(ticket)) {
    resources.set(
      ticket,
      createResource({
        url: "helpdesk.api.entitlement.get_ticket_customer_snapshot",
        params: { ticket },
        auto: true,
      })
    );
  }
  return resources.get(ticket);
}

export function useCustomerPending(ticket: () => string | number | undefined) {
  const res = computed(() => {
    const t = ticket();
    return t ? resourceFor(String(t)) : null;
  });
  const snapshot = computed(() =>
    res.value?.data?.status === "ok" ? res.value.data.snapshot : null
  );
  const hasOverdue = computed(() =>
    (snapshot.value?.invoices || []).some((i: any) => i.days_overdue > 0)
  );
  const missingCustomer = computed(() => !!res.value?.data?.missing_customer);
  const billingContact = computed(() => !!res.value?.data?.billing_contact);
  return {
    snapshot,
    erpUrl: computed(() => (res.value?.data?.erp_url || "").replace(/\/$/, "")),
    hasOverdue,
    missingCustomer,
    billingContact,
    // Missing customer, or overdue invoices: either way the agent should notice.
    pending: computed(() => missingCustomer.value || hasOverdue.value),
    // A request already in flight started before the save and would be reused
    // with stale data, so let it finish and then ask again.
    reload: async () => {
      const r = res.value;
      if (!r) return;
      if (r.loading && r.promise) await r.promise.catch(() => {});
      r.reload();
    },
  };
}

// The (i) dialog hands text to whichever WhatsApp reply box shows this ticket.
const replyInsert = ref<{ ticket: string; text: string; at: number } | null>(null);

export function sendToReply(ticket: string | number, text: string) {
  replyInsert.value = { ticket: String(ticket), text, at: Date.now() };
}

export function onReplyInsert(
  ticket: () => string | number | undefined,
  apply: (text: string) => void
) {
  watch(replyInsert, (v) => {
    if (v && v.ticket === String(ticket())) apply(v.text);
  });
}
