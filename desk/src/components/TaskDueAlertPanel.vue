<template>
  <Transition name="slide-up">
    <div
      v-if="visible && dueTasks.length"
      class="fixed bottom-4 right-4 z-50 w-72 rounded-xl shadow-2xl border border-outline-gray-2 overflow-hidden bg-surface-white"
    >
      <!-- Header -->
      <div class="flex items-center justify-between px-3 py-2.5 bg-ink-gray-9">
        <div class="flex items-center gap-2">
          <span class="text-sm">⏰</span>
          <span class="text-xs font-semibold text-surface-white">
            {{ dueTasks.length }} task{{ dueTasks.length > 1 ? "s" : "" }}
            need{{ dueTasks.length === 1 ? "s" : "" }} attention
          </span>
        </div>
        <button
          class="text-ink-gray-5 hover:text-surface-white transition-colors"
          @click="dismiss"
        >
          <LucideX class="h-3.5 w-3.5" />
        </button>
      </div>

      <!-- Task list -->
      <div class="max-h-80 overflow-y-auto divide-y divide-outline-gray-1">
        <div v-for="task in dueTasks" :key="task.name" class="px-3 py-2.5">
          <!-- Title -->
          <button
            class="text-xs font-semibold text-ink-gray-9 leading-snug mb-1 text-left hover:text-ink-blue-3 hover:underline transition-colors w-full"
            @click="goToTask(task.name)"
          >
            {{ task.title }}
          </button>
          <!-- Due label + ticket ref -->
          <div class="flex items-center gap-1.5 mb-2">
            <span
              class="h-1.5 w-1.5 rounded-full flex-shrink-0"
              :class="dueColor(task.due_date).dot"
            />
            <span class="text-[10px] font-medium" :class="dueColor(task.due_date).text">
              {{ dueLabel(task.due_date) }}
            </span>
            <span v-if="task.ticket" class="text-[10px] text-ink-gray-4">
              · #{{ task.ticket }}
            </span>
          </div>
          <!-- Snooze + Done buttons -->
          <div class="flex gap-1.5 flex-wrap">
            <button
              v-for="opt in snoozeOptions"
              :key="opt.label"
              class="px-2 py-1 text-[10px] font-medium rounded border border-outline-gray-2 bg-surface-gray-1 text-ink-gray-6 hover:bg-surface-gray-2 transition-colors"
              @click="snooze(task.name, opt.value)"
            >
              {{ opt.label }}
            </button>
            <button
              class="px-2 py-1 text-[10px] font-medium rounded border border-green-200 bg-green-50 text-green-700 hover:bg-green-100 transition-colors"
              @click="markDone(task.name)"
            >
              ✓ Done
            </button>
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { useTaskDueAlerts } from "@/composables/useTaskDueAlerts"
import { views } from "@/composables/useView"
import { dayjs } from "frappe-ui"
import { useRouter, useRoute } from "vue-router"
import LucideX from "~icons/lucide/x"

const router = useRouter()
const route = useRoute()
const { dueTasks, visible, snooze, markDone, dismiss } = useTaskDueAlerts()

function goToTask(taskName: string) {
  const kanbanView = (views.data as any[] | undefined)?.find(
    (v) => v.type === "kanban" && v.dt === "HD Task"
  )
  const query: Record<string, string> = { openTask: taskName }
  if (kanbanView) {
    query.view = kanbanView.name
  } else if (route.query.view) {
    query.view = route.query.view as string
  }
  router.push({ name: "TasksAgent", query })
  dismiss()
}

const snoozeOptions: Array<{ label: string; value: number | "tomorrow9am" }> = [
	{ label: "15 min",   value: 15 },
	{ label: "1 hr",     value: 60 },
	{ label: "Tmrw 9am", value: "tomorrow9am" },
]

function dueColor(due_date: string): { dot: string; text: string } {
	const today = dayjs().startOf("day")
	const due = dayjs(due_date).startOf("day")
	if (due.isBefore(today)) return { dot: "bg-red-500",   text: "text-red-500" }
	return                          { dot: "bg-amber-400", text: "text-amber-500" }
}

function dueLabel(due_date: string): string {
	const today = dayjs().startOf("day")
	const due = dayjs(due_date).startOf("day")
	const diff = due.diff(today, "day")
	if (diff === 0)  return "Due today"
	if (diff === -1) return "Overdue by 1 day"
	return `Overdue by ${Math.abs(diff)} days`
}
</script>

<style scoped>
.slide-up-enter-active,
.slide-up-leave-active {
  transition: transform 0.25s ease, opacity 0.25s ease;
}
.slide-up-enter-from,
.slide-up-leave-to {
  transform: translateY(12px);
  opacity: 0;
}
</style>
