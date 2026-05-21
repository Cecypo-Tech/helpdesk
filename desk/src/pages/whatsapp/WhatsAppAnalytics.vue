<template>
  <div class="flex min-h-0 flex-1 flex-col overflow-y-auto bg-surface-gray-1">
    <!-- Header -->
    <div class="flex items-center gap-3 border-b border-outline-gray-2 bg-surface-white px-6 py-3">
      <button
        class="flex h-7 w-7 items-center justify-center rounded-full text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8"
        title="Back to chats"
        @click="router.push('/whatsapp')"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>
      <WhatsAppIcon class="h-4 w-4 text-green-600" />
      <h1 class="text-sm font-semibold text-ink-gray-9">WhatsApp Analytics</h1>
      <div class="flex-1" />
      <div class="flex items-center gap-1">
        <button
          v-for="p in presets"
          :key="p.label"
          class="rounded-full px-3 py-1 text-xs font-medium transition-colors"
          :class="activePreset === p.label ? 'text-surface-white' : 'text-ink-gray-6 hover:bg-surface-gray-2'"
          :style="activePreset === p.label ? { backgroundColor: 'var(--ink-gray-9)' } : {}"
          @click="applyPreset(p)"
        >{{ p.label }}</button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="analytics.loading && !analytics.data" class="flex flex-1 items-center justify-center py-20">
      <LoadingIndicator :scale="6" class="text-ink-gray-4" />
    </div>

    <!-- Error -->
    <div v-else-if="analytics.error" class="p-6 text-sm text-red-500">
      Failed to load analytics. {{ analytics.error }}
    </div>

    <!-- Data -->
    <div v-else-if="data" class="space-y-6 p-6">

      <!-- Summary cards -->
      <div class="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-4">
          <div class="mb-1 text-[11px] font-medium text-ink-gray-5">Total Messages</div>
          <div class="text-2xl font-bold text-ink-gray-9">{{ (data.summary.total || 0).toLocaleString() }}</div>
        </div>
        <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-4">
          <div class="mb-1 text-[11px] font-medium text-ink-gray-5">Conversations</div>
          <div class="text-2xl font-bold text-ink-gray-9">{{ (data.summary.conversations || 0).toLocaleString() }}</div>
        </div>
        <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-4">
          <div class="mb-1 text-[11px] font-medium text-ink-gray-5">Incoming</div>
          <div class="text-2xl font-bold text-blue-600">{{ (data.summary.incoming || 0).toLocaleString() }}</div>
        </div>
        <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-4">
          <div class="mb-1 text-[11px] font-medium text-ink-gray-5">Outgoing</div>
          <div class="text-2xl font-bold text-green-600">{{ (data.summary.outgoing || 0).toLocaleString() }}</div>
        </div>
      </div>

      <!-- Messages per day -->
      <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-5">
        <div class="mb-4 text-sm font-semibold text-ink-gray-8">Messages per Day</div>
        <div v-if="!data.daily.length" class="py-6 text-center text-xs text-ink-gray-4">No data</div>
        <div v-else>
          <div class="flex items-end gap-px overflow-x-auto" style="height:120px">
            <div
              v-for="r in data.daily"
              :key="r.date"
              class="group relative flex min-w-[16px] flex-1 flex-col items-center justify-end"
              style="height:120px"
            >
              <div
                class="w-full rounded-t opacity-70 transition-opacity group-hover:opacity-100"
                :style="{ height: Math.max(2, Math.round((r.total / dailyMax) * 110)) + 'px', backgroundColor: 'var(--ink-gray-8)' }"
              />
              <div class="absolute -top-6 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded px-1.5 py-0.5 text-[9px] text-surface-white group-hover:block z-10" style="background-color: var(--ink-gray-9)">
                {{ r.date }}: {{ r.total }}
              </div>
            </div>
          </div>
          <div class="mt-1 flex items-center justify-between text-[9px] text-ink-gray-4">
            <span>{{ data.daily[0]?.date }}</span>
            <span>{{ data.daily[data.daily.length - 1]?.date }}</span>
          </div>
        </div>
      </div>

      <!-- Hourly + Direction -->
      <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <!-- Hourly chart -->
        <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-5">
          <div class="mb-4 text-sm font-semibold text-ink-gray-8">Messages by Hour of Day</div>
          <div class="flex items-end gap-px" style="height:80px">
            <div
              v-for="r in data.hourly"
              :key="r.hour"
              class="group relative flex flex-1 flex-col items-center justify-end"
              style="height:80px"
            >
              <div
                class="w-full rounded-t transition-all"
                :class="r.hour >= 8 && r.hour <= 18 ? 'bg-green-500 opacity-80 group-hover:opacity-100' : 'bg-surface-gray-4 group-hover:bg-surface-gray-5'"
                :style="{ height: Math.max(2, Math.round((r.total / hourlyMax) * 70)) + 'px' }"
              />
              <div v-if="r.total" class="absolute -top-6 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded px-1.5 py-0.5 text-[9px] text-surface-white group-hover:block z-10" style="background-color: var(--ink-gray-9)">
                {{ r.hour }}:00 — {{ r.total }}
              </div>
            </div>
          </div>
          <div class="mt-1 flex">
            <span v-for="h in 24" :key="h" class="flex-1 text-center text-[8px] text-ink-gray-4">
              {{ (h - 1) % 6 === 0 ? hourLabel(h - 1) : '' }}
            </span>
          </div>
        </div>

        <!-- Incoming vs outgoing -->
        <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-5">
          <div class="mb-4 text-sm font-semibold text-ink-gray-8">Incoming vs Outgoing</div>
          <div class="flex flex-col items-center justify-center gap-5 py-4">
            <div class="flex h-4 w-full overflow-hidden rounded-full">
              <div class="bg-blue-500 transition-all" :style="{ width: inPct + '%' }" />
              <div class="bg-green-500 transition-all" :style="{ width: outPct + '%' }" />
            </div>
            <div class="flex gap-10">
              <div class="text-center">
                <div class="mb-1 flex items-center gap-1 text-[10px] text-ink-gray-5">
                  <span class="inline-block h-2 w-2 rounded-full bg-blue-500" />Incoming
                </div>
                <div class="text-xl font-bold text-blue-600">{{ inPct }}%</div>
                <div class="text-[10px] text-ink-gray-4">{{ (data.summary.incoming || 0).toLocaleString() }} msgs</div>
              </div>
              <div class="text-center">
                <div class="mb-1 flex items-center gap-1 text-[10px] text-ink-gray-5">
                  <span class="inline-block h-2 w-2 rounded-full bg-green-500" />Outgoing
                </div>
                <div class="text-xl font-bold text-green-600">{{ outPct }}%</div>
                <div class="text-[10px] text-ink-gray-4">{{ (data.summary.outgoing || 0).toLocaleString() }} msgs</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Top contacts -->
      <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-5">
        <div class="mb-4 text-sm font-semibold text-ink-gray-8">Top Active Contacts &amp; Groups</div>
        <div v-if="!data.top_contacts.length" class="py-6 text-center text-xs text-ink-gray-4">No data</div>
        <div v-else class="space-y-2">
          <div v-for="(c, i) in data.top_contacts" :key="c.jid" class="flex items-center gap-3">
            <span class="w-5 shrink-0 text-right text-[11px] text-ink-gray-4">{{ i + 1 }}</span>
            <div
              class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold"
              :class="c.is_group ? 'bg-purple-100 text-purple-700' : 'bg-surface-gray-3 text-ink-gray-7'"
            >
              {{ c.is_group ? '#' : (c.display_name[0] || '?').toUpperCase() }}
            </div>
            <div class="min-w-0 flex-1">
              <div class="truncate text-xs font-medium text-ink-gray-8">{{ c.display_name }}</div>
              <div v-if="c.company" class="truncate text-[10px] text-ink-gray-4">{{ c.company }}</div>
            </div>
            <div class="flex items-center gap-3 text-[11px]">
              <span class="text-blue-600">↓{{ c.incoming }}</span>
              <span class="text-green-600">↑{{ c.outgoing }}</span>
            </div>
            <div class="w-24 shrink-0">
              <div class="h-1.5 overflow-hidden rounded-full bg-surface-gray-3">
                <div
                  class="h-full rounded-full" style="background-color: var(--ink-gray-7)"
                  :style="{ width: Math.round((c.total / maxContactTotal) * 100) + '%' }"
                />
              </div>
            </div>
            <span class="w-8 shrink-0 text-right text-xs font-semibold text-ink-gray-7">{{ c.total }}</span>
          </div>
        </div>
      </div>

      <!-- Agent response times -->
      <div class="rounded-lg border border-outline-gray-2 bg-surface-white p-5">
        <div class="mb-4 text-sm font-semibold text-ink-gray-8">Agent Response Times</div>
        <div v-if="!data.agent_stats.length" class="py-6 text-center text-xs text-ink-gray-4">
          No outgoing replies found in this period
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead>
              <tr class="border-b border-outline-gray-2 text-left">
                <th class="pb-2 pr-4 font-medium text-ink-gray-5">Agent</th>
                <th class="pb-2 pr-4 text-right font-medium text-ink-gray-5">Replies</th>
                <th class="pb-2 pr-4 text-right font-medium text-ink-gray-5">Avg</th>
                <th class="pb-2 pr-4 text-right font-medium">
                  <span class="rounded bg-green-100 px-1.5 py-0.5 text-[10px] text-green-700">&lt;5 min</span>
                </th>
                <th class="pb-2 pr-4 text-right font-medium">
                  <span class="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] text-blue-700">5–30 min</span>
                </th>
                <th class="pb-2 pr-4 text-right font-medium">
                  <span class="rounded bg-yellow-100 px-1.5 py-0.5 text-[10px] text-yellow-700">30 min–2 hr</span>
                </th>
                <th class="pb-2 text-right font-medium">
                  <span class="rounded bg-red-100 px-1.5 py-0.5 text-[10px] text-red-700">&gt;2 hr</span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="a in data.agent_stats"
                :key="a.agent_name"
                class="border-b border-outline-gray-2 last:border-0"
              >
                <td class="py-2.5 pr-4 font-medium text-ink-gray-8">{{ a.agent_name }}</td>
                <td class="py-2.5 pr-4 text-right text-ink-gray-7">{{ a.replies }}</td>
                <td class="py-2.5 pr-4 text-right text-ink-gray-6">{{ formatMinutes(a.avg_minutes) }}</td>
                <td class="py-2.5 pr-4 text-right">
                  <span v-if="a.lt5" class="font-semibold text-green-600">{{ a.lt5 }}</span>
                  <span v-else class="text-ink-gray-3">—</span>
                </td>
                <td class="py-2.5 pr-4 text-right">
                  <span v-if="a.lt30" class="font-semibold text-blue-600">{{ a.lt30 }}</span>
                  <span v-else class="text-ink-gray-3">—</span>
                </td>
                <td class="py-2.5 pr-4 text-right">
                  <span v-if="a.lt120" class="font-semibold text-yellow-600">{{ a.lt120 }}</span>
                  <span v-else class="text-ink-gray-3">—</span>
                </td>
                <td class="py-2.5 text-right">
                  <span v-if="a.gt120" class="font-semibold text-red-500">{{ a.gt120 }}</span>
                  <span v-else class="text-ink-gray-3">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { createResource, LoadingIndicator } from "frappe-ui";
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import WhatsAppIcon from "@/components/icons/WhatsAppIcon.vue";

const router = useRouter();

// ── Date presets ──────────────────────────────────────────────────────────────

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
function todayStr(): string {
  return new Date().toISOString().slice(0, 10);
}

const fromDate = ref(daysAgo(30));
const toDate = ref(todayStr());

function applyPreset(p: { label: string; days: number }) {
  activePreset.value = p.label;
  fromDate.value = daysAgo(p.days);
  toDate.value = todayStr();
  analytics.submit({ from_date: fromDate.value, to_date: toDate.value });
}

// ── Data ──────────────────────────────────────────────────────────────────────

const analytics = createResource({
  url: "helpdesk.integrations.baileys.get_baileys_analytics",
  auto: false,
});

analytics.submit({ from_date: fromDate.value, to_date: toDate.value });

const data = computed(() => analytics.data || null);

// ── Computed helpers ──────────────────────────────────────────────────────────

const dailyMax = computed(() =>
  Math.max(1, ...(data.value?.daily || []).map((r: any) => r.total))
);

const hourlyMax = computed(() =>
  Math.max(1, ...(data.value?.hourly || []).map((r: any) => r.total))
);

const maxContactTotal = computed(() =>
  Math.max(1, ...(data.value?.top_contacts || []).map((c: any) => c.total))
);

const totalMessages = computed(() => {
  const s = data.value?.summary;
  return (s?.incoming || 0) + (s?.outgoing || 0);
});

const inPct = computed(() =>
  totalMessages.value ? Math.round(((data.value?.summary.incoming || 0) / totalMessages.value) * 100) : 50
);
const outPct = computed(() => 100 - inPct.value);

// ── Formatting ────────────────────────────────────────────────────────────────

function hourLabel(h: number): string {
  if (h === 0) return "12a";
  if (h === 12) return "12p";
  return h < 12 ? `${h}a` : `${h - 12}p`;
}

function formatMinutes(m: number): string {
  if (!m) return "—";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}
</script>
