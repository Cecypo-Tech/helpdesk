<template>
  <div class="flex min-h-0 flex-1 flex-col overflow-y-auto bg-surface-gray-1">
    <!-- Header -->
    <div class="flex items-center gap-3 border-b border-outline-gray-2 bg-surface-white px-6 py-3">
      <button
        class="flex h-7 w-7 items-center justify-center rounded-full text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8"
        @click="router.push('/whatsapp')"
        title="Back to chats"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>
      <WhatsAppIcon class="h-4 w-4 text-green-600" />
      <h1 class="text-sm font-semibold text-ink-gray-9">WhatsApp Analytics</h1>
      <div class="flex-1" />
      <!-- Date range -->
      <div class="flex items-center gap-2">
        <button
          v-for="p in presets"
          :key="p.label"
          class="rounded-full px-3 py-1 text-xs font-medium transition-colors"
          :class="activePreset === p.label
            ? 'bg-ink-gray-9 text-surface-white'
            : 'text-ink-gray-6 hover:bg-surface-gray-2'"
          @click="applyPreset(p)"
        >{{ p.label }}</button>
      </div>
    </div>

    <div v-if="analytics.loading && !analytics.data" class="flex flex-1 items-center justify-center py-20">
      <LoadingIndicator :scale="6" class="text-ink-gray-4" />
    </div>

    <div v-else-if="data" class="space-y-6 p-6">
      <!-- Summary cards -->
      <div class="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Total Messages" :value="data.summary.total" />
        <StatCard label="Conversations" :value="data.summary.conversations" />
        <StatCard label="Incoming" :value="data.summary.incoming" color="blue" />
        <StatCard label="Outgoing" :value="data.summary.outgoing" color="green" />
      </div>

      <!-- Daily chart -->
      <ChartCard title="Messages per Day">
        <DailyChart :rows="data.daily" />
      </ChartCard>

      <!-- Hourly chart + Incoming/Outgoing side by side -->
      <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard title="Messages by Hour of Day">
          <HourlyChart :rows="data.hourly" />
        </ChartCard>
        <ChartCard title="Incoming vs Outgoing">
          <DirectionChart :incoming="data.summary.incoming" :outgoing="data.summary.outgoing" />
        </ChartCard>
      </div>

      <!-- Top contacts -->
      <ChartCard title="Top Active Contacts & Groups">
        <div class="space-y-2 px-1 py-2">
          <div v-if="!data.top_contacts.length" class="py-6 text-center text-xs text-ink-gray-4">No data</div>
          <div
            v-for="(c, i) in data.top_contacts"
            :key="c.jid"
            class="flex items-center gap-3"
          >
            <span class="w-5 shrink-0 text-right text-[11px] text-ink-gray-4">{{ i + 1 }}</span>
            <div class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold"
              :class="c.is_group ? 'bg-purple-100 text-purple-700' : 'bg-surface-gray-3 text-ink-gray-7'">
              {{ c.is_group ? '#' : (c.display_name[0] || '?').toUpperCase() }}
            </div>
            <div class="min-w-0 flex-1">
              <div class="truncate text-xs font-medium text-ink-gray-8">{{ c.display_name }}</div>
              <div v-if="c.company" class="truncate text-[10px] text-ink-gray-4">{{ c.company }}</div>
            </div>
            <div class="flex items-center gap-3 text-[11px] text-ink-gray-5">
              <span class="text-blue-600">↓{{ c.incoming }}</span>
              <span class="text-green-600">↑{{ c.outgoing }}</span>
            </div>
            <div class="w-24 shrink-0">
              <div class="h-1.5 overflow-hidden rounded-full bg-surface-gray-3">
                <div
                  class="h-full rounded-full bg-ink-gray-7 transition-all"
                  :style="{ width: barWidth(c.total, maxContactTotal) + '%' }"
                />
              </div>
            </div>
            <span class="w-8 shrink-0 text-right text-xs font-semibold text-ink-gray-7">{{ c.total }}</span>
          </div>
        </div>
      </ChartCard>

      <!-- Agent response times -->
      <ChartCard title="Agent Response Times">
        <div v-if="!data.agent_stats.length" class="py-6 text-center text-xs text-ink-gray-4">
          No outgoing replies in this period
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead>
              <tr class="border-b border-outline-gray-2 text-left text-ink-gray-5">
                <th class="pb-2 pr-4 font-medium">Agent</th>
                <th class="pb-2 pr-4 text-right font-medium">Replies</th>
                <th class="pb-2 pr-4 text-right font-medium">Avg Response</th>
                <th class="pb-2 pr-4 text-right font-medium">
                  <span class="inline-block rounded bg-green-100 px-1.5 py-0.5 text-[10px] text-green-700">&lt;5 min</span>
                </th>
                <th class="pb-2 pr-4 text-right font-medium">
                  <span class="inline-block rounded bg-blue-100 px-1.5 py-0.5 text-[10px] text-blue-700">5–30 min</span>
                </th>
                <th class="pb-2 pr-4 text-right font-medium">
                  <span class="inline-block rounded bg-yellow-100 px-1.5 py-0.5 text-[10px] text-yellow-700">30–2 hr</span>
                </th>
                <th class="pb-2 text-right font-medium">
                  <span class="inline-block rounded bg-red-100 px-1.5 py-0.5 text-[10px] text-red-700">&gt;2 hr</span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="a in data.agent_stats"
                :key="a.agent_name"
                class="border-b border-outline-gray-2 last:border-0"
              >
                <td class="py-2 pr-4 font-medium text-ink-gray-8">{{ a.agent_name }}</td>
                <td class="py-2 pr-4 text-right text-ink-gray-7">{{ a.replies }}</td>
                <td class="py-2 pr-4 text-right text-ink-gray-6">{{ formatMinutes(a.avg_minutes) }}</td>
                <td class="py-2 pr-4 text-right">
                  <span v-if="a.lt5" class="font-semibold text-green-600">{{ a.lt5 }}</span>
                  <span v-else class="text-ink-gray-3">—</span>
                </td>
                <td class="py-2 pr-4 text-right">
                  <span v-if="a.lt30" class="font-semibold text-blue-600">{{ a.lt30 }}</span>
                  <span v-else class="text-ink-gray-3">—</span>
                </td>
                <td class="py-2 pr-4 text-right">
                  <span v-if="a.lt120" class="font-semibold text-yellow-600">{{ a.lt120 }}</span>
                  <span v-else class="text-ink-gray-3">—</span>
                </td>
                <td class="py-2 text-right">
                  <span v-if="a.gt120" class="font-semibold text-red-500">{{ a.gt120 }}</span>
                  <span v-else class="text-ink-gray-3">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </ChartCard>
    </div>
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator } from "frappe-ui";
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";

// ── Sub-components ────────────────────────────────────────────────────────────

const StatCard = {
  props: ["label", "value", "color"],
  template: `
    <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-4">
      <div class="mb-1 text-[11px] font-medium text-ink-gray-5">{{ label }}</div>
      <div class="text-2xl font-bold"
        :class="color === 'blue' ? 'text-blue-600' : color === 'green' ? 'text-green-600' : 'text-ink-gray-9'">
        {{ (value || 0).toLocaleString() }}
      </div>
    </div>
  `,
};

const ChartCard = {
  props: ["title"],
  template: `
    <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-5">
      <div class="mb-4 text-sm font-semibold text-ink-gray-8">{{ title }}</div>
      <slot />
    </div>
  `,
};

const DailyChart = {
  props: ["rows"],
  setup(props: { rows: any[] }) {
    const max = computed(() => Math.max(1, ...props.rows.map((r: any) => r.total)));
    return { max };
  },
  template: `
    <div v-if="!rows.length" class="py-6 text-center text-xs text-ink-gray-4">No data</div>
    <div v-else class="w-full overflow-x-auto">
      <div class="flex min-w-0 items-end gap-px" style="height:120px; min-width: max-content">
        <div
          v-for="r in rows"
          :key="r.date"
          class="group relative flex min-w-[18px] flex-1 flex-col items-center justify-end"
          style="height:120px"
        >
          <div
            class="w-full rounded-t bg-ink-gray-8 opacity-80 transition-opacity group-hover:opacity-100"
            :style="{ height: Math.max(2, Math.round((r.total / max) * 100)) + 'px' }"
          />
          <div class="absolute -top-5 hidden whitespace-nowrap rounded bg-ink-gray-9 px-1.5 py-0.5 text-[9px] text-surface-white group-hover:block">
            {{ r.date }}: {{ r.total }}
          </div>
        </div>
      </div>
      <div class="mt-1 flex items-center justify-between text-[9px] text-ink-gray-4">
        <span>{{ rows[0]?.date }}</span>
        <span>{{ rows[rows.length - 1]?.date }}</span>
      </div>
    </div>
  `,
};

const HourlyChart = {
  props: ["rows"],
  setup(props: { rows: any[] }) {
    const max = computed(() => Math.max(1, ...props.rows.map((r: any) => r.total)));
    const labels = ["12a","1","2","3","4","5","6","7","8","9","10","11","12p","1","2","3","4","5","6","7","8","9","10","11"];
    return { max, labels };
  },
  template: `
    <div class="w-full">
      <div class="flex items-end gap-px" style="height:80px">
        <div
          v-for="r in rows"
          :key="r.hour"
          class="group relative flex flex-1 flex-col items-center justify-end"
          style="height:80px"
        >
          <div
            class="w-full rounded-t transition-all"
            :class="r.hour >= 8 && r.hour <= 18 ? 'bg-green-500 opacity-80 group-hover:opacity-100' : 'bg-surface-gray-4 group-hover:bg-surface-gray-5'"
            :style="{ height: Math.max(2, Math.round((r.total / max) * 70)) + 'px' }"
          />
          <div v-if="r.total" class="absolute -top-5 hidden whitespace-nowrap rounded bg-ink-gray-9 px-1.5 py-0.5 text-[9px] text-surface-white group-hover:block">
            {{ r.hour }}:00 — {{ r.total }}
          </div>
        </div>
      </div>
      <div class="mt-1 flex items-center">
        <span v-for="(l, i) in labels" :key="i" class="flex-1 text-center text-[8px] text-ink-gray-4">
          {{ i % 3 === 0 ? l : '' }}
        </span>
      </div>
    </div>
  `,
};

const DirectionChart = {
  props: ["incoming", "outgoing"],
  setup(props: { incoming: number; outgoing: number }) {
    const total = computed(() => (props.incoming || 0) + (props.outgoing || 0));
    const inPct = computed(() => total.value ? Math.round(((props.incoming || 0) / total.value) * 100) : 50);
    const outPct = computed(() => 100 - inPct.value);
    return { total, inPct, outPct };
  },
  template: `
    <div class="flex flex-col items-center justify-center gap-4 py-4">
      <div class="flex h-4 w-full overflow-hidden rounded-full">
        <div class="bg-blue-500 transition-all" :style="{ width: inPct + '%' }" />
        <div class="bg-green-500 transition-all" :style="{ width: outPct + '%' }" />
      </div>
      <div class="flex gap-8">
        <div class="text-center">
          <div class="text-[10px] text-ink-gray-5"><span class="mr-1 inline-block h-2 w-2 rounded-full bg-blue-500" />Incoming</div>
          <div class="text-lg font-bold text-blue-600">{{ inPct }}%</div>
          <div class="text-[10px] text-ink-gray-4">{{ (incoming || 0).toLocaleString() }} msgs</div>
        </div>
        <div class="text-center">
          <div class="text-[10px] text-ink-gray-5"><span class="mr-1 inline-block h-2 w-2 rounded-full bg-green-500" />Outgoing</div>
          <div class="text-lg font-bold text-green-600">{{ outPct }}%</div>
          <div class="text-[10px] text-ink-gray-4">{{ (outgoing || 0).toLocaleString() }} msgs</div>
        </div>
      </div>
    </div>
  `,
};

// ── Page logic ────────────────────────────────────────────────────────────────

const router = useRouter();

const presets = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
];
const activePreset = ref("30d");

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}
function today(): string {
  return new Date().toISOString().slice(0, 10);
}

const fromDate = ref(daysAgo(30));
const toDate = ref(today());

function applyPreset(p: { label: string; days: number }) {
  activePreset.value = p.label;
  fromDate.value = daysAgo(p.days);
  toDate.value = today();
  analytics.submit({ from_date: fromDate.value, to_date: toDate.value });
}

const analytics = createResource({
  url: "helpdesk.integrations.baileys.get_baileys_analytics",
  auto: false,
});

analytics.submit({ from_date: fromDate.value, to_date: toDate.value });

const data = computed(() => analytics.data || null);

const maxContactTotal = computed(() => {
  const contacts = data.value?.top_contacts || [];
  return Math.max(1, ...contacts.map((c: any) => c.total));
});

function barWidth(val: number, max: number): number {
  return Math.round((val / max) * 100);
}

function formatMinutes(m: number): string {
  if (!m) return "—";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}
</script>
