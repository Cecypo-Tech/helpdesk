import { call, dayjs } from "frappe-ui"
import { onMounted, onUnmounted, ref } from "vue"

export interface DueTask {
	name: string
	title: string
	due_date: string
	due_time: string | null
	status: string
	ticket: string | null
}

const SNOOZE_PREFIX = "hd_task_snooze__"
const POLL_INTERVAL_MS = 5 * 60 * 1000

export function useTaskDueAlerts() {
	const dueTasks = ref<DueTask[]>([])
	const visible = ref(false)
	let timer: ReturnType<typeof setInterval> | null = null

	function snoozeKey(taskName: string): string {
		const user = (window as any).frappe?.session?.user ?? "user"
		return `${SNOOZE_PREFIX}${user}__${taskName}`
	}

	function isSnoozed(taskName: string): boolean {
		const val = localStorage.getItem(snoozeKey(taskName))
		if (!val) return false
		return dayjs().isBefore(dayjs(val))
	}

	function snooze(taskName: string, minutes: number | "tomorrow9am") {
		let until: string
		if (minutes === "tomorrow9am") {
			until = dayjs().add(1, "day").startOf("day").add(9, "hour").toISOString()
		} else {
			until = dayjs().add(minutes, "minute").toISOString()
		}
		localStorage.setItem(snoozeKey(taskName), until)
		dueTasks.value = dueTasks.value.filter((t) => t.name !== taskName)
		if (dueTasks.value.length === 0) visible.value = false
	}

	async function markDone(taskName: string) {
		await call("helpdesk.helpdesk.doctype.hd_task.hd_task.set_task_field", {
			task_name: taskName,
			fieldname: "status",
			value: "Done",
		})
		dueTasks.value = dueTasks.value.filter((t) => t.name !== taskName)
		if (dueTasks.value.length === 0) visible.value = false
	}

	async function check() {
		const tasks: DueTask[] = await call(
			"helpdesk.helpdesk.doctype.hd_task.hd_task.get_my_due_tasks"
		)
		const active = (tasks ?? []).filter((t) => !isSnoozed(t.name))
		dueTasks.value = active
		if (active.length > 0) visible.value = true
	}

	function dismiss() {
		visible.value = false
	}

	onMounted(() => {
		check()
		timer = setInterval(check, POLL_INTERVAL_MS)
	})

	onUnmounted(() => {
		if (timer) clearInterval(timer)
	})

	return { dueTasks, visible, snooze, markDone, dismiss }
}
