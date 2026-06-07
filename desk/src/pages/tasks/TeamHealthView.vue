<template>
  <div class="flex flex-col h-full overflow-hidden">
    <!-- Counts strip -->
    <div class="flex items-center gap-2 px-4 py-2 border-b border-outline-gray-1 bg-surface-white">
      <button
        v-for="f in filters"
        :key="f.key"
        class="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded border transition-colors"
        :class="activeFilter === f.key
          ? f.activeClass
          : 'bg-surface-white border-outline-gray-2 text-ink-gray-6 hover:border-outline-gray-4'"
        @click="activeFilter = activeFilter === f.key ? null : f.key"
      >
        {{ f.label }}
        <span class="font-semibold">{{ f.count }}</span>
      </button>
    </div>

    <!-- Grouped task list -->
    <div class="flex-1 overflow-auto p-4 space-y-5">
      <div v-if="resource.loading" class="text-sm text-ink-gray-5">{{ __('Loading…') }}</div>
      <div
        v-else-if="visibleGroups.length === 0"
        class="text-sm text-ink-gray-5 flex flex-col items-center gap-2 mt-20"
      >
        <LucideCheckCircle2 class="size-6 text-green-500" />
        {{ __('Nothing overdue or due soon. Nice.') }}
      </div>
      <div v-for="group in visibleGroups" :key="group.assignee" class="space-y-1.5">
        <div class="text-sm font-medium text-ink-gray-8">
          {{ group.assignee || __('Unassigned') }}
          <span class="text-ink-gray-5">({{ group.tasks.length }})</span>
        </div>
        <RouterLink
          v-for="task in group.tasks"
          :key="task.name"
          :to="{ name: 'TaskAgent', params: { taskId: task.name } }"
          class="flex items-center justify-between gap-3 rounded border border-outline-gray-1 bg-surface-white px-3 py-2 hover:bg-surface-gray-2"
        >
          <span class="truncate text-base text-ink-gray-8">{{ task.title }}</span>
          <span
            class="shrink-0 text-xs"
            :class="task._overdue ? 'text-red-600 font-medium' : 'text-amber-600'"
          >
            {{ task.due_date }}
          </span>
        </RouterLink>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { createResource } from "frappe-ui";
import { computed, ref } from "vue";
import LucideCheckCircle2 from "~icons/lucide/check-circle-2";

const activeFilter = ref<"overdue" | "due_soon" | null>(null);

const resource = createResource({
  url: "helpdesk.helpdesk.doctype.hd_task.hd_task.get_team_task_health",
  auto: true,
});

const counts = computed(() => resource.data?.counts ?? { overdue: 0, due_soon: 0, unassigned: 0 });

const filters = computed(() => [
  {
    key: "overdue",
    label: __("Overdue"),
    count: counts.value.overdue,
    activeClass: "bg-red-50 border-red-300 text-red-600 font-semibold",
  },
  {
    key: "due_soon",
    label: __("Due Soon"),
    count: counts.value.due_soon,
    activeClass: "bg-amber-50 border-amber-300 text-amber-600 font-semibold",
  },
]);

const allTasks = computed(() => {
  const overdue = (resource.data?.overdue ?? []).map((t: any) => ({ ...t, _overdue: true }));
  const dueSoon = (resource.data?.due_soon ?? []).map((t: any) => ({ ...t, _overdue: false }));
  if (activeFilter.value === "overdue") return overdue;
  if (activeFilter.value === "due_soon") return dueSoon;
  return [...overdue, ...dueSoon];
});

const visibleGroups = computed(() => {
  const byAssignee = new Map<string, any[]>();
  for (const task of allTasks.value) {
    const key = task.assigned_to || "";
    if (!byAssignee.has(key)) byAssignee.set(key, []);
    byAssignee.get(key)!.push(task);
  }
  // Named assignees first (alphabetical), unassigned ("") bucket last.
  return [...byAssignee.entries()]
    .sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b)))
    .map(([assignee, tasks]) => ({ assignee, tasks }));
});
</script>
