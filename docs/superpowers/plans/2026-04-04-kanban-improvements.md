# Kanban Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the HD Task kanban board with better card readability, tag support, filtering (search/assignee/tag), task history via track_changes, and panel enhancements (copy button, created-by footer).

**Architecture:** Option A (incremental). Tags use Frappe's `_user_tags` column (comma-separated string on the document row). Filtering is client-side for assignee/tag, server-side SQL for text search. History uses `track_changes: 1` on the DocType. All changes are surgical to 6 files + 1 new component.

**Tech Stack:** Python (Frappe), Vue 3 Composition API, frappe-ui (Autocomplete, call, createListResource, createDocumentResource), Tailwind via frappe-ui semantic tokens, TypeScript.

**Bench root:** `/home/frappeuser/bench16` — all `bench` commands run from there. Default site: `site16.local`.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `helpdesk/helpdesk/doctype/hd_task/hd_task.json` | Modify | Add `track_changes: 1` |
| `helpdesk/helpdesk/doctype/hd_task/hd_task.py` | Modify | Add `_user_tags` to ALLOWED_FIELDS; add `get_all_task_tags()`, `search_tasks()` |
| `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py` | Modify | Tests for new backend functions |
| `desk/src/components/TagInput.vue` | Create | Reusable tag chip-input with autocomplete dropdown |
| `desk/src/pages/tasks/KanbanView.vue` | Modify | Filter bar + redesigned cards (avatar, priority bars, relative dates, tag pills) |
| `desk/src/pages/tasks/KanbanTaskPanel.vue` | Modify | Copy button, tags field, created-by footer |
| `desk/src/pages/tasks/TaskDetail.vue` | Modify | Tags field, created-by footer |

---

## Task 1: DocType — enable track_changes

**Files:**
- Modify: `helpdesk/helpdesk/doctype/hd_task/hd_task.json`

- [ ] **Step 1: Add `track_changes` to the DocType JSON**

Open `helpdesk/helpdesk/doctype/hd_task/hd_task.json`. Find the top-level `"index_web_pages_for_search": 1` line and add `"track_changes": 1` alongside it:

```json
 "index_web_pages_for_search": 1,
 "track_changes": 1,
```

The surrounding context (around line 88-95 of the file) should look like:
```json
 "index_web_pages_for_search": 1,
 "track_changes": 1,
 "links": [],
 "modified": "2026-03-23 00:00:00",
```

- [ ] **Step 2: Run migration**

```bash
cd /home/frappeuser/bench16
bench --site site16.local migrate
```

Expected: migration completes without errors. `tabHD Task` now has a `_version` tracking entry in `tabVersion` after any save.

- [ ] **Step 3: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/helpdesk/doctype/hd_task/hd_task.json
git commit -m "feat: enable track_changes on HD Task DocType"
```

---

## Task 2: Backend — _user_tags support + tag/search APIs

**Files:**
- Modify: `helpdesk/helpdesk/doctype/hd_task/hd_task.py`
- Modify: `helpdesk/helpdesk/doctype/hd_task/test_hd_task.py`

- [ ] **Step 1: Write failing tests**

Replace the content of `test_hd_task.py` with:

```python
import frappe
from frappe.tests.utils import FrappeTestCase


class TestHDTask(FrappeTestCase):
	def setUp(self):
		# Create test tasks
		self.task1 = frappe.get_doc({
			"doctype": "HD Task",
			"title": "Alpha task with special content",
			"status": "Backlog",
			"description": "This description mentions unicorn",
		}).insert(ignore_permissions=True)

		self.task2 = frappe.get_doc({
			"doctype": "HD Task",
			"title": "Beta task",
			"status": "Todo",
		}).insert(ignore_permissions=True)

		# Set tags directly via db to avoid doc.save() overhead in setUp
		frappe.db.set_value("HD Task", self.task1.name, "_user_tags", "frontend,bug")
		frappe.db.set_value("HD Task", self.task2.name, "_user_tags", "backend,bug")

	def tearDown(self):
		frappe.delete_doc("HD Task", self.task1.name, ignore_permissions=True, force=True)
		frappe.delete_doc("HD Task", self.task2.name, ignore_permissions=True, force=True)

	def test_create_task(self):
		task = frappe.get_doc({
			"doctype": "HD Task",
			"title": "Test Task",
			"status": "Backlog",
		})
		task.insert(ignore_permissions=True)
		self.assertEqual(task.status, "Backlog")
		task.delete(ignore_permissions=True)

	def test_get_all_task_tags(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import get_all_task_tags
		tags = get_all_task_tags()
		self.assertIn("frontend", tags)
		self.assertIn("bug", tags)
		self.assertIn("backend", tags)
		# Should be sorted and unique
		self.assertEqual(tags, sorted(set(tags)))

	def test_search_tasks_by_title(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import search_tasks
		results = search_tasks("Alpha task")
		self.assertIn(self.task1.name, results)
		self.assertNotIn(self.task2.name, results)

	def test_search_tasks_by_description(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import search_tasks
		results = search_tasks("unicorn")
		self.assertIn(self.task1.name, results)
		self.assertNotIn(self.task2.name, results)

	def test_search_tasks_by_subtask(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import search_tasks, save_task_subtasks
		# Add a subtask to task2 with unique text
		save_task_subtasks(
			self.task2.name,
			[{"title": "Deploy the phoenix service", "status": "Backlog", "due_date": None}]
		)
		results = search_tasks("phoenix")
		self.assertIn(self.task2.name, results)
		self.assertNotIn(self.task1.name, results)

	def test_set_task_field_user_tags(self):
		from helpdesk.helpdesk.doctype.hd_task.hd_task import set_task_field
		set_task_field(self.task1.name, "_user_tags", "newtag,anothertag")
		saved = frappe.db.get_value("HD Task", self.task1.name, "_user_tags")
		self.assertEqual(saved, "newtag,anothertag")
```

- [ ] **Step 2: Run tests — expect failures**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk --module helpdesk.helpdesk.doctype.hd_task.test_hd_task
```

Expected: `test_get_all_task_tags`, `test_search_tasks_by_title`, `test_search_tasks_by_description`, `test_search_tasks_by_subtask` FAIL with `ImportError` or `AttributeError`. `test_set_task_field_user_tags` FAIL because `_user_tags` not in ALLOWED_FIELDS.

- [ ] **Step 3: Implement backend changes**

Replace the full content of `helpdesk/helpdesk/doctype/hd_task/hd_task.py` with:

```python
import frappe
from frappe.model.document import Document

ALLOWED_FIELDS = {
	"title", "status", "priority", "assigned_to",
	"due_date", "ticket", "team", "description", "_user_tags",
}


class HDTask(Document):
	def validate(self):
		if self.assigned_to == "@me":
			self.assigned_to = frappe.session.user

	@staticmethod
	def default_list_data():
		columns = [
			{"label": "Title", "type": "Data", "key": "title", "width": "20rem"},
			{"label": "Status", "type": "Select", "key": "status", "width": "9rem"},
			{"label": "Priority", "type": "Link", "key": "priority", "width": "8rem"},
			{"label": "Assigned To", "type": "Link", "key": "assigned_to", "width": "10rem"},
			{"label": "Due Date", "type": "Date", "key": "due_date", "width": "8rem"},
			{"label": "Ticket", "type": "Link", "key": "ticket", "width": "8rem"},
		]
		rows = ["title", "status", "priority", "assigned_to", "due_date", "ticket"]
		return {"columns": columns, "rows": rows}


@frappe.whitelist()
def set_task_field(task_name: str, fieldname: str, value=None):
	"""Set a single field on HD Task using a direct DB write to avoid timestamp conflicts.

	Using frappe.db.set_value skips check_if_latest entirely, which is safe for
	simple field updates where the last-write-wins semantic is acceptable.
	"""
	if fieldname not in ALLOWED_FIELDS:
		frappe.throw(frappe._("Field {0} cannot be updated via this endpoint").format(fieldname))

	frappe.has_permission("HD Task", doc=task_name, ptype="write", throw=True)
	if value == "@me":
		value = frappe.session.user
	frappe.db.set_value("HD Task", task_name, fieldname, value or None)
	frappe.clear_document_cache("HD Task", task_name)
	modified = frappe.db.get_value("HD Task", task_name, "modified")
	return {"modified": str(modified)}


@frappe.whitelist()
def save_task(task_name: str, fields: dict | str, subtasks: list | str = "[]"):
	"""Save all fields and subtasks for an HD Task in one call.

	Loads fresh from DB so the client never needs to track modified.
	Retries up to 3x on TimestampMismatchError to handle concurrent saves.
	"""
	frappe.has_permission("HD Task", doc=task_name, ptype="write", throw=True)
	fields = frappe.parse_json(fields)
	subtasks = frappe.parse_json(subtasks)

	allowed_scalar = ALLOWED_FIELDS
	for attempt in range(3):
		try:
			doc = frappe.get_doc("HD Task", task_name)
			for fname, fvalue in fields.items():
				if fname in allowed_scalar:
					if isinstance(fvalue, str) and fvalue == "@me":
						fvalue = frappe.session.user
					doc.set(fname, fvalue or None)
			doc.subtasks = []
			for sub in subtasks:
				doc.append("subtasks", {
					"doctype": "HD Task Subtask",
					"name": sub.get("name") or None,
					"title": sub.get("title", ""),
					"status": sub.get("status", "Backlog"),
					"due_date": sub.get("due_date") or None,
				})
			doc.save()
			return {"modified": str(doc.modified)}
		except frappe.TimestampMismatchError:
			if attempt == 2:
				raise
			frappe.db.rollback()


@frappe.whitelist()
def save_task_subtasks(task_name: str, subtasks: list | str):
	"""Save the subtasks child table for an HD Task.

	Always loads fresh from DB to get the current modified timestamp, and
	retries up to 3 times on TimestampMismatchError (race condition between
	concurrent saves resolves within one retry).
	"""
	frappe.has_permission("HD Task", doc=task_name, ptype="write", throw=True)
	subtasks = frappe.parse_json(subtasks)

	for attempt in range(3):
		try:
			doc = frappe.get_doc("HD Task", task_name)
			doc.subtasks = []
			for sub in subtasks:
				doc.append("subtasks", {
					"doctype": "HD Task Subtask",
					"name": sub.get("name") or None,
					"title": sub.get("title", ""),
					"status": sub.get("status", "Backlog"),
					"due_date": sub.get("due_date") or None,
				})
			doc.save()
			return {"modified": str(doc.modified)}
		except frappe.TimestampMismatchError:
			if attempt == 2:
				raise
			# Brief yield then retry with a fresh get_doc
			frappe.db.rollback()


@frappe.whitelist()
def get_all_task_tags() -> list[str]:
	"""Return all distinct tags used on HD Task documents, sorted alphabetically.

	Reads the _user_tags column (comma-separated) directly from tabHD Task
	to avoid relying on tabTag sync.
	"""
	rows = frappe.db.sql(
		"SELECT _user_tags FROM `tabHD Task` WHERE _user_tags IS NOT NULL AND _user_tags != ''",
		as_dict=False,
	)
	tags: set[str] = set()
	for (tag_str,) in rows:
		for tag in tag_str.split(","):
			tag = tag.strip()
			if tag:
				tags.add(tag)
	return sorted(tags)


@frappe.whitelist()
def search_tasks(query: str) -> list[str]:
	"""Search task names, descriptions, and subtask titles via SQL LIKE.

	Returns a list of matching HD Task names. Used by the kanban filter bar
	for deep-content search without loading heavy fields into the card list.
	"""
	if not query or not query.strip():
		return []
	like = f"%{query.strip()}%"
	main = frappe.db.sql(
		"""
		SELECT name FROM `tabHD Task`
		WHERE title LIKE %(like)s OR description LIKE %(like)s
		""",
		{"like": like},
		as_dict=False,
	)
	subs = frappe.db.sql(
		"""
		SELECT DISTINCT parent FROM `tabHD Task Subtask`
		WHERE title LIKE %(like)s
		""",
		{"like": like},
		as_dict=False,
	)
	names = {r[0] for r in main} | {r[0] for r in subs}
	return list(names)
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk --module helpdesk.helpdesk.doctype.hd_task.test_hd_task
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Restart bench**

```bash
cd /home/frappeuser/bench16
bench restart
```

- [ ] **Step 6: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add helpdesk/helpdesk/doctype/hd_task/hd_task.py helpdesk/helpdesk/doctype/hd_task/test_hd_task.py
git commit -m "feat: add _user_tags support, get_all_task_tags, search_tasks to HD Task"
```

---

## Task 3: TagInput.vue — reusable tag chip editor

**Files:**
- Create: `desk/src/components/TagInput.vue`

This component is used in both `KanbanTaskPanel.vue` and `TaskDetail.vue`.

- [ ] **Step 1: Create the component**

Create `desk/src/components/TagInput.vue` with the following content:

```vue
<template>
  <div class="flex flex-wrap gap-1.5 p-1.5 rounded border border-outline-gray-2 bg-surface-white focus-within:border-outline-gray-4 min-h-[2rem]">
    <!-- Existing tag chips -->
    <span
      v-for="tag in currentTags"
      :key="tag"
      class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium"
      :class="tagColor(tag)"
    >
      {{ tag }}
      <button
        type="button"
        class="hover:opacity-70 flex-shrink-0"
        @click="removeTag(tag)"
      >
        <LucideX class="h-2.5 w-2.5" />
      </button>
    </span>

    <!-- Input -->
    <div class="relative flex-1 min-w-[6rem]">
      <input
        ref="inputRef"
        v-model="inputText"
        type="text"
        class="w-full bg-transparent text-sm text-ink-gray-8 outline-none placeholder:text-ink-gray-4 py-0.5"
        :placeholder="currentTags.length === 0 ? __('Add tags...') : ''"
        @input="onInput"
        @keydown="onKeydown"
        @focus="showDropdown = true"
        @blur="onBlur"
      />

      <!-- Autocomplete dropdown -->
      <div
        v-if="showDropdown && filteredSuggestions.length"
        class="absolute left-0 top-full mt-1 z-50 w-48 rounded-md border border-outline-gray-2 bg-surface-white shadow-md overflow-hidden"
      >
        <button
          v-for="suggestion in filteredSuggestions"
          :key="suggestion"
          type="button"
          class="w-full text-left px-3 py-1.5 text-sm text-ink-gray-8 hover:bg-surface-gray-1 flex items-center gap-2"
          @mousedown.prevent="addTag(suggestion)"
        >
          <span class="inline-block h-2 w-2 rounded-full flex-shrink-0" :class="tagDot(suggestion)" />
          {{ suggestion }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import LucideX from "~icons/lucide/x";
import { computed, ref } from "vue";

const props = defineProps<{
  modelValue: string;   // comma-separated, e.g. "bug,frontend"
  allTags: string[];    // autocomplete source
}>();

const emit = defineEmits<{ "update:modelValue": [value: string] }>();

const inputRef = ref<HTMLInputElement | null>(null);
const inputText = ref("");
const showDropdown = ref(false);

const currentTags = computed<string[]>(() => {
  if (!props.modelValue) return [];
  return props.modelValue.split(",").map((t) => t.trim()).filter(Boolean);
});

const filteredSuggestions = computed<string[]>(() => {
  const q = inputText.value.trim().toLowerCase();
  return props.allTags.filter(
    (t) => !currentTags.value.includes(t) && t.toLowerCase().includes(q)
  );
});

function addTag(tag: string) {
  const trimmed = tag.trim();
  if (!trimmed || currentTags.value.includes(trimmed)) {
    inputText.value = "";
    return;
  }
  const next = [...currentTags.value, trimmed].join(",");
  emit("update:modelValue", next);
  inputText.value = "";
}

function removeTag(tag: string) {
  const next = currentTags.value.filter((t) => t !== tag).join(",");
  emit("update:modelValue", next);
}

function onInput() {
  showDropdown.value = true;
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" || e.key === ",") {
    e.preventDefault();
    const val = inputText.value.replace(/,$/, "").trim();
    if (val) addTag(val);
  } else if (e.key === "Backspace" && !inputText.value && currentTags.value.length) {
    removeTag(currentTags.value[currentTags.value.length - 1]);
  } else if (e.key === "Escape") {
    showDropdown.value = false;
  }
}

function onBlur() {
  // Small delay so mousedown on suggestions fires first
  setTimeout(() => {
    if (inputText.value.trim()) addTag(inputText.value);
    showDropdown.value = false;
  }, 150);
}

// ── Visual helpers ────────────────────────────────────────────
const TAG_CLASSES = [
  "bg-blue-100 text-blue-700",
  "bg-green-100 text-green-700",
  "bg-purple-100 text-purple-700",
  "bg-orange-100 text-orange-700",
  "bg-pink-100 text-pink-700",
  "bg-teal-100 text-teal-700",
];

const TAG_DOT_CLASSES = [
  "bg-blue-400",
  "bg-green-400",
  "bg-purple-400",
  "bg-orange-400",
  "bg-pink-400",
  "bg-teal-400",
];

function hashStr(s: string): number {
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) & 0xffff;
  return h;
}

function tagColor(tag: string): string {
  return TAG_CLASSES[hashStr(tag) % TAG_CLASSES.length];
}

function tagDot(tag: string): string {
  return TAG_DOT_CLASSES[hashStr(tag) % TAG_DOT_CLASSES.length];
}
</script>
```

- [ ] **Step 2: Build and verify component compiles**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | tail -20
```

Expected: build completes without TypeScript or import errors.

- [ ] **Step 3: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/components/TagInput.vue
git commit -m "feat: add TagInput.vue reusable tag chip editor with autocomplete"
```

---

## Task 4: KanbanView.vue — card redesign

**Files:**
- Modify: `desk/src/pages/tasks/KanbanView.vue`

Adds avatar circles, priority bar icons, relative due dates, and tag pills to each card. Also adds `_user_tags` to the list resource fields.

- [ ] **Step 1: Replace KanbanView.vue**

Replace the full content of `desk/src/pages/tasks/KanbanView.vue` with:

```vue
<template>
  <div class="flex flex-col h-full overflow-hidden">

    <!-- ── Filter bar ── -->
    <!-- (Added in Task 5 — placeholder div keeps layout stable) -->

    <!-- ── Kanban columns ── -->
    <div class="flex-1 flex overflow-x-auto gap-3 p-4">
      <div
        v-for="col in columns"
        :key="col.status"
        class="flex flex-col w-72 flex-shrink-0 rounded-lg bg-surface-gray-1 border border-outline-gray-2 transition-all"
        :class="hoveredColumn === col.status ? 'ring-2 ring-ink-blue-3 ring-offset-1' : ''"
        @dragover.prevent="onDragOver(col.status)"
        @dragleave="onDragLeave"
        @drop.prevent="onDrop(col.status)"
      >
        <!-- Column header -->
        <div class="flex items-center justify-between px-3 py-2.5 border-b border-outline-gray-1">
          <div class="flex items-center gap-2">
            <span class="h-2.5 w-2.5 rounded-full flex-shrink-0" :class="col.dotClass" />
            <span class="text-sm font-semibold text-ink-gray-8">{{ col.status }}</span>
            <span class="text-xs text-ink-gray-4 font-normal">
              {{ getCardsForStatus(col.status).length }}
            </span>
          </div>
          <button
            class="flex items-center justify-center h-5 w-5 rounded text-ink-gray-4 hover:text-ink-gray-8 hover:bg-surface-gray-2 transition-colors"
            :title="__('Add task')"
            @click="createTask(col.status)"
          >
            <LucidePlus class="h-3.5 w-3.5" />
          </button>
        </div>

        <!-- Cards -->
        <div class="flex flex-col gap-2 p-2 overflow-y-auto flex-1">
          <div
            v-for="card in getCardsForStatus(col.status)"
            :key="card.name"
            draggable="true"
            class="bg-surface-white rounded-md border border-outline-gray-1 p-3 cursor-pointer hover:border-outline-gray-3 hover:shadow-sm transition-all select-none"
            :class="[
              card.name === selectedTaskId ? 'ring-2 ring-ink-blue-3' : '',
              draggedCard?.name === card.name ? 'opacity-40' : '',
            ]"
            @click="selectedTaskId = card.name"
            @dragstart="onDragStart(card)"
            @dragend="onDragEnd"
          >
            <!-- Title -->
            <p class="text-sm font-medium text-ink-gray-9 leading-snug mb-2">{{ card.title }}</p>

            <!-- Tags row -->
            <div v-if="cardTags(card).length" class="flex flex-wrap gap-1 mb-2">
              <span
                v-for="tag in cardTags(card).slice(0, 2)"
                :key="tag"
                class="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium"
                :class="tagColor(tag)"
              >{{ tag }}</span>
              <span
                v-if="cardTags(card).length > 2"
                class="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-surface-gray-2 text-ink-gray-5"
              >+{{ cardTags(card).length - 2 }}</span>
            </div>

            <!-- Bottom row: priority | due date | avatar -->
            <div class="flex items-center gap-2">
              <!-- Priority bars -->
              <span
                v-if="card.priority"
                :title="card.priority"
                class="flex items-end gap-[2px] flex-shrink-0"
              >
                <span class="w-[3px] rounded-sm" :class="[priorityBarH(card.priority, 0), priorityBarColor(card.priority)]" />
                <span class="w-[3px] rounded-sm" :class="[priorityBarH(card.priority, 1), priorityBarColor(card.priority)]" />
                <span class="w-[3px] rounded-sm" :class="[priorityBarH(card.priority, 2), priorityBarColor(card.priority)]" />
              </span>

              <!-- Due date -->
              <span
                v-if="card.due_date"
                class="flex items-center gap-1 text-xs"
                :class="relativeDue(card.due_date).cls"
              >
                <LucideCalendar class="h-3 w-3 flex-shrink-0" />
                {{ relativeDue(card.due_date).label }}
              </span>

              <!-- Spacer -->
              <span class="flex-1" />

              <!-- Assignee avatar -->
              <span
                v-if="card.assigned_to"
                :title="card.assigned_to"
                class="h-6 w-6 rounded-full flex items-center justify-center text-[10px] font-semibold flex-shrink-0"
                :class="[avatarColor(card.assigned_to).bg, avatarColor(card.assigned_to).text]"
              >{{ avatarInitials(card.assigned_to) }}</span>
            </div>

            <!-- Ticket ref -->
            <div v-if="card.ticket" class="mt-1.5 text-xs text-ink-gray-4">#{{ card.ticket }}</div>
          </div>

          <!-- Empty state -->
          <div
            v-if="getCardsForStatus(col.status).length === 0 && !tasks.list?.loading"
            class="flex flex-col items-center justify-center py-8 text-ink-gray-3"
          >
            <LucideSquareDashed class="h-8 w-8 mb-2 opacity-40" />
            <p class="text-xs">{{ __("No tasks") }}</p>
          </div>

          <!-- Loading skeleton -->
          <div v-if="tasks.list?.loading" class="flex flex-col gap-2">
            <div v-for="i in 3" :key="i" class="h-16 rounded-md bg-surface-gray-2 animate-pulse" />
          </div>
        </div>
      </div>
    </div>

    <!-- ── Collapse toggle ── -->
    <button
      class="flex-shrink-0 w-6 flex items-center justify-center border-l border-outline-gray-1 text-ink-gray-4 hover:text-ink-gray-7 hover:bg-surface-gray-1 transition-colors"
      :title="panelCollapsed ? __('Expand panel') : __('Collapse panel')"
      @click="toggleCollapse"
    >
      <LucideChevronLeft v-if="!panelCollapsed" class="h-4 w-4" />
      <LucideChevronRight v-else class="h-4 w-4" />
    </button>

    <!-- ── Detail panel ── -->
    <div
      v-show="!panelCollapsed"
      class="flex-shrink-0 w-96 border-l border-outline-gray-1 flex flex-col overflow-hidden"
    >
      <div
        v-if="!selectedTaskId"
        class="flex flex-col items-center justify-center h-full text-ink-gray-3 gap-2"
      >
        <LucideSquareDashed class="h-10 w-10 opacity-40" />
        <p class="text-sm">{{ __("Select a task to view details") }}</p>
      </div>

      <KanbanTaskPanel
        v-else
        :key="selectedTaskId"
        :task-id="selectedTaskId"
        :all-tags="allTags"
        @close="selectedTaskId = null"
        @saved="tasks.reload()"
      />
    </div>

  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { call, createListResource, dayjs, toast } from "frappe-ui";
import LucideCalendar from "~icons/lucide/calendar";
import LucideChevronLeft from "~icons/lucide/chevron-left";
import LucideChevronRight from "~icons/lucide/chevron-right";
import LucidePlus from "~icons/lucide/plus";
import LucideSquareDashed from "~icons/lucide/square-dashed";
import { onActivated, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import KanbanTaskPanel from "./KanbanTaskPanel.vue";

const router = useRouter();

// ── Panel state ──────────────────────────────────────────────
const COLLAPSE_KEY = "hd_task_kanban_panel_collapsed";
const selectedTaskId = ref<string | null>(null);
const panelCollapsed = ref(false);

onMounted(() => {
  if (window.innerWidth < 640) {
    panelCollapsed.value = true;
  } else {
    panelCollapsed.value = localStorage.getItem(COLLAPSE_KEY) === "true";
  }
  tasks.reload();
  loadAllTags();
});

onActivated(() => tasks.reload());

function toggleCollapse() {
  panelCollapsed.value = !panelCollapsed.value;
  localStorage.setItem(COLLAPSE_KEY, String(panelCollapsed.value));
}

// ── Columns ──────────────────────────────────────────────────
const columns = [
  { status: "Backlog", dotClass: "bg-gray-400" },
  { status: "Todo", dotClass: "bg-blue-400" },
  { status: "In Progress", dotClass: "bg-orange-400" },
  { status: "Done", dotClass: "bg-green-400" },
];

// ── Task list ────────────────────────────────────────────────
const tasks = createListResource({
  doctype: "HD Task",
  fields: ["name", "title", "status", "priority", "due_date", "assigned_to", "ticket", "_user_tags"],
  filters: [],
  orderBy: "modified desc",
  pageLength: 999,
  auto: true,
});

function getCardsForStatus(status: string) {
  return (tasks.data ?? []).filter((t: any) => t.status === status);
}

// ── All tags (for panel autocomplete) ────────────────────────
const allTags = ref<string[]>([]);

async function loadAllTags() {
  try {
    const result = await call("helpdesk.helpdesk.doctype.hd_task.hd_task.get_all_task_tags");
    allTags.value = result ?? [];
  } catch {
    // non-critical
  }
}

// ── Drag and drop ────────────────────────────────────────────
interface DraggedCard { name: string; status: string }
const draggedCard = ref<DraggedCard | null>(null);
const hoveredColumn = ref<string | null>(null);

function onDragStart(card: any) {
  draggedCard.value = { name: card.name, status: card.status };
}

function onDragEnd() {
  draggedCard.value = null;
  hoveredColumn.value = null;
}

function onDragOver(status: string) {
  hoveredColumn.value = status;
}

function onDragLeave() {
  hoveredColumn.value = null;
}

async function onDrop(targetStatus: string) {
  hoveredColumn.value = null;
  const card = draggedCard.value;
  draggedCard.value = null;

  if (!card || card.status === targetStatus) return;

  const live = (tasks.data ?? []).find((t: any) => t.name === card.name);
  if (live) live.status = targetStatus;

  try {
    await call(
      "helpdesk.helpdesk.doctype.hd_task.hd_task.set_task_field",
      { task_name: card.name, fieldname: "status", value: targetStatus }
    );
    tasks.reload();
  } catch {
    if (live) live.status = card.status;
    toast.error(__("Failed to move task"));
  }
}

// ── Helpers ──────────────────────────────────────────────────
function createTask(status: string) {
  router.push({ name: "TaskAgentNew", query: { status } });
}

function formatDate(d: string) {
  if (!d) return "";
  return dayjs(d).format((window as any).date_format?.toUpperCase() || "DD-MM-YYYY");
}

// ── Avatar ───────────────────────────────────────────────────
const AVATAR_COLORS = [
  { bg: "bg-blue-100", text: "text-blue-700" },
  { bg: "bg-green-100", text: "text-green-700" },
  { bg: "bg-purple-100", text: "text-purple-700" },
  { bg: "bg-orange-100", text: "text-orange-700" },
  { bg: "bg-pink-100", text: "text-pink-700" },
  { bg: "bg-teal-100", text: "text-teal-700" },
  { bg: "bg-indigo-100", text: "text-indigo-700" },
  { bg: "bg-red-100", text: "text-red-700" },
];

function hashStr(s: string): number {
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) & 0xffff;
  return h;
}

function avatarInitials(name: string): string {
  if (!name) return "?";
  const parts = name.split(/[@.\s]/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

function avatarColor(name: string): { bg: string; text: string } {
  return AVATAR_COLORS[hashStr(name) % AVATAR_COLORS.length];
}

// ── Priority bars ────────────────────────────────────────────
const PRIORITY_HEIGHTS: Record<string, [string, string, string]> = {
  Low:    ["h-[4px]", "h-[4px]", "h-[4px]"],
  Medium: ["h-[4px]", "h-[8px]", "h-[8px]"],
  High:   ["h-[4px]", "h-[8px]", "h-[12px]"],
  Urgent: ["h-[4px]", "h-[8px]", "h-[12px]"],
};

function priorityBarH(priority: string, idx: number): string {
  return (PRIORITY_HEIGHTS[priority] ?? PRIORITY_HEIGHTS["Low"])[idx];
}

function priorityBarColor(priority: string): string {
  const map: Record<string, string> = {
    Urgent: "bg-red-500",
    High:   "bg-orange-500",
    Medium: "bg-amber-400",
    Low:    "bg-green-400",
  };
  return map[priority] ?? "bg-gray-300";
}

// ── Relative due date ─────────────────────────────────────────
function relativeDue(d: string): { label: string; cls: string } {
  if (!d) return { label: "", cls: "" };
  const today = dayjs().startOf("day");
  const due = dayjs(d).startOf("day");
  const diff = due.diff(today, "day");
  if (diff < -1) return { label: `${Math.abs(diff)}d ago`, cls: "text-red-500" };
  if (diff === -1) return { label: "Yesterday", cls: "text-red-500" };
  if (diff === 0)  return { label: "Today", cls: "text-ink-gray-6" };
  if (diff === 1)  return { label: "Tomorrow", cls: "text-blue-500" };
  if (diff <= 7)   return { label: `In ${diff}d`, cls: "text-ink-gray-5" };
  return { label: formatDate(d), cls: "text-ink-gray-4" };
}

// ── Tags ──────────────────────────────────────────────────────
const TAG_CLASSES = [
  "bg-blue-100 text-blue-700",
  "bg-green-100 text-green-700",
  "bg-purple-100 text-purple-700",
  "bg-orange-100 text-orange-700",
  "bg-pink-100 text-pink-700",
  "bg-teal-100 text-teal-700",
];

function tagColor(tag: string): string {
  return TAG_CLASSES[hashStr(tag) % TAG_CLASSES.length];
}

function cardTags(card: any): string[] {
  if (!card._user_tags) return [];
  return card._user_tags.split(",").map((t: string) => t.trim()).filter(Boolean);
}
</script>
```

- [ ] **Step 2: Build and check for errors**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | tail -30
```

Expected: build succeeds. No TypeScript or import errors.

- [ ] **Step 3: Visual check in browser**

Open `http://localhost:8002` → Tasks → Kanban view. Verify:
- Cards show avatar circles (initials, colored) instead of username text
- Priority shows 3 tiny bars instead of a badge
- Due dates show relative labels (Yesterday/Today/Tomorrow/X days ago)
- Cards with `_user_tags` show colored pill chips

- [ ] **Step 4: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/pages/tasks/KanbanView.vue
git commit -m "feat: redesign kanban cards with avatars, priority bars, relative dates, tag pills"
```

---

## Task 5: KanbanView.vue — filter bar

**Files:**
- Modify: `desk/src/pages/tasks/KanbanView.vue`

Adds the filter bar (search, assignee, tag) above the kanban columns with AND-composed filtering.

- [ ] **Step 1: Add filter bar template**

In `KanbanView.vue`, replace the comment `<!-- ── Filter bar ── -->` placeholder block with:

```html
    <!-- ── Filter bar ── -->
    <div class="flex items-center gap-2 px-4 py-2 border-b border-outline-gray-1 bg-surface-white flex-shrink-0">

      <!-- Search -->
      <div class="relative flex items-center">
        <LucideSearch class="absolute left-2 h-3.5 w-3.5 text-ink-gray-4 pointer-events-none" />
        <input
          v-model="filterSearch"
          type="text"
          class="pl-7 pr-6 py-1.5 text-sm rounded border border-outline-gray-2 bg-surface-white text-ink-gray-8 placeholder:text-ink-gray-4 focus:outline-none focus:border-outline-gray-4 w-52"
          :placeholder="__('Search tasks…')"
          @input="onSearchInput"
        />
        <button
          v-if="filterSearch"
          class="absolute right-1.5 text-ink-gray-4 hover:text-ink-gray-7"
          @click="filterSearch = ''; searchResultNames = null"
        >
          <LucideX class="h-3 w-3" />
        </button>
        <LucideLoader v-if="searchLoading" class="absolute right-1.5 h-3 w-3 text-ink-gray-4 animate-spin" />
      </div>

      <!-- Assignee filter -->
      <div class="relative flex items-center">
        <LucideUser class="absolute left-2 h-3.5 w-3.5 text-ink-gray-4 pointer-events-none z-10" />
        <select
          v-model="filterAssignee"
          class="pl-7 pr-6 py-1.5 text-sm rounded border border-outline-gray-2 bg-surface-white text-ink-gray-8 focus:outline-none focus:border-outline-gray-4 appearance-none w-40"
        >
          <option value="">{{ __('All agents') }}</option>
          <option
            v-for="agent in uniqueAssignees"
            :key="agent"
            :value="agent"
          >{{ agent }}</option>
        </select>
        <button
          v-if="filterAssignee"
          class="absolute right-1.5 text-ink-gray-4 hover:text-ink-gray-7"
          @click="filterAssignee = ''"
        >
          <LucideX class="h-3 w-3" />
        </button>
      </div>

      <!-- Tag filter -->
      <div class="relative flex items-center">
        <LucideTag class="absolute left-2 h-3.5 w-3.5 text-ink-gray-4 pointer-events-none" />
        <input
          v-model="filterTag"
          type="text"
          list="kanban-tag-list"
          class="pl-7 pr-6 py-1.5 text-sm rounded border border-outline-gray-2 bg-surface-white text-ink-gray-8 placeholder:text-ink-gray-4 focus:outline-none focus:border-outline-gray-4 w-36"
          :placeholder="__('Filter by tag')"
        />
        <datalist id="kanban-tag-list">
          <option v-for="tag in allTags" :key="tag" :value="tag" />
        </datalist>
        <button
          v-if="filterTag"
          class="absolute right-1.5 text-ink-gray-4 hover:text-ink-gray-7"
          @click="filterTag = ''"
        >
          <LucideX class="h-3 w-3" />
        </button>
      </div>

      <!-- Clear all -->
      <button
        v-if="hasFilters"
        class="text-xs text-ink-gray-4 hover:text-ink-gray-7 ml-1"
        @click="clearFilters"
      >
        {{ __('Clear all') }}
      </button>
    </div>
```

- [ ] **Step 2: Add filter state and logic to the script**

In the `<script setup>` section of `KanbanView.vue`, add these additions:

After the `allTags` ref, add the filter state:

```ts
// ── Filter state ─────────────────────────────────────────────
const filterSearch = ref("");
const filterAssignee = ref("");
const filterTag = ref("");
const searchResultNames = ref<Set<string> | null>(null);
const searchLoading = ref(false);
let searchTimer: ReturnType<typeof setTimeout> | null = null;

const hasFilters = computed(() => !!(filterSearch.value || filterAssignee.value || filterTag.value));

const uniqueAssignees = computed<string[]>(() => {
  const set = new Set<string>();
  for (const t of (tasks.data ?? [])) {
    if (t.assigned_to) set.add(t.assigned_to);
  }
  return [...set].sort();
});

function onSearchInput() {
  if (searchTimer) clearTimeout(searchTimer);
  if (!filterSearch.value.trim()) {
    searchResultNames.value = null;
    return;
  }
  searchLoading.value = true;
  searchTimer = setTimeout(async () => {
    try {
      const names: string[] = await call(
        "helpdesk.helpdesk.doctype.hd_task.hd_task.search_tasks",
        { query: filterSearch.value.trim() }
      );
      searchResultNames.value = new Set(names);
    } catch {
      searchResultNames.value = null;
    } finally {
      searchLoading.value = false;
    }
  }, 300);
}

function clearFilters() {
  filterSearch.value = "";
  filterAssignee.value = "";
  filterTag.value = "";
  searchResultNames.value = null;
}
```

Replace the existing `getCardsForStatus` function with:

```ts
function getCardsForStatus(status: string) {
  return (tasks.data ?? []).filter((t: any) => {
    if (t.status !== status) return false;
    if (searchResultNames.value !== null && !searchResultNames.value.has(t.name)) return false;
    if (filterAssignee.value && t.assigned_to !== filterAssignee.value) return false;
    if (filterTag.value) {
      const tags = (t._user_tags ?? "").split(",").map((x: string) => x.trim()).filter(Boolean);
      if (!tags.includes(filterTag.value)) return false;
    }
    return true;
  });
}
```

Add these new icon imports to the existing import block:

```ts
import LucideLoader from "~icons/lucide/loader";
import LucideSearch from "~icons/lucide/search";
import LucideTag from "~icons/lucide/tag";
import LucideUser from "~icons/lucide/user";
import LucideX from "~icons/lucide/x";
```

Also add `computed` to the vue import:
```ts
import { computed, onActivated, onMounted, ref } from "vue";
```

- [ ] **Step 3: Build**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | tail -30
```

Expected: clean build.

- [ ] **Step 4: Visual check**

Open the kanban view. Verify:
- Filter bar appears above columns
- Typing in search triggers API call after 300ms pause, filters cards
- Selecting an agent in the assignee dropdown filters cards
- Typing a tag name filters to cards that have that tag
- "Clear all" button appears when any filter is active and resets all three

- [ ] **Step 5: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/pages/tasks/KanbanView.vue
git commit -m "feat: add filter bar to kanban (search, assignee, tag)"
```

---

## Task 6: KanbanTaskPanel.vue — copy button, tags, footer

**Files:**
- Modify: `desk/src/pages/tasks/KanbanTaskPanel.vue`

- [ ] **Step 1: Replace KanbanTaskPanel.vue**

Replace the full content of `desk/src/pages/tasks/KanbanTaskPanel.vue` with:

```vue
<template>
  <div class="flex flex-col h-full bg-surface-white overflow-hidden">

    <!-- Header -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-outline-gray-1 flex-shrink-0">
      <span class="text-xs text-green-600 transition-opacity duration-300 min-w-0">
        {{ savedIndicator ? __('✓ Saved') : '' }}
      </span>
      <div class="flex items-center gap-3 flex-shrink-0">
        <button
          class="text-xs text-ink-gray-5 hover:text-ink-blue-4"
          @click="router.push({ name: 'TaskAgent', params: { taskId } })"
        >
          {{ __('Open full page →') }}
        </button>
        <!-- Copy button -->
        <button
          class="text-ink-gray-4 hover:text-ink-gray-7"
          :title="__('Copy task summary')"
          @click="copyToClipboard"
        >
          <LucideClipboard class="h-4 w-4" />
        </button>
        <button
          class="text-ink-gray-4 hover:text-ink-gray-7"
          @click="$emit('close')"
        >
          <LucideX class="h-4 w-4" />
        </button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="task.loading" class="flex items-center justify-center h-32">
      <LoadingIndicator class="h-5 w-5 text-ink-gray-4" />
    </div>

    <!-- Content -->
    <div v-else-if="task.doc" class="flex flex-col gap-4 p-4 overflow-y-auto flex-1">

      <!-- Title -->
      <input
        v-model="form.title"
        type="text"
        class="w-full text-base font-semibold text-ink-gray-9 bg-transparent border-0 outline-none focus:ring-1 focus:ring-outline-gray-3 rounded px-1 -mx-1"
        :placeholder="__('Task title')"
        @blur="saveField('title', form.title)"
      />

      <!-- Status + Priority -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Status') }}</label>
          <select
            v-model="form.status"
            class="text-sm rounded border border-outline-gray-2 bg-surface-white px-2 py-1.5 text-ink-gray-8 focus:outline-none focus:border-outline-gray-4"
            @change="saveField('status', form.status)"
          >
            <option v-for="s in statusOptions" :key="s" :value="s">{{ s }}</option>
          </select>
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Priority') }}</label>
          <Link
            :value="form.priority"
            doctype="HD Ticket Priority"
            :placeholder="__('—')"
            class="form-control"
            @change="(val) => { form.priority = val; saveField('priority', val || null); }"
          />
        </div>
      </div>

      <!-- Assigned To + Due Date -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Assigned To') }}</label>
          <Link
            :value="form.assigned_to"
            doctype="HD Agent"
            :placeholder="__('—')"
            class="form-control"
            @change="(val) => { form.assigned_to = val; saveField('assigned_to', val || null); }"
          />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Due Date') }}</label>
          <DatePicker
            v-model="form.due_date"
            :format="dateFormat"
            :clearable="true"
            :placeholder="__('—')"
            :input-class="isOverdue(form.due_date) ? '!text-red-500' : ''"
            @change="(val) => saveField('due_date', val || null)"
          />
        </div>
      </div>

      <!-- Ticket + Team -->
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="flex items-center gap-1 text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">
            {{ __('Ticket') }}
            <button
              v-if="form.ticket"
              class="text-ink-blue-4 hover:text-ink-blue-5 normal-case"
              :title="__('Open ticket')"
              @click="router.push({ name: 'TicketAgent', params: { ticketId: form.ticket } })"
            >
              <LucideExternalLink class="h-3 w-3" />
            </button>
          </label>
          <Link
            :value="form.ticket"
            doctype="HD Ticket"
            :placeholder="__('—')"
            class="form-control"
            @change="(val) => { form.ticket = val; saveField('ticket', val || null); }"
          />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Team') }}</label>
          <Link
            :value="form.team"
            doctype="HD Team"
            :placeholder="__('—')"
            class="form-control"
            @change="(val) => { form.team = val; saveField('team', val || null); }"
          />
        </div>
      </div>

      <!-- Tags -->
      <div class="flex flex-col gap-1">
        <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Tags') }}</label>
        <TagInput
          :model-value="form.user_tags"
          :all-tags="allTags"
          @update:model-value="(val) => { form.user_tags = val; saveField('_user_tags', val || null); }"
        />
      </div>

      <!-- Description -->
      <div class="flex flex-col gap-1">
        <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">{{ __('Description') }}</label>
        <TextEditor
          v-model:content="form.description"
          :editable="true"
          editor-class="min-h-[5rem] prose-f p-2 rounded border border-outline-gray-2 focus-within:border-outline-gray-4 text-sm"
          :placeholder="__('Add a description...')"
          @change="debouncedSaveDescription"
        />
      </div>

      <!-- Subtasks -->
      <div class="flex flex-col gap-2">
        <div class="flex items-center justify-between">
          <label class="text-xs font-semibold text-ink-gray-5 uppercase tracking-wide">
            {{ __('Subtasks') }}
          </label>
          <span class="text-xs text-ink-gray-4">{{ doneCount }} / {{ form.subtasks.length }} {{ __('done') }}</span>
        </div>

        <div v-if="form.subtasks.length" class="h-1 w-full rounded-full bg-surface-gray-2 overflow-hidden">
          <div
            class="h-full rounded-full bg-green-500 transition-all duration-300"
            :style="{ width: progressPct + '%' }"
          />
        </div>

        <div
          v-for="(sub, idx) in form.subtasks"
          :key="sub.name || idx"
          class="flex items-center gap-2 rounded p-1 hover:bg-surface-gray-1 group"
        >
          <Checkbox
            :model-value="sub.status === 'Done'"
            class="flex-shrink-0"
            @update:model-value="toggleSubtask(idx)"
          />
          <input
            v-model="sub.title"
            type="text"
            class="flex-1 bg-transparent text-sm text-ink-gray-8 outline-none placeholder:text-ink-gray-4"
            :class="sub.status === 'Done' ? 'line-through text-ink-gray-4' : ''"
            :placeholder="__('Subtask title')"
            @blur="saveSubtasks"
          />
          <button
            class="invisible group-hover:visible text-ink-gray-4 hover:text-red-400"
            @click="removeSubtask(idx)"
          >
            <LucideX class="h-3 w-3" />
          </button>
        </div>

        <button
          class="flex items-center gap-1 text-xs text-ink-gray-5 hover:text-ink-gray-8 w-fit"
          @click="addSubtask"
        >
          <LucidePlus class="h-3.5 w-3.5" />
          {{ __('Add subtask') }}
        </button>
      </div>

    </div>

    <!-- Footer: created by / on -->
    <div
      v-if="task.doc"
      class="flex-shrink-0 flex items-center gap-3 px-4 py-2 border-t border-outline-gray-1 text-xs text-ink-gray-4"
    >
      <span>{{ __('Created by') }} <span class="font-medium text-ink-gray-6">{{ task.doc.owner }}</span></span>
      <span>{{ formatCreation(task.doc.creation) }}</span>
    </div>

  </div>
</template>

<script setup lang="ts">
import TagInput from "@/components/TagInput.vue";
import Link from "@/components/frappe-ui/Link.vue";
import { __ } from "@/translation";
import {
  call,
  Checkbox,
  createDocumentResource,
  DatePicker,
  dayjs,
  LoadingIndicator,
  TextEditor,
  toast,
} from "frappe-ui";
import LucideClipboard from "~icons/lucide/clipboard";
import LucideExternalLink from "~icons/lucide/external-link";
import LucidePlus from "~icons/lucide/plus";
import LucideX from "~icons/lucide/x";
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

const dateFormat = (window as any).date_format?.toUpperCase() || "DD-MM-YYYY";

const props = defineProps<{
  taskId: string;
  allTags: string[];
}>();
const emit = defineEmits<{ close: []; saved: [] }>();
const router = useRouter();

const savedIndicator = ref(false);
const isFormLoaded = ref(false);
const currentModified = ref<string | null>(null);
let savedTimer: ReturnType<typeof setTimeout> | null = null;
let descTimer: ReturnType<typeof setTimeout> | null = null;

const statusOptions = ["Backlog", "Todo", "In Progress", "Done"];

interface Subtask {
  name?: string;
  title: string;
  status: string;
  due_date: string;
}

const form = reactive({
  title: "",
  status: "Backlog",
  priority: "",
  assigned_to: "",
  due_date: "",
  ticket: "",
  team: "",
  description: "",
  user_tags: "",   // mirrors _user_tags (underscore-prefixed fields can't be reactive keys)
  subtasks: [] as Subtask[],
});

const task = createDocumentResource({
  doctype: "HD Task",
  name: props.taskId,
  auto: true,
  onError() {
    toast.error(__("Task not found"));
    emit("close");
  },
});

watch(
  () => task.doc,
  (doc: any) => {
    if (!doc) return;
    isFormLoaded.value = false;
    currentModified.value = doc.modified ?? null;
    form.title = doc.title ?? "";
    form.status = doc.status ?? "Backlog";
    form.priority = doc.priority ?? "";
    form.assigned_to = doc.assigned_to ?? "";
    form.due_date = doc.due_date ?? "";
    form.ticket = doc.ticket ?? "";
    form.team = doc.team ?? "";
    form.description = doc.description ?? "";
    form.user_tags = doc._user_tags ?? "";
    form.subtasks = (doc.subtasks ?? []).map((s: any) => ({
      name: s.name,
      title: s.title ?? "",
      status: s.status ?? "Backlog",
      due_date: s.due_date ?? "",
    }));
    nextTick(() => { isFormLoaded.value = true; });
  },
  { immediate: true }
);

onMounted(() => task.reload());

const doneCount = computed(() => form.subtasks.filter((s) => s.status === "Done").length);
const progressPct = computed(() =>
  form.subtasks.length ? Math.round((doneCount.value / form.subtasks.length) * 100) : 0
);

function errorMessage(e: any, fallback: string): string {
  if (e?.exc_type === "TimestampMismatchError") {
    return __("Document was modified elsewhere — close and reopen the panel to refresh.");
  }
  return e?.message || e?.exc || fallback;
}

function flashSaved() {
  savedIndicator.value = true;
  if (savedTimer) clearTimeout(savedTimer);
  savedTimer = setTimeout(() => { savedIndicator.value = false; }, 1500);
}

async function saveField(fieldname: string, value: any) {
  try {
    const result = await call(
      "helpdesk.helpdesk.doctype.hd_task.hd_task.set_task_field",
      { task_name: props.taskId, fieldname, value: value || null }
    );
    if (result?.modified) currentModified.value = result.modified;
    flashSaved();
    emit("saved");
  } catch (e: any) {
    toast.error(errorMessage(e, __("Failed to save")));
  }
}

function debouncedSaveDescription(val?: string) {
  if (!isFormLoaded.value) return;
  if (val !== undefined) form.description = val;
  if (descTimer) clearTimeout(descTimer);
  descTimer = setTimeout(() => saveField("description", form.description), 800);
}

async function saveSubtasks() {
  try {
    const result = await call(
      "helpdesk.helpdesk.doctype.hd_task.hd_task.save_task_subtasks",
      {
        task_name: props.taskId,
        subtasks: JSON.stringify(
          form.subtasks.map((s) => ({
            name: s.name || null,
            title: s.title,
            status: s.status,
            due_date: s.due_date || null,
          }))
        ),
      }
    );
    if (result?.modified) currentModified.value = result.modified;
    task.reload();
    flashSaved();
    emit("saved");
  } catch (e: any) {
    toast.error(errorMessage(e, __("Failed to save subtasks")));
  }
}

function addSubtask() {
  form.subtasks.push({ title: "", status: "Backlog", due_date: "" });
}

function removeSubtask(idx: number) {
  form.subtasks.splice(idx, 1);
  saveSubtasks();
}

function toggleSubtask(idx: number) {
  const sub = form.subtasks[idx];
  sub.status = sub.status === "Done" ? "Todo" : "Done";
  saveSubtasks();
}

function isOverdue(d: string) {
  if (!d) return false;
  return new Date(d) < new Date();
}

function formatCreation(ts: string): string {
  if (!ts) return "";
  return dayjs(ts).format("DD MMM YYYY");
}

function copyToClipboard() {
  const lines: string[] = [
    `Task: ${form.title}`,
    `Status: ${form.status}`,
  ];
  if (form.user_tags) lines.push(`Tags: ${form.user_tags}`);
  if (form.subtasks.length) {
    lines.push(`Progress: ${doneCount.value} of ${form.subtasks.length} subtasks completed`);
    lines.push("");
    lines.push("Subtasks:");
    for (const s of form.subtasks) {
      const check = s.status === "Done" ? "[x]" : "[ ]";
      lines.push(`${check} ${s.title} (${s.status})`);
    }
  }
  navigator.clipboard
    .writeText(lines.join("\n"))
    .then(() => toast.success(__("Copied to clipboard")))
    .catch(() => toast.error(__("Could not copy to clipboard")));
}
</script>
```

- [ ] **Step 2: Build**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | tail -30
```

Expected: clean build.

- [ ] **Step 3: Visual check**

Open the kanban, click a task to open the panel. Verify:
- Clipboard icon appears in the header next to "Open full page →"
- Tags field appears after Team, showing chips + autocomplete input
- Footer shows "Created by [owner] [date]"
- Clicking the clipboard icon copies the task summary to clipboard (check browser clipboard)

- [ ] **Step 4: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/pages/tasks/KanbanTaskPanel.vue
git commit -m "feat: add copy button, tags field, and created-by footer to KanbanTaskPanel"
```

---

## Task 7: TaskDetail.vue — tags field and created-by footer

**Files:**
- Modify: `desk/src/pages/tasks/TaskDetail.vue`

- [ ] **Step 1: Add TagInput import and allTags fetch to TaskDetail.vue**

In `TaskDetail.vue`, add the TagInput import at the top of `<script setup>`:

```ts
import TagInput from "@/components/TagInput.vue";
```

Add `allTags` state and fetch logic after the existing `ref`/`reactive` declarations:

```ts
const allTags = ref<string[]>([]);

onMounted(async () => {
  try {
    const result = await call("helpdesk.helpdesk.doctype.hd_task.hd_task.get_all_task_tags");
    allTags.value = result ?? [];
  } catch {
    // non-critical
  }
});
```

Add `user_tags` to the `form` reactive object (after `description`):

```ts
  user_tags: "",
```

In the `watch(() => task.doc, ...)` callback, add after `form.description = doc.description ?? ""`:

```ts
    form.user_tags = doc._user_tags ?? "";
```

In `saveTask()`, add `_user_tags` to the fields map:

```ts
        _user_tags: form.user_tags || null,
```

Also add `call` to the frappe-ui imports if not already there (it already is), and add `onMounted` to the vue imports.

- [ ] **Step 2: Add Tags field to the template**

In `TaskDetail.vue` template, after the Team field block (the second column of the third grid row), add a new full-width row for Tags:

```html
      <!-- Tags -->
      <div class="flex flex-col gap-1.5">
        <label class="block text-sm font-medium text-ink-gray-7">
          {{ __("Tags") }}
        </label>
        <TagInput
          v-model="form.user_tags"
          :all-tags="allTags"
        />
      </div>
```

Place this after the Linked Ticket + Team row and before the Description row.

- [ ] **Step 3: Add created-by footer to the template**

In `TaskDetail.vue` template, after the closing `</div>` of the subtasks section (before the closing `</div>` of `v-else-if="task.doc"`), add:

```html
      <!-- Created by footer -->
      <div class="flex items-center gap-3 pt-2 border-t border-outline-gray-1 text-xs text-ink-gray-4">
        <span>{{ __('Created by') }} <span class="font-medium text-ink-gray-6">{{ task.doc.owner }}</span></span>
        <span>{{ dayjs(task.doc.creation).format('DD MMM YYYY') }}</span>
      </div>
```

- [ ] **Step 4: Build**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk 2>&1 | tail -30
```

Expected: clean build.

- [ ] **Step 5: Visual check**

Open a task full page (Tasks → click any task → "Open full page"). Verify:
- Tags field appears between Team and Description
- Tags show as chips, typing autocompletes from existing tags
- Enter or comma confirms a new tag, saves immediately on blur/save
- Footer at bottom shows "Created by [name] [date]"
- Save button saves tags with the rest of the form

- [ ] **Step 6: Commit**

```bash
cd /home/frappeuser/bench16/apps/helpdesk
git add desk/src/pages/tasks/TaskDetail.vue
git commit -m "feat: add tags field and created-by footer to TaskDetail full-page view"
```

---

## Self-Review

**Spec coverage check:**
- ✅ `track_changes: 1` — Task 1
- ✅ `_user_tags` in ALLOWED_FIELDS — Task 2
- ✅ `get_all_task_tags()` — Task 2
- ✅ `search_tasks()` — Task 2
- ✅ `TagInput.vue` reusable component — Task 3
- ✅ Avatar circles on cards — Task 4
- ✅ Priority bar icons on cards — Task 4
- ✅ Relative due dates on cards — Task 4
- ✅ Tag pills on cards (up to 2 + overflow) — Task 4
- ✅ Filter bar: search (server-side, debounced) — Task 5
- ✅ Filter bar: assignee (client-side) — Task 5
- ✅ Filter bar: tag (client-side) — Task 5
- ✅ AND composition of filters — Task 5
- ✅ Copy button in KanbanTaskPanel header — Task 6
- ✅ Tags field in KanbanTaskPanel — Task 6
- ✅ Created-by footer in KanbanTaskPanel — Task 6
- ✅ `allTags` prop passed from KanbanView to KanbanTaskPanel — Task 4 + 6
- ✅ Tags field in TaskDetail — Task 7
- ✅ Created-by footer in TaskDetail — Task 7

**Type consistency check:**
- `form.user_tags` (string) used in both KanbanTaskPanel and TaskDetail — consistent
- `allTags: string[]` prop in KanbanTaskPanel, same type in TagInput — consistent
- `searchResultNames: Set<string> | null` — null = no filter, Set = filter active — consistent with `getCardsForStatus`
- `hashStr()` function defined once in KanbanView, duplicated logic inline in TagInput (both files are self-contained — acceptable)
- `saveField('_user_tags', val)` — `_user_tags` now in ALLOWED_FIELDS ✅
