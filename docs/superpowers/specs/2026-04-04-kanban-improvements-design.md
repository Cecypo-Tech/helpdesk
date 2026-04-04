# Kanban Improvements Design
Date: 2026-04-04

## Overview

Improve the HD Task kanban board with better card readability, filtering, tagging, task history, and panel enhancements. Inspired by the reference ERPNext project management UI.

---

## 1. Backend / DocType Changes

### `hd_task.json`
- Add `"track_changes": 1` — enables Frappe's built-in version log (full field-level history accessible via the DocType's full-page view).

### `hd_task.py` — new/modified whitelist functions

**`ALLOWED_FIELDS`**: add `"_user_tags"` so `set_task_field` can save tags.

**`get_all_task_tags()`** — new:
```python
@frappe.whitelist()
def get_all_task_tags():
    """Return all distinct tags used on HD Task documents."""
    rows = frappe.db.sql("""
        SELECT DISTINCT tag FROM `tabTag`
        WHERE document_type = 'HD Task'
        ORDER BY tag
    """, as_dict=False)
    return [r[0] for r in rows]
```

**`search_tasks(query)`** — new:
```python
@frappe.whitelist()
def search_tasks(query: str):
    """Search task names, descriptions, and subtask titles. Returns list of matching task names."""
    like = f"%{query}%"
    # Search title + description on main table
    main = frappe.db.sql("""
        SELECT name FROM `tabHD Task`
        WHERE title LIKE %(like)s OR description LIKE %(like)s
    """, {"like": like}, as_dict=False)
    # Search subtask titles
    subs = frappe.db.sql("""
        SELECT DISTINCT parent FROM `tabHD Task Subtask`
        WHERE title LIKE %(like)s
    """, {"like": like}, as_dict=False)
    names = {r[0] for r in main} | {r[0] for r in subs}
    return list(names)
```

### List resource fields
Add `"_user_tags"` to the `fields` array in `KanbanView.vue`'s `createListResource` call so tags are available on cards without a separate fetch.

---

## 2. Card Design (KanbanView.vue)

### Assignee — avatar circle
Replace `<LucideUser> text` with an avatar circle showing initials, colored by a hash of the username:
- 2-letter initials (first char of first + last word split by `@` or space)
- Background color: one of ~8 preset semantic colors, picked by `charCodeAt(0) % 8`
- Size: `h-6 w-6`, `text-[10px]`, `rounded-full`

### Priority — bar-chart icon
Replace the text badge with an inline SVG showing 3 vertical bars:
- Low: 1 bar filled (gray-green)
- Medium: 2 bars filled (amber)
- High: 3 bars filled (orange)
- Urgent: 3 bars filled (red)
- Each bar is `2px wide`, heights `4px / 8px / 12px`, gap `1px`

### Due date — relative labels
| Condition | Label | Color class |
|-----------|-------|-------------|
| Overdue (past, not today) | "X days ago" or "Yesterday" | `text-red-500` |
| Today | "Today" | `text-ink-gray-6` |
| Tomorrow | "Tomorrow" | `text-blue-500` |
| Within 7 days | "In N days" | `text-ink-gray-5` |
| Further out | formatted date | `text-ink-gray-4` |

### Tags on cards
- Show up to 2 tag pills below the title
- Pill style: `text-[10px] px-1.5 py-0.5 rounded-full bg-surface-gray-2 text-ink-gray-6`
- Tag color: hash tag string → one of 6 light background / dark text pairs (same approach as assignee color)
- If more than 2 tags: show "+N" chip
- Tags sourced from `_user_tags` (comma-separated string → split and trim)

### Card layout
```
[Title — bold, full width]
[Tag pill] [Tag pill] [+N]
[Priority bars]  [Due date]  [Avatar]
[#ticket]  (if present)
```

---

## 3. Filter Bar (KanbanView.vue)

A slim bar (`h-10`, `border-b border-outline-gray-1`, `bg-surface-white`) directly above the kanban columns, inside the main layout div, below the page header.

### Controls (left to right)
1. **Search** — text input (`w-56`), placeholder "Search tasks…", debounced 300ms, triggers `search_tasks(query)` API call. Shows a spinner while loading. Clear (×) button when non-empty.
2. **Assignee** — `Link` component (`doctype="HD Agent"`, `w-40`), placeholder "All agents". Clear button.
3. **Tag** — text input with dropdown autocomplete (`w-36`), populated from `get_all_task_tags()` (fetched once on mount). Clear button.
4. **Clear all** — small text button `text-ink-gray-4 hover:text-ink-gray-7`, only visible when any filter is active.

### Filter logic in `getCardsForStatus(status)`
All three filters compose with AND logic:
```
cards = tasks.data filtered by status
if searchResultNames is non-null → keep only cards whose name is in searchResultNames
if assigneeFilter → keep only cards where assigned_to === assigneeFilter
if tagFilter → keep only cards where _user_tags contains tagFilter
```

`searchResultNames` is `null` when search is empty (show all), or a `Set<string>` of names returned by the API.

---

## 4. KanbanTaskPanel Additions

### Copy button in header
Add `LucideClipboard` icon button (same size as the X close button, `h-4 w-4`) to the right of "Open full page →" in the panel header. On click, copies task summary to clipboard:
```
Task: {title}
Status: {status}
Tags: {_user_tags}
[subtask checklist if any]
```

### Tags field
New form row after the Team field:
- Label: "Tags" (same uppercase tracking style as other labels)
- Custom tag-input component (inline in the panel): displays current tags as removable chips + a text input that autocompletes from `get_all_task_tags()`
- On tag add/remove: calls `set_task_field('_user_tags', newCommaString)`
- `_user_tags` format: comma-separated, e.g. `"bug,urgent,frontend"`

### Panel footer
Fixed at bottom of panel (below scrollable content, `flex-shrink-0`):
```html
<div class="px-4 py-2 border-t border-outline-gray-1 text-xs text-ink-gray-4 flex gap-3">
  <span>Created by <strong>{{ task.doc.owner }}</strong></span>
  <span>{{ formatDate(task.doc.creation) }}</span>
</div>
```
`task.doc.owner` and `task.doc.creation` are standard Frappe fields, always present.

---

## 5. TaskDetail.vue (Full-page) Additions

- **Tags field**: same tag-input component, inserted after the Team row in the form. Saves via the existing `saveTask()` call (add `_user_tags` to the fields map).
- **Created-by footer**: same footer pattern, below the subtasks section, before the end of the scrollable area.
- **Copy button**: already exists in this view — no change needed.

---

## 6. New Shared Component: `TagInput.vue`

A small reusable component (`desk/src/components/TagInput.vue`) used in both the panel and full-page view:

**Props**: `modelValue: string` (comma-separated tags), `allTags: string[]` (autocomplete source)  
**Emits**: `update:modelValue`  
**Behaviour**:
- Splits `modelValue` by comma → array of current tags
- Renders each as a chip with × remove button
- Text input: on typing, filters `allTags` for matches, shows dropdown
- Enter or click on suggestion: adds tag (deduped), clears input
- On any change: emits updated comma-string

---

## 7. Files Modified / Created

| File | Change |
|------|--------|
| `helpdesk/helpdesk/doctype/hd_task/hd_task.json` | Add `track_changes: 1` |
| `helpdesk/helpdesk/doctype/hd_task/hd_task.py` | Add `_user_tags` to ALLOWED_FIELDS; add `get_all_task_tags()`, `search_tasks()` |
| `desk/src/pages/tasks/KanbanView.vue` | Filter bar, card redesign (avatar, priority bars, relative dates, tags) |
| `desk/src/pages/tasks/KanbanTaskPanel.vue` | Copy button, tags field, footer |
| `desk/src/pages/tasks/TaskDetail.vue` | Tags field, footer |
| `desk/src/components/TagInput.vue` | New shared tag-input component |

---

## Out of Scope (v1)

- Tag colors manually set by user (auto-hashed is sufficient)
- URL-persisted filter state
- History timeline in the panel (use Frappe's built-in version log on full-page)
- Multiple assignees per task
