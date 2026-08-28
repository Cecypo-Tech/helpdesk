import { createResource } from "frappe-ui";
import { defineStore } from "pinia";
import { computed } from "vue";

/**
 * Count of contacts waiting on an agent's verdict, for the sidebar badge.
 *
 * A store rather than a resource inside the sidebar component because two
 * places need the same number: the badge, and the queue page, which refreshes
 * it after acting so clearing the queue does not leave a stale count behind.
 *
 * Deliberately fetched once on load rather than polled. The count changes when
 * a customer replies with a PIN, which is minutes-to-hours scale, and a poll on
 * every agent's session would cost more than it tells anyone.
 */
export const useVerificationStore = defineStore("verification", () => {
  const pendingCount = createResource({
    url: "helpdesk.api.verification.get_pending_claim_count",
    // NOT `auto: true`. That defers the first fetch to onMounted, and a Pinia
    // store has no component instance to mount — the request simply never went
    // out, the count stayed 0, and the sidebar entry hid itself forever.
    //
    // Nor is it fetched here at store-creation time: the store is instantiated
    // during the sidebar's setup, before the session is established, and the
    // call came back 403. The sidebar calls refresh() from onMounted instead.
  });

  const count = computed(() => {
    const v = pendingCount.data;
    return typeof v === "number" ? v : 0;
  });

  function refresh() {
    pendingCount.reload();
  }

  return { pendingCount, count, refresh };
});
