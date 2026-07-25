import { defineStore } from "pinia";
import { createResource } from "frappe-ui";
import { ref, computed } from "vue";

export interface WaLine {
  name: string;
  label: string;
  instance_name: string;
  display_label: string;
  unread: number;
}

export const useWaLinesStore = defineStore("waLines", () => {
  const lines = ref<WaLine[]>([]);

  const linesResource = createResource({
    url: "helpdesk.integrations.wa.get_wa_lines",
    auto: true,
    onSuccess(data: WaLine[]) {
      lines.value = data || [];
    },
  });

  const totalUnread = computed(() =>
    lines.value.reduce((sum, l) => sum + (l.unread || 0), 0)
  );

  function reload() {
    linesResource.reload();
  }

  // The badge is maintained locally rather than refetched. get_wa_lines is an
  // aggregate over the message table and the sidebar is mounted on every agent
  // page, so refetching it on each `helpdesk:baileys-message` meant every
  // connected agent ran that query for every message anyone sent or received.
  // Incoming messages bump the count; mark-read hands back exactly how many it
  // cleared, so the two cancel out. `reconcile()` repairs any drift.
  function bumpUnread(line: string, delta: number) {
    const target = lines.value.find((l) => l.name === line);
    if (!target) return;
    target.unread = Math.max(0, (target.unread || 0) + delta);
  }

  // Local counting can drift when events are missed (socket reconnect) or when
  // the same agent reads elsewhere (second tab, phone). Callers fire this on
  // those boundaries; the throttle keeps a flapping connection or rapid tab
  // switching from turning the repair itself back into a hot path.
  const RECONCILE_INTERVAL = 60_000;
  let lastReconcile = 0;

  function reconcile() {
    const now = Date.now();
    if (now - lastReconcile < RECONCILE_INTERVAL) return;
    lastReconcile = now;
    linesResource.reload();
  }

  return {
    lines,
    totalUnread,
    reload,
    bumpUnread,
    reconcile,
    loading: linesResource.loading,
  };
});
