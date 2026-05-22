import { defineStore } from "pinia";
import { createResource } from "frappe-ui";
import { ref, computed } from "vue";

export interface EvolutionLine {
  name: string;
  label: string;
  instance_name: string;
  display_label: string;
  unread: number;
}

export const useEvolutionLinesStore = defineStore("evolutionLines", () => {
  const lines = ref<EvolutionLine[]>([]);

  const linesResource = createResource({
    url: "helpdesk.integrations.evolution.get_evolution_lines",
    auto: true,
    onSuccess(data: EvolutionLine[]) {
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
