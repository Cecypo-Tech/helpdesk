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

  return { lines, totalUnread, reload, loading: linesResource.loading };
});
