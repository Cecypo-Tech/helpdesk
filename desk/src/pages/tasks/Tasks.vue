<template>
  <div>
    <LayoutHeader>
      <template #left-header>
        <ViewBreadcrumbs
          :label="__('Tasks')"
          route-name="TasksAgent"
          :options="dropdownOptions"
          :dropdown-actions="viewActions"
          :current-view="currentView"
        />
      </template>
      <template #right-header>
        <RouterLink :to="{ name: 'TaskAgentNew' }">
          <Button :label="__('New Task')" theme="gray" variant="solid">
            <template #prefix>
              <LucidePlus class="h-4 w-4" />
            </template>
          </Button>
        </RouterLink>
      </template>
    </LayoutHeader>
    <CalendarView
      v-if="isCalendarView"
    />
    <KanbanView
      v-else-if="isKanbanView"
    />
    <template v-else>
      <div class="flex items-center gap-2 px-4 py-2 border-b border-outline-gray-1 bg-surface-white">
        <button
          class="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded border transition-colors"
          :class="quickFilter === 'overdue'
            ? 'bg-red-50 border-red-300 text-red-600 font-semibold'
            : 'bg-surface-white border-outline-gray-2 text-ink-gray-6 hover:border-outline-gray-4'"
          @click="quickFilter = quickFilter === 'overdue' ? null : 'overdue'"
        >
          <LucideAlertCircle class="h-3 w-3" />
          {{ __('Overdue') }}
        </button>
        <button
          class="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded border transition-colors"
          :class="quickFilter === 'due-today'
            ? 'bg-amber-50 border-amber-300 text-amber-600 font-semibold'
            : 'bg-surface-white border-outline-gray-2 text-ink-gray-6 hover:border-outline-gray-4'"
          @click="quickFilter = quickFilter === 'due-today' ? null : 'due-today'"
        >
          <LucideCalendarClock class="h-3 w-3" />
          {{ __('Due Today') }}
        </button>
      </div>
      <ListViewBuilder
        ref="listViewRef"
        :options="options"
        @empty-state-action="() => $router.push({ name: 'TaskAgentNew' })"
        @row-click="
          (row) => $router.push({ name: 'TaskAgent', params: { taskId: row } })
        "
      />
    </template>
    <ViewModal
      v-if="viewDialog.show"
      v-model="viewDialog"
      @update="(view, action) => handleView(view, action)"
    />
  </div>
</template>

<script setup lang="ts">
import { LayoutHeader, ListViewBuilder } from "@/components";
import CalendarView from "@/pages/tasks/CalendarView.vue";
import KanbanView from "@/pages/tasks/KanbanView.vue";
import {
  EditIcon,
  PinIcon,
  UnpinIcon,
} from "@/components/icons";
import ViewBreadcrumbs from "@/components/ViewBreadcrumbs.vue";
import ViewModal from "@/components/ViewModal.vue";
import { currentView, useView } from "@/composables/useView";
import { useAuthStore } from "@/stores/auth";
import { globalStore } from "@/stores/globalStore";
import { __ } from "@/translation";
import { View } from "@/types";
import { getIcon } from "@/utils";
import { Badge, FeatherIcon, toast, usePageMeta } from "frappe-ui";
import LucideAlignJustify from "~icons/lucide/align-justify";
import LucideCalendarDays from "~icons/lucide/calendar-days";
import LucidePlus from "~icons/lucide/plus";
import LucideAlertCircle from "~icons/lucide/alert-circle";
import LucideCalendarClock from "~icons/lucide/calendar-clock";
import { computed, h, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

const router = useRouter();
const route = useRoute();
const { isManager } = useAuthStore();
const { $dialog } = globalStore();

const listViewRef = ref(null);

const quickFilter = ref<"overdue" | "due-today" | null>(null);

watch(quickFilter, () => {
	if (!listViewRef.value?.list) return;
	const today = new Date().toISOString().slice(0, 10);
	const filters =
		quickFilter.value === "overdue"
			? [["due_date", "<", today], ["status", "!=", "Done"]]
			: quickFilter.value === "due-today"
			? [["due_date", "=", today]]
			: [];
	listViewRef.value.list.params.filters = filters;
	listViewRef.value.list.reload();
});

const {
  getCurrentUserViews,
  createView,
  publicViews,
  pinnedViews,
  findView,
  updateView,
  deleteView,
} = useView("HD Task");

const isCalendarView = computed(() => route.query.view === "calendar");

const isKanbanView = computed(() => {
  const viewName = route.query.view as string | undefined;
  if (!viewName || viewName === "calendar") return false;
  return findView(viewName).value?.type === "kanban";
});

const statusColorMap: Record<string, string> = {
  Backlog: "gray",
  Todo: "blue",
  "In Progress": "orange",
  Done: "green",
};

const options = {
  doctype: "HD Task",
  columnConfig: {
    status: {
      custom: ({ item }: { item: string }) =>
        h(Badge, {
          label: item,
          theme: statusColorMap[item] ?? "gray",
          variant: "subtle",
        }),
    },
  },
  selectable: true,
  showSelectBanner: true,
  emptyState: {
    title: __("No Tasks Found"),
  },
  rowRoute: {
    name: "TaskAgent",
    prop: "taskId",
  },
  hideColumnSetting: false,
};

let viewDialog = reactive({
  show: false,
  view: { label: "", icon: "", name: "" },
  mode: "create",
});

let selectedView: View | null = null;

const dropdownOptions = computed(() => {
  const items: any[] = [
    {
      group: __("Default Views"),
      items: [
        {
          label: __("List View"),
          icon: "align-justify",
          onClick: () => router.push({ name: "TasksAgent" }),
        },
        {
          label: __("Calendar View"),
          icon: h(LucideCalendarDays, { class: "h-4 w-4" }),
          onClick: () => {
            currentView.value = { label: __("Calendar"), icon: LucideCalendarDays };
            router.push({ name: "TasksAgent", query: { view: "calendar" } });
          },
        },
      ],
    },
  ];

  if (getCurrentUserViews.value?.length !== 0) {
    items.push({
      group: __("Saved Views"),
      items: parseViews(getCurrentUserViews.value),
    });
  }
  if (pinnedViews.value?.length !== 0) {
    items.push({
      group: __("Private Views"),
      items: parseViews(pinnedViews.value),
    });
  }
  if (publicViews.value?.length !== 0) {
    items.push({
      group: __("Public Views"),
      items: parseViews(publicViews.value),
    });
  }

  items.push({
    group: __("Create View"),
    hideLabel: true,
    items: [
      {
        label: __("Create View"),
        icon: "plus",
        onClick: () => {
          resetState();
          viewDialog.show = true;
        },
      },
    ],
  });

  return items;
});

const viewActions = (view: any) => {
  const _view = findView(view.name).value;

  const actions: any[] = [
    {
      group: __("Default Views"),
      hideLabel: true,
      items: [
        {
          label: __("Duplicate"),
          icon: h(FeatherIcon, { name: "copy" }),
          onClick: () => {
            viewDialog.view.label = _view.label + " (New)";
            viewDialog.view.icon = _view.icon;
            viewDialog.view.name = _view.name;
            viewDialog.mode = "duplicate";
            selectedView = _view;
            viewDialog.show = true;
          },
        },
      ],
    },
  ];

  if (!_view.public || isManager) {
    actions[0].items.push({
      label: __("Edit"),
      icon: h(EditIcon, { class: "h-4 w-4" }),
      onClick: () => {
        viewDialog.view.label = _view.label;
        viewDialog.view.icon = _view.icon;
        viewDialog.view.name = _view.name;
        viewDialog.mode = "edit";
        viewDialog.show = true;
      },
    });
    if (!_view.public) {
      actions[0].items.push({
        label: _view?.pinned ? __("Unpin View") : __("Pin View"),
        icon: h(_view?.pinned ? UnpinIcon : PinIcon, { class: "h-4 w-4" }),
        onClick: () => {
          updateView({ name: _view.name, pinned: !_view.pinned });
        },
      });
    }
    if (isManager) {
      actions[0].items.push({
        label: _view?.public ? __("Make Private") : __("Make Public"),
        icon: h(FeatherIcon, {
          name: _view?.public ? "lock" : "unlock",
          class: "h-4 w-4",
        }),
        onClick: () => {
          const newView = { name: _view.name, public: !_view.public };
          if (_view.public) {
            $dialog({
              title: __("Make {0} private?", [_view.label]),
              message: __(
                "This view is currently public. Changing it to private will hide it for all the users."
              ),
              actions: [
                {
                  label: __("Confirm"),
                  variant: "solid",
                  onClick({ close }: { close: () => void }) {
                    close();
                    updateView(newView);
                  },
                },
              ],
            });
          } else {
            updateView(newView);
          }
        },
      });
    }
    actions.push({
      group: __("Delete View"),
      hideLabel: true,
      items: [
        {
          label: __("Delete"),
          icon: "trash-2",
          onClick: () => {
            $dialog({
              title: __("Delete {0}?", [_view.label]),
              message:
                __("Are you sure you want to delete this view?") +
                (_view.public
                  ? " " +
                    __(
                      "This view is public, and will be removed for all users."
                    )
                  : ""),
              actions: [
                {
                  label: __("Confirm"),
                  variant: "solid",
                  onClick({ close }: { close: () => void }) {
                    if (route.query.view === _view.name) {
                      router.push({ name: "TasksAgent" });
                    }
                    deleteView(_view.name);
                    handleSuccess(__("deleted"));
                    close();
                  },
                },
              ],
            });
          },
        },
      ],
    });
  }

  return actions;
};

function parseViews(views: View[]) {
  return views?.map((view) => ({
    ...view,
    onClick: () => {
      currentView.value = { label: view.label, icon: view.icon };
      router.push({ name: view.route_name, query: { view: view.name } });
    },
  }));
}

function handleView(viewInfo: any, action: string) {
  let view: View;
  if (action === "update") {
    updateView(viewInfo);
    handleSuccess(__("updated"));
    currentView.value = { label: viewInfo.label, icon: getIcon(viewInfo.icon) };
    return;
  } else if (action === "duplicate") {
    view = {
      ...selectedView,
      filters: JSON.stringify(selectedView!.filters),
      columns: JSON.stringify(selectedView!.columns),
      rows: JSON.stringify(selectedView!.rows),
      label: viewInfo.label,
      icon: viewInfo.icon,
      public: false,
      pinned: false,
    };
  } else {
    view = {
      dt: "HD Task",
      type: "list",
      label: viewInfo.label ?? __("List"),
      icon: viewInfo.icon ?? "",
      route_name: router.currentRoute.value.name as string,
      order_by: listViewRef.value?.list?.params.order_by,
      filters: JSON.stringify(listViewRef.value?.list?.params.filters),
      columns: JSON.stringify(listViewRef.value?.list?.data.columns),
      rows: JSON.stringify(listViewRef.value?.list?.data?.rows),
    };
  }

  createView(view, (d: any) => {
    currentView.value = { label: d.label || __("List"), icon: getIcon(d.icon) };
    router.push({ name: "TasksAgent", query: { view: d.name } });
    handleSuccess();
  });
}

function handleSuccess(msg = __("created")) {
  toast.success(__("View {0}", [msg]));
  resetState();
}

function resetState() {
  viewDialog.show = false;
  viewDialog.view.label = "";
  viewDialog.view.icon = "";
  viewDialog.view.name = "";
  viewDialog.mode = null;
  selectedView = null;
}

onMounted(() => {
  if (!route.query.view) {
    currentView.value = { label: __("List"), icon: LucideAlignJustify };
  } else if (route.query.view === "calendar") {
    currentView.value = { label: __("Calendar"), icon: LucideCalendarDays };
  }
});

usePageMeta(() => ({ title: __("Tasks") }));
</script>
