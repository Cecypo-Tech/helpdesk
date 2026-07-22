# WA Line per-agent unread state Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the WA Line (Baileys) unread badge — conversation list, sidebar, and per-ticket tab — per-agent, so one agent opening a chat no longer clears the badge for every other agent.

**Architecture:** Replace the single global `WA Message.is_read` boolean with a new `WA Conversation Read State` doctype holding one row per (agent, JID) — a "read up to this timestamp" cursor. All four unread-count read sites in `helpdesk/integrations/wa.py` switch from `is_read = 0` filters to `message.creation > cursor.last_read` (scoped to `frappe.session.user`); the two mark-read endpoints switch from bulk-flipping message rows to upserting one cursor row.

**Tech Stack:** Frappe framework (Python backend, MariaDB), no frontend changes — all touched endpoints keep their existing response shapes.

## Global Constraints

- Full design source of truth: `docs/superpowers/specs/2026-07-22-wa-per-user-unread-state-design.md`.
- Site for all commands: `dev.localhost`.
- Scope is WA Line (Baileys) only. The WABA (`WhatsApp Message.status`) unread badge has the same underlying bug but is explicitly out of scope — do not touch it.
- No frontend (`.vue`) changes — every touched endpoint's request/response shape is unchanged.
- On rollout, every agent must start with zero backlog (no false "unread" appears for pre-existing messages) — this is what the migration patch and the historical-sync-job fix both exist to guarantee.
- Follow existing file conventions exactly: `helpdesk/integrations/wa.py` is 4-space indented **except** the body of `_run_sync_old_messages_job` (~L3695-3783), which is tab-indented. Preserve whichever indentation the surrounding code already uses in each edit.

---

### Task 1: `WA Conversation Read State` doctype

**Files:**
- Create: `helpdesk/helpdesk/doctype/wa_conversation_read_state/__init__.py`
- Create: `helpdesk/helpdesk/doctype/wa_conversation_read_state/wa_conversation_read_state.json`
- Create: `helpdesk/helpdesk/doctype/wa_conversation_read_state/wa_conversation_read_state.py`

**Interfaces:**
- Produces: DocType `WA Conversation Read State` with fields `user` (Link User, required), `jid` (Data, required), `last_read` (Datetime, required). Table name `tabWA Conversation Read State`. Python class `WAConversationReadState`.

- [ ] **Step 1: Create the package `__init__.py`**

Empty file:

```python
```

- [ ] **Step 2: Create the doctype JSON**

Modeled on `helpdesk/helpdesk/doctype/wa_message/wa_message.json`'s permission block (same app, same access pattern: agents read/write their own data, System Manager has full control).

```json
{
 "actions": [],
 "creation": "2026-07-22 00:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": [
  "user",
  "jid",
  "last_read"
 ],
 "fields": [
  {
   "fieldname": "user",
   "fieldtype": "Link",
   "in_list_view": 1,
   "label": "User",
   "options": "User",
   "reqd": 1,
   "search_index": 1
  },
  {
   "fieldname": "jid",
   "fieldtype": "Data",
   "in_list_view": 1,
   "label": "JID",
   "reqd": 1,
   "search_index": 1
  },
  {
   "fieldname": "last_read",
   "fieldtype": "Datetime",
   "in_list_view": 1,
   "label": "Last Read",
   "reqd": 1
  }
 ],
 "links": [],
 "modified": "2026-07-22 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "Helpdesk",
 "name": "WA Conversation Read State",
 "owner": "Administrator",
 "permissions": [
  {
   "read": 1,
   "role": "Agent",
   "write": 1
  },
  {
   "create": 1,
   "delete": 1,
   "read": 1,
   "role": "System Manager",
   "write": 1
  }
 ],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

- [ ] **Step 3: Create the controller**

```python
from frappe.model.document import Document


class WAConversationReadState(Document):
	pass
```

- [ ] **Step 4: Migrate and verify the table exists**

Run: `bench --site dev.localhost migrate`
Expected: no errors; migrate output includes syncing `WA Conversation Read State`.

Run: `bench --site dev.localhost mariadb -e "DESCRIBE \`tabWA Conversation Read State\`;"`
Expected: columns `user`, `jid`, `last_read` listed alongside the standard Frappe metadata columns (`name`, `creation`, `modified`, etc.)

- [ ] **Step 5: Commit**

```bash
git add helpdesk/helpdesk/doctype/wa_conversation_read_state/
git commit -m "feat(wa): add WA Conversation Read State doctype"
```

---

### Task 2: Cursor helpers + wire into the conversation-list badge and `mark_wa_messages_read`

This is the task that fixes the exact bug reported: two agents, one JID, one agent marks read, the other agent's badge must be unaffected.

**Files:**
- Modify: `helpdesk/integrations/wa.py`
- Test: `helpdesk/tests/test_wa_unread_state.py`

**Interfaces:**
- Consumes: doctype `WA Conversation Read State` (Task 1).
- Produces (used by Task 3; Task 4 adds one more helper of its own):
  - `_mark_conversation_read_for_user(jid: str, user: str | None = None, upto=None) -> None` — upserts one cursor row. `user` defaults to `frappe.session.user`; `upto` defaults to `now_datetime()`.
  - `_unread_counts_for_user(jids: list[str], user: str) -> dict[str, int]` — `{jid: unread_count}` for the given user, incoming messages only.

- [ ] **Step 1: Write the failing test**

Create `helpdesk/tests/test_wa_unread_state.py`:

```python
import frappe
from frappe.tests.utils import FrappeTestCase


class TestWAUnreadState(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.agent_a = self._make_agent("wa-unread-test-a@example.com")
		self.agent_b = self._make_agent("wa-unread-test-b@example.com")

	def _make_agent(self, email):
		if not frappe.db.exists("User", email):
			user = frappe.get_doc({
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
			}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "User", email, ignore_permissions=True, force=True)
		else:
			user = frappe.get_doc("User", email)
		user.add_roles("Agent")
		if not frappe.db.exists("HD Agent", {"user": email}):
			agent = frappe.get_doc({
				"doctype": "HD Agent",
				"user": email,
				"agent_name": email,
			}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "HD Agent", agent.name, ignore_permissions=True, force=True)
		return email

	def _make_message(self, jid, message="hello", line=""):
		doc = frappe.get_doc({
			"doctype": "WA Message",
			"jid": jid,
			"direction": "Incoming",
			"message": message,
			"content_type": "text",
			"message_id": frappe.generate_hash(length=10),
			"sender_name": "Test Sender",
			"sender_jid": jid,
			"status": "Delivered",
			"line": line,
		})
		doc.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "WA Message", doc.name, ignore_permissions=True, force=True)
		return doc

	def test_mark_read_does_not_clear_other_agents_badge(self):
		from helpdesk.integrations.wa import get_wa_conversations, mark_wa_messages_read

		jid = "111unreadtest@s.whatsapp.net"
		self._make_message(jid, "first")
		self._make_message(jid, "second")

		frappe.set_user(self.agent_a)
		mark_wa_messages_read(jid=jid)

		def unread_count_for(jid):
			convs = get_wa_conversations(line="")
			match = [c for c in convs if c["jid"] == jid]
			return match[0]["unread_count"] if match else None

		self.assertEqual(unread_count_for(jid), 0)

		frappe.set_user(self.agent_b)
		self.assertEqual(unread_count_for(jid), 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_unread_state`
Expected: FAIL — agent B also sees `0` (current global `is_read` behavior), not `2`.

- [ ] **Step 3: Add the cursor helpers**

In `helpdesk/integrations/wa.py`, insert immediately before the `@frappe.whitelist()` line that precedes `def get_wa_conversations` (currently line 1747):

```python
def _mark_conversation_read_for_user(jid: str, user: str | None = None, upto=None) -> None:
    """Upsert one agent's read cursor for a JID to `upto` (default: now)."""
    user = user or frappe.session.user
    upto = upto or now_datetime()
    existing = frappe.db.get_value("WA Conversation Read State", {"user": user, "jid": jid}, "name")
    if existing:
        frappe.db.set_value("WA Conversation Read State", existing, "last_read", upto, update_modified=False)
    else:
        frappe.get_doc({
            "doctype": "WA Conversation Read State",
            "user": user,
            "jid": jid,
            "last_read": upto,
        }).insert(ignore_permissions=True)


def _unread_counts_for_user(jids: list[str], user: str) -> dict[str, int]:
    """{jid: unread incoming message count} for one agent, scoped to `jids`."""
    if not jids:
        return {}
    rows = frappe.db.sql(
        """
        SELECT m.jid, COUNT(*) AS cnt
        FROM `tabWA Message` m
        LEFT JOIN `tabWA Conversation Read State` r
          ON r.jid = m.jid AND r.user = %(user)s
        WHERE m.direction = 'Incoming' AND m.jid IN %(jids)s
          AND (r.last_read IS NULL OR m.creation > r.last_read)
        GROUP BY m.jid
        """,
        {"jids": tuple(jids), "user": user},
        as_dict=True,
    )
    return {row.jid: row.cnt for row in rows}


```

- [ ] **Step 4: Wire the helper into `get_wa_conversations`**

In `helpdesk/integrations/wa.py`, replace the block currently at lines 1821-1834:

```python
    # Unread incoming message counts per JID, from the same is_read flag mark_wa_messages_read maintains.
    unread_counts: dict[str, int] = {}
    if jids:
        for row in frappe.db.sql(
            """
            SELECT jid, COUNT(*) AS cnt
            FROM `tabWA Message`
            WHERE direction = 'Incoming' AND is_read = 0 AND jid IN %(jids)s
            GROUP BY jid
            """,
            {"jids": tuple(jids)},
            as_dict=True,
        ):
            unread_counts[row.jid] = row.cnt
```

with:

```python
    # Unread incoming message counts per JID, scoped to the requesting agent
    # via WA Conversation Read State (per-agent cursor, not a shared flag).
    unread_counts = _unread_counts_for_user(jids, frappe.session.user)
```

- [ ] **Step 5: Wire the helper into `mark_wa_messages_read`'s Baileys branch**

Replace, in `mark_wa_messages_read` (currently lines 1896-1902):

```python
    if jid:
        # Baileys path
        filters: dict = {"jid": jid, "direction": "Incoming", "is_read": 0}
        unread = frappe.get_all("WA Message", filters=filters, fields=["name"])
        for row in unread:
            frappe.db.set_value("WA Message", row.name, "is_read", 1, update_modified=False)
        if unread:
            frappe.db.commit()
        return len(unread)
```

with:

```python
    if jid:
        # Baileys path
        count = _unread_counts_for_user([jid], frappe.session.user).get(jid, 0)
        _mark_conversation_read_for_user(jid)
        frappe.db.commit()
        return count
```

- [ ] **Step 6: Run test to verify it passes**

Run: `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_unread_state`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add helpdesk/integrations/wa.py helpdesk/tests/test_wa_unread_state.py
git commit -m "fix(wa): per-agent unread cursor for conversation list badge"
```

---

### Task 3: Wire the cursor into the sidebar, ticket-tab badge, and "mark all read"

**Files:**
- Modify: `helpdesk/integrations/wa.py`
- Test: `helpdesk/tests/test_wa_unread_state.py`

**Interfaces:**
- Consumes: `_mark_conversation_read_for_user`, `_unread_counts_for_user` (Task 2).

- [ ] **Step 1: Write the failing test**

Add to `TestWAUnreadState` in `helpdesk/tests/test_wa_unread_state.py`:

```python
	def test_ticket_badge_and_mark_all_are_per_agent(self):
		from helpdesk.integrations.wa import (
			get_ticket_wa_unread_count,
			get_wa_lines,
			mark_all_wa_messages_read,
		)

		line_name = "wa-unread-test-line"
		if not frappe.db.exists("WA Line", line_name):
			frappe.get_doc({"doctype": "WA Line", "instance_name": line_name}).insert(ignore_permissions=True)
			self.addCleanup(frappe.delete_doc, "WA Line", line_name, ignore_permissions=True, force=True)

		jid = "222unreadtest@s.whatsapp.net"
		self._make_message(jid, "one", line=line_name)
		self._make_message(jid, "two", line=line_name)
		self._make_message(jid, "three", line=line_name)

		ticket = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": "WA unread test ticket",
			"raised_by": "wa-unread-ticket-test@example.com",
			"baileys_jid": jid,
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "HD Ticket", ticket.name, ignore_permissions=True, force=True)

		def sidebar_unread():
			rows = [l for l in get_wa_lines() if l["name"] == line_name]
			return rows[0]["unread"] if rows else None

		frappe.set_user(self.agent_a)
		self.assertEqual(get_ticket_wa_unread_count(ticket.name), 3)
		self.assertEqual(sidebar_unread(), 3)
		marked = mark_all_wa_messages_read(line=line_name)
		self.assertEqual(marked, 3)
		self.assertEqual(get_ticket_wa_unread_count(ticket.name), 0)
		self.assertEqual(sidebar_unread(), 0)

		frappe.set_user(self.agent_b)
		self.assertEqual(get_ticket_wa_unread_count(ticket.name), 3)
		self.assertEqual(sidebar_unread(), 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_unread_state`
Expected: FAIL on agent B's assertions (both return `0` instead of `3`) — agent A's bulk `mark_all_wa_messages_read` call still flips the global `is_read` flag, which agent B's reads still key off.

- [ ] **Step 3: Wire `get_wa_lines`**

Replace, in `get_wa_lines` (currently lines 1736-1741):

```python
        line["unread"] = (
            frappe.db.count(
                "WA Message",
                {"line": line["name"], "direction": "Incoming", "is_read": 0},
            )
            if wa_msg_exists
            else 0
        )
```

with:

```python
        line["unread"] = (
            (frappe.db.sql(
                """
                SELECT COUNT(*)
                FROM `tabWA Message` m
                LEFT JOIN `tabWA Conversation Read State` r
                  ON r.jid = m.jid AND r.user = %(user)s
                WHERE m.direction = 'Incoming' AND m.line = %(line)s
                  AND (r.last_read IS NULL OR m.creation > r.last_read)
                """,
                {"line": line["name"], "user": frappe.session.user},
            ) or [[0]])[0][0]
            if wa_msg_exists
            else 0
        )
```

- [ ] **Step 4: Wire `get_ticket_wa_unread_count`**

Replace, in `get_ticket_wa_unread_count` (currently line 1953):

```python
        return frappe.db.count("WA Message", {"jid": jid, "direction": "Incoming", "is_read": 0})
```

with:

```python
        return _unread_counts_for_user([jid], frappe.session.user).get(jid, 0)
```

- [ ] **Step 5: Wire `mark_all_wa_messages_read`**

Replace the full body of `mark_all_wa_messages_read` (currently lines 1969-1979):

```python
@frappe.whitelist()
def mark_all_wa_messages_read(line: str) -> int:
    """Mark all unread incoming Baileys Messages for an entire line as read."""
    if not line:
        return 0
    filters: dict = {"line": line, "direction": "Incoming", "is_read": 0}
    unread = frappe.get_all("WA Message", filters=filters, fields=["name"])
    for row in unread:
        frappe.db.set_value("WA Message", row.name, "is_read", 1, update_modified=False)
    if unread:
        frappe.db.commit()
    return len(unread)
```

with:

```python
@frappe.whitelist()
def mark_all_wa_messages_read(line: str) -> int:
    """Mark all unread incoming Baileys Messages for an entire line as read, for the current agent."""
    if not line:
        return 0
    jids = frappe.get_all(
        "WA Message",
        filters={"line": line, "direction": "Incoming"},
        pluck="jid",
        distinct=True,
    )
    if not jids:
        return 0
    counts = _unread_counts_for_user(jids, frappe.session.user)
    total = sum(counts.values())
    upto = now_datetime()
    for jid in jids:
        _mark_conversation_read_for_user(jid, upto=upto)
    frappe.db.commit()
    return total
```

- [ ] **Step 6: Run test to verify it passes**

Run: `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_unread_state`
Expected: PASS (both tests)

- [ ] **Step 7: Commit**

```bash
git add helpdesk/integrations/wa.py helpdesk/tests/test_wa_unread_state.py
git commit -m "fix(wa): per-agent unread cursor for sidebar, ticket badge, and mark-all-read"
```

---

### Task 4: Historical sync job must not create phantom unread for anyone

`_run_sync_old_messages_job` currently inserts imported historical messages with `is_read: 1` so a backfill doesn't manufacture unread badges. Under the cursor model, the equivalent is advancing every agent's cursor for each JID touched by the import.

**Files:**
- Modify: `helpdesk/integrations/wa.py`
- Test: `helpdesk/tests/test_wa_unread_state.py`

**Interfaces:**
- Consumes: `_mark_conversation_read_for_user` (Task 2).
- Produces: `_mark_conversation_read_for_all_agents(jid: str, upto) -> None` — advances every `HD Agent.user`'s cursor for a JID to `upto`.

- [ ] **Step 1: Write the failing test**

Add to `TestWAUnreadState`:

```python
	def test_historical_sync_does_not_create_unread_for_anyone(self):
		from helpdesk.integrations.wa import _mark_conversation_read_for_all_agents, get_wa_conversations

		jid = "333unreadtest@g.us"
		self._make_message(jid, "imported old message")
		_mark_conversation_read_for_all_agents(jid, upto=frappe.utils.now_datetime())

		for user in (self.agent_a, self.agent_b):
			frappe.set_user(user)
			convs = get_wa_conversations(line="")
			match = [c for c in convs if c["jid"] == jid]
			self.assertEqual(match[0]["unread_count"] if match else 0, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_unread_state`
Expected: FAIL with `ImportError: cannot import name '_mark_conversation_read_for_all_agents'` — the helper doesn't exist yet.

- [ ] **Step 3: Add the helper**

In `helpdesk/integrations/wa.py`, add immediately after `_mark_conversation_read_for_user` (defined in Task 2, just above `_unread_counts_for_user`):

```python
def _mark_conversation_read_for_all_agents(jid: str, upto) -> None:
    """Advance every agent's cursor for a JID — used for historical/backfilled imports
    that shouldn't appear as unread for anyone."""
    for agent_user in frappe.get_all("HD Agent", pluck="user"):
        _mark_conversation_read_for_user(jid, user=agent_user, upto=upto)


```

- [ ] **Step 4: Run test to verify it passes**

Run: `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_unread_state`
Expected: PASS

- [ ] **Step 5: Wire the sync job**

In `helpdesk/integrations/wa.py`, this function is **tab-indented** — match it exactly.

Add a tracking set right after `imported = skipped = 0` (~line 3706):

```
		imported = skipped = 0
		touched_jids: set[str] = set()
```

Add to the tracking set on successful insert, right after `imported += 1` (~line 3765) inside the `try:` block:

```
					imported += 1
					touched_jids.add(remote_jid)
```

Remove the `"is_read": 1,` line from the message dict being inserted (~line 3758, inside the same `try:` block) — delete it entirely, since the field no longer exists after Task 6's cleanup and this cursor-based approach replaces it:

```
						"status": "Read" if from_me else "Delivered",
						"line": line_doc.name,
```

(i.e. the line `"is_read": 1,` that currently follows `"line": line_doc.name,` is deleted, not replaced.)

After the `while current_page <= total_pages:` loop ends, before `frappe.publish_realtime(` (~line 3774), add:

```
		if touched_jids:
			now = now_datetime()
			for touched_jid in touched_jids:
				_mark_conversation_read_for_all_agents(touched_jid, upto=now)
```

- [ ] **Step 6: Run the full test module**

Run: `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_unread_state`
Expected: PASS (all tests; the sync job's `while` loop itself has no local test double for the Evolution API call it makes, so it isn't exercised end-to-end — the helper it now calls was already proven correct in Steps 1-4 above)

- [ ] **Step 7: Commit**

```bash
git add helpdesk/integrations/wa.py
git commit -m "fix(wa): advance every agent's read cursor after historical message import"
```

---

### Task 5: Migration patch — every agent starts caught up

**Files:**
- Create: `helpdesk/patches/add_wa_conversation_read_state.py`
- Modify: `helpdesk/patches.txt`

**Interfaces:**
- Consumes: doctype `WA Conversation Read State` (Task 1), doctype `HD Agent`, `_mark_conversation_read_for_all_agents` (Task 4).

- [ ] **Step 1: Write the patch**

Reuses `_mark_conversation_read_for_all_agents` from Task 4 rather than re-implementing the same per-agent upsert — it's already idempotent (upsert, not insert-if-absent), so rerunning this patch is safe and simply re-confirms every agent is caught up.

```python
import frappe

from helpdesk.integrations.wa import _mark_conversation_read_for_all_agents


def execute():
	"""Backfill WA Conversation Read State so every agent starts caught up.

	Without this, agents would see every pre-existing incoming WA Line message
	as unread the moment this feature ships, since no cursor row exists yet
	and the default treats "no row" as "unread from the start."
	"""
	if not frappe.db.table_exists("WA Message") or not frappe.db.table_exists("WA Conversation Read State"):
		return

	if not frappe.get_all("HD Agent", limit=1):
		return

	jid_max_creation = frappe.db.sql(
		"""
		SELECT jid, MAX(creation) AS latest
		FROM `tabWA Message`
		WHERE direction = 'Incoming'
		GROUP BY jid
		""",
		as_dict=True,
	)

	for row in jid_max_creation:
		_mark_conversation_read_for_all_agents(row.jid, upto=row.latest)

	frappe.db.commit()
```

- [ ] **Step 2: Register the patch**

In `helpdesk/patches.txt`, append after the last line (`helpdesk.patches.backfill_wa_thumbnails`):

```
helpdesk.patches.add_wa_conversation_read_state
```

- [ ] **Step 3: Run the migration and verify**

Run: `bench --site dev.localhost migrate`
Expected: patch runs without error; log shows `Migrating helpdesk` including the new patch name.

Run: `bench --site dev.localhost mariadb -e "SELECT COUNT(*) FROM \`tabWA Conversation Read State\`;"`
Expected: a row count roughly equal to `(distinct JIDs with incoming messages) × (number of HD Agents)` — nonzero if any WA Line data exists on this site.

- [ ] **Step 4: Commit**

```bash
git add helpdesk/patches/add_wa_conversation_read_state.py helpdesk/patches.txt
git commit -m "feat(wa): migrate existing agents to caught-up read cursors"
```

---

### Task 6: Remove the dead `WA Message.is_read` field

By this point nothing reads or writes `is_read` except the four remaining message-insert sites below (all in code paths untouched by earlier tasks) and the field definition itself.

**Files:**
- Modify: `helpdesk/helpdesk/doctype/wa_message/wa_message.json`
- Modify: `helpdesk/integrations/wa.py`

- [ ] **Step 1: Confirm nothing else references `is_read`**

Run: `grep -n "is_read" helpdesk/integrations/wa.py`
Expected output: exactly four lines remain — the outgoing-message-insert sites (~L1071, ~L1429, ~L1530) and the incoming-message-insert site (~L1110). The sync-job site was already removed in Task 4. Steps 3-4 below remove all four.

- [ ] **Step 2: Remove the field from the doctype**

In `helpdesk/helpdesk/doctype/wa_message/wa_message.json`, remove `"is_read",` from the `field_order` array, and remove this whole object from the `fields` array:

```json
  {
   "default": "0",
   "fieldname": "is_read",
   "fieldtype": "Check",
   "label": "Is Read",
   "read_only": 1
  },
```

- [ ] **Step 3: Remove the three remaining dead writes in `wa.py`**

Each of these is a line `"is_read": 1,` inside a `frappe.get_doc({...})` dict for an **Outgoing** message insert. Delete each line (context shown so each is unambiguous):

At ~L1071 (inside `_handle_upsert`'s "mirrored from phone" branch):
```python
				"reference_doctype": "",
				"reference_name": "",
				"line": line.name,
				"is_read": 1,
			}).insert(ignore_permissions=True)
```
becomes:
```python
				"reference_doctype": "",
				"reference_name": "",
				"line": line.name,
			}).insert(ignore_permissions=True)
```

At ~L1429 (send-message path):
```python
        "reference_doctype": "HD Ticket" if ticket else "",
        "reference_name": ticket or "",
        "line": line.name,
        "is_read": 1,
    })
    msg_doc.insert(ignore_permissions=True)
```
becomes:
```python
        "reference_doctype": "HD Ticket" if ticket else "",
        "reference_name": ticket or "",
        "line": line.name,
    })
    msg_doc.insert(ignore_permissions=True)
```

At ~L1530 (send-reaction path):
```python
        "reply_to_message_id": target_message_id,
        "line": line.name,
        "is_read": 1,
    }).insert(ignore_permissions=True)
```
becomes:
```python
        "reply_to_message_id": target_message_id,
        "line": line.name,
    }).insert(ignore_permissions=True)
```

- [ ] **Step 4: Remove the dead write on the incoming-message insert**

At ~L1110, `"is_read": 0,` on the **Incoming** message insert is also dead (nothing reads it anymore — unread state now comes entirely from the cursor table). Delete that line too:

```python
			"status": "Pending",
			"reference_doctype": "HD Ticket" if _ticket_ref else "",
			"reference_name": _ticket_ref,
			"line": line.name,
			"is_read": 0,
		}).insert(ignore_permissions=True)
```
becomes:
```python
			"status": "Pending",
			"reference_doctype": "HD Ticket" if _ticket_ref else "",
			"reference_name": _ticket_ref,
			"line": line.name,
		}).insert(ignore_permissions=True)
```

- [ ] **Step 5: Migrate and run the full test suite**

Run: `bench --site dev.localhost migrate`
Expected: no errors; `is_read` column dropped from `tabWA Message`.

Run: `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_wa_unread_state`
Expected: PASS (all tests)

Run: `bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_baileys_standalone`
Expected: no new failures caused by this change (pre-existing failures in that module, if any, are unrelated — see note below)

- [ ] **Step 6: Commit**

```bash
git add helpdesk/helpdesk/doctype/wa_message/wa_message.json helpdesk/integrations/wa.py
git commit -m "chore(wa): drop dead WA Message.is_read field"
```

---

## Notes for the implementer

- `helpdesk/tests/test_baileys_standalone.py` imports `get_baileys_conversations` / `get_baileys_messages` from `helpdesk.integrations.baileys`, but that module is now just a compatibility shim re-exporting `webhook` from `wa.py` (see its docstring). Those two functions don't exist there anymore under those names — that test file appears to already be broken independent of this work. Don't fix it as part of this plan (out of scope); just confirm this plan's changes don't add any *new* failures there.
- Every task after Task 1 is additive/replacement within `helpdesk/integrations/wa.py` — no task depends on frontend code, and no `.vue` file should be touched anywhere in this plan.
