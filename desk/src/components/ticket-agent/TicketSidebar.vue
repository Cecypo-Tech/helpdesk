<template>
  <Resizer class="flex flex-col border-l" side="right">
    <!-- Tab bar — custom render so Tasks tab can carry a count badge -->
    <div class="flex shrink-0 border-b border-outline-gray-2 px-4 pt-3">
      <button
        v-for="tab in tabs"
        :key="tab.value"
        class="flex items-center gap-1.5 border-b-2 px-3 pb-2 text-xs font-medium transition-colors"
        :class="
          currentTab === tab.value
            ? 'border-blue-600 text-ink-gray-9'
            : 'border-transparent text-ink-gray-5 hover:text-ink-gray-8'
        "
        @click="currentTab = tab.value"
      >
        {{ tab.label }}
        <span
          v-if="tab.count"
          class="rounded-full bg-blue-600 px-1.5 py-0.5 text-[9px] font-bold leading-none text-white"
        >{{ tab.count }}</span>
      </button>
    </div>

    <div class="max-h-full flex-1 overflow-hidden">
      <TicketDetailsTab v-if="currentTab === 'details'" />
      <TicketContactTab v-else-if="currentTab === 'contact'" />
      <!-- v-show keeps TicketTasksTab mounted so it can emit its count
           even while Details or Contact is the active tab -->
      <TicketTasksTab
        v-show="currentTab === 'tasks'"
        @countChange="taskCount = $event"
      />
    </div>
  </Resizer>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import Resizer from "../Resizer.vue";
import TicketContactTab from "./TicketContactTab.vue";
import TicketDetailsTab from "./TicketDetailsTab.vue";
import TicketTasksTab from "./TicketTasksTab.vue";

const currentTab = ref("details");
const taskCount = ref(0);

const tabs = computed(() => [
  { label: "Details", value: "details", count: 0 },
  { label: "Contact", value: "contact", count: 0 },
  { label: "Tasks",   value: "tasks",   count: taskCount.value },
]);
</script>
