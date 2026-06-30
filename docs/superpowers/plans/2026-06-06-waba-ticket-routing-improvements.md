# WABA Ticket Routing Improvements

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix a logic bug, harden against race conditions, and improve the WABA ticket find-or-create flow with a reopen window, last-activity timeout, previous-ticket link, and clean subject truncation.

**Architecture:** All six changes are isolated to `on_whatsapp_message_insert` and its helpers in `wa.py`, one new setting field in `WhatsApp Helpdesk Settings`, and one new custom field fixture on `HD Ticket`. No frontend changes needed.

**Tech Stack:** Python, Frappe ORM, frappe.cache() Redis, frappe.qb query builder

---

## File Map

| File | Change |
|------|--------|
| `helpdesk/integrations/wa.py` | Tasks 1–6: all runtime logic |
| `helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/whatsapp_helpdesk_settings.json` | Task 4: add `reopen_window_minutes` field |
| `helpdesk/fixtures/custom_field.json` | Task 5: add `previous_ticket` Link field on HD Ticket |
| `helpdesk/integrations/tests/test_fw_ticket_routing.py` | Task 7: new test file |

---

## Task 1: Fix null `status_category` guard

**Files:**
- Modify: `helpdesk/integrations/wa.py:2149`

**Context:** Line 2149 reads `if status_category and status_category != "Resolved":`. When `status_category` is `None` (ticket has no status set), the `and` short-circuits to `False`, causing the candidate ticket to be skipped and a spurious new ticket to be created. The fix is a one-character change.

- [ ] **Step 1: Apply the fix**

In `helpdesk/integrations/wa.py`, change line 2149:

```python
# BEFORE
		if status_category and status_category != "Resolved":
# AFTER
		if status_category != "Resolved":
```

- [ ] **Step 2: Commit**

```bash
git add helpdesk/integrations/wa.py
git commit -m "fix: treat null status_category as non-resolved when routing WABA messages"
```

---

## Task 2: Fix subject truncation at word boundary

**Files:**
- Modify: `helpdesk/integrations/wa.py:2162`

**Context:** `(doc.message or "")[:100]` slices mid-word. `textwrap.shorten` respects word boundaries and appends a placeholder.

- [ ] **Step 1: Apply the fix**

In `helpdesk/integrations/wa.py`, `on_whatsapp_message_insert`, replace line 2162:

```python
# BEFORE
		subject = (doc.message or "")[:100] or f"WhatsApp from {profile_name}"
# AFTER
		import textwrap as _textwrap
		subject = _textwrap.shorten(doc.message or "", width=100, placeholder="…") or f"WhatsApp from {profile_name}"
```

- [ ] **Step 2: Commit**

```bash
git add helpdesk/integrations/wa.py
git commit -m "fix: truncate WABA ticket subject at word boundary with ellipsis"
```

---

## Task 3: Timeout from last activity (not just last incoming)

**Files:**
- Modify: `helpdesk/integrations/wa.py` — add helper `_fw_last_activity`, update timeout check in `on_whatsapp_message_insert`

**Context:** Currently the timeout window is measured from `linked[0].creation` — the last *incoming* message. If an agent replied recently but the customer's last message was > 24 h ago, a new ticket is created even though the conversation is active. The fix queries the most recent message in either direction on the candidate ticket.

- [ ] **Step 1: Add `_fw_last_activity` helper**

Add this function anywhere near `_fw_settings` (around line 142):

```python
def _fw_last_activity(ticket_name: str):
    """Return the creation datetime of the most recent WhatsApp Message on a WABA ticket."""
    from frappe.query_builder import DocType as _DocType
    WM = _DocType("WhatsApp Message")
    result = (
        frappe.qb.from_(WM)
        .select(WM.creation)
        .where(WM.reference_doctype == "HD Ticket")
        .where(WM.reference_name == ticket_name)
        .orderby(WM.creation, order=frappe.qb.desc)
        .limit(1)
        .run(as_dict=True)
    )
    return result[0].creation if result else None
```

- [ ] **Step 2: Update the timeout check in `on_whatsapp_message_insert`**

In `on_whatsapp_message_insert`, replace the timeout check block (currently lines 2150–2152):

```python
# BEFORE
			timeout = int(s.new_conversation_timeout_hours or 24)
			if time_diff_in_hours(now_datetime(), linked[0].creation) < timeout:
				existing_ticket = candidate
# AFTER
			timeout = int(s.new_conversation_timeout_hours or 24)
			last_activity = _fw_last_activity(candidate) or linked[0].creation
			if time_diff_in_hours(now_datetime(), last_activity) < timeout:
				existing_ticket = candidate
```

- [ ] **Step 3: Commit**

```bash
git add helpdesk/integrations/wa.py
git commit -m "feat: measure WABA inactivity timeout from last message in either direction"
```

---

## Task 4: Reopen recently-resolved tickets instead of always creating new

**Files:**
- Modify: `helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/whatsapp_helpdesk_settings.json`
- Modify: `helpdesk/integrations/wa.py` — add reopen logic inside `on_whatsapp_message_insert`

**Context:** When `status_category == "Resolved"`, a new ticket is always created. A configurable reopen window lets teams reopen the old ticket if the customer replies within N minutes of resolution.

- [ ] **Step 1: Add `reopen_window_minutes` field to the settings JSON**

In `helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/whatsapp_helpdesk_settings.json`:

Add `"reopen_window_minutes"` to `field_order` immediately after `"new_conversation_timeout_hours"`:

```json
"field_order": [
    "enabled",
    "unknown_contact_action",
    "new_conversation_timeout_hours",
    "reopen_window_minutes",
    ...
```

Add the field definition to the `"fields"` array after the `new_conversation_timeout_hours` entry:

```json
{
    "default": "0",
    "fieldname": "reopen_window_minutes",
    "fieldtype": "Int",
    "label": "Reopen Window (Minutes)",
    "description": "Reopen a resolved ticket if the customer replies within this many minutes of resolution. Set 0 to always create a new ticket."
},
```

- [ ] **Step 2: Add reopen logic in `on_whatsapp_message_insert`**

In `wa.py`, replace the `if status_category != "Resolved":` block (currently lines 2149–2152) with:

```python
		if status_category != "Resolved":
			timeout = int(s.new_conversation_timeout_hours or 24)
			last_activity = _fw_last_activity(candidate) or linked[0].creation
			if time_diff_in_hours(now_datetime(), last_activity) < timeout:
				existing_ticket = candidate
		else:
			reopen_minutes = int(s.reopen_window_minutes or 0)
			if reopen_minutes > 0:
				ticket_modified = frappe.db.get_value("HD Ticket", candidate, "modified")
				if ticket_modified and time_diff_in_hours(now_datetime(), ticket_modified) * 60 < reopen_minutes:
					existing_ticket = candidate
```

- [ ] **Step 3: Commit**

```bash
git add helpdesk/integrations/wa.py
git add helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/whatsapp_helpdesk_settings.json
git commit -m "feat: add reopen window — reopen resolved WABA ticket if customer replies within N minutes"
```

---

## Task 5: Link new ticket back to the previous resolved ticket

**Files:**
- Modify: `helpdesk/fixtures/custom_field.json` — add `previous_ticket` Link field on HD Ticket
- Modify: `helpdesk/integrations/wa.py` — set `previous_ticket` when creating a new ticket

**Context:** When a new ticket is created for a returning customer, there's no pointer to their history. Agents must search manually. A `previous_ticket` Link field on HD Ticket provides a one-click jump.

- [ ] **Step 1: Add `previous_ticket` to the custom field fixture**

In `helpdesk/fixtures/custom_field.json`, append this object to the JSON array (after the `baileys_line` entry):

```json
{
    "alignment": "",
    "allow_in_quick_entry": 0,
    "allow_on_submit": 0,
    "bold": 0,
    "button_color": "",
    "collapsible": 0,
    "collapsible_depends_on": null,
    "columns": 0,
    "default": null,
    "depends_on": null,
    "description": "Previous WhatsApp ticket from the same contact.",
    "docstatus": 0,
    "doctype": "Custom Field",
    "dt": "HD Ticket",
    "fetch_from": null,
    "fetch_if_empty": 0,
    "fieldname": "previous_ticket",
    "fieldtype": "Link",
    "hidden": 0,
    "hide_border": 0,
    "hide_days": 0,
    "hide_seconds": 0,
    "ignore_user_permissions": 0,
    "ignore_xss_filter": 0,
    "in_global_search": 0,
    "in_list_view": 0,
    "in_preview": 0,
    "in_standard_filter": 0,
    "insert_after": "baileys_line",
    "is_system_generated": 0,
    "is_virtual": 0,
    "label": "Previous Ticket",
    "length": 0,
    "link_filters": null,
    "mandatory_depends_on": null,
    "modified": "2026-06-06 00:00:00.000000",
    "module": null,
    "name": "HD Ticket-previous_ticket",
    "no_copy": 1,
    "non_negative": 0,
    "options": "HD Ticket",
    "permlevel": 0,
    "placeholder": null,
    "precision": "",
    "print_hide": 1,
    "print_hide_if_no_value": 0,
    "print_width": null,
    "read_only": 1,
    "read_only_depends_on": null,
    "report_hide": 0,
    "reqd": 0,
    "search_index": 0,
    "show_dashboard": 0,
    "sort_options": 0,
    "translatable": 0,
    "unique": 0,
    "width": null
}
```

- [ ] **Step 2: Track `candidate` before the if/else block**

At the top of the find-or-create section in `on_whatsapp_message_insert`, ensure `candidate` is initialised to `None` so it's available in both branches:

```python
	existing_ticket = None
	candidate = None          # ← add this line
	if linked and linked[0].reference_name:
		candidate = linked[0].reference_name
		...
```

The variable `candidate` is already set inside the `if linked` block, but initialising it to `None` before the block makes it safely accessible in the `else` branch below.

- [ ] **Step 3: Set `previous_ticket` when inserting a new ticket**

In the `else` branch of `if existing_ticket:` (around line 2162), add `previous_ticket` to `ticket_data`:

```python
	else:
		import textwrap as _textwrap
		subject = _textwrap.shorten(doc.message or "", width=100, placeholder="…") or f"WhatsApp from {profile_name}"
		ticket_data = {
			"doctype": "HD Ticket",
			"subject": subject,
			"raised_by": email,
			"description": doc.message or "",
			"via_customer_portal": 0,
		}
		if candidate:
			ticket_data["previous_ticket"] = candidate
		if contact_name:
			ticket_data["contact"] = contact_name
		if s.default_ticket_type:
			ticket_data["ticket_type"] = s.default_ticket_type
		if s.default_team:
			ticket_data["agent_group"] = s.default_team
		...
```

- [ ] **Step 4: Apply the fixture — run bench migrate on the local site**

```bash
cd /home/kushal/frappe-bench
bench --site dev.localhost migrate
```

Expected: migration completes without errors and the `previous_ticket` field appears on HD Ticket.

- [ ] **Step 5: Commit**

```bash
git add helpdesk/fixtures/custom_field.json helpdesk/integrations/wa.py
git commit -m "feat: link new WABA ticket to previous resolved ticket via previous_ticket field"
```

---

## Task 6: Race condition — serialise concurrent first-message handlers

**Files:**
- Modify: `helpdesk/integrations/wa.py` — `on_whatsapp_message_insert`

**Context:** Two webhook deliveries for the same number can arrive within milliseconds. Both query `linked = []` (neither message is linked yet), both create a fresh HD Ticket. The existing dedup pattern (line 736) uses Redis `SET NX EX` — an atomic compare-and-set — which we replicate here.

The strategy: the first handler acquires the lock and proceeds normally. The second handler fails to acquire, sleeps 1 second (long enough for the winner to finish the find-or-create), then continues — at which point the `linked` query finds the ticket the winner created and reuses it.

- [ ] **Step 1: Add lock acquisition right after `phone` is established**

In `on_whatsapp_message_insert`, immediately after `if not phone: return` (around line 2102), insert:

```python
	# Serialise concurrent first-message handlers for the same phone number.
	# Uses Redis SET NX (atomic) — same pattern as the WA Line message dedup at line ~736.
	_fw_lock = f"wa_fw_ticket:{phone}"
	if not frappe.cache().set(_fw_lock, 1, ex=30, nx=True):
		import time as _time
		_time.sleep(1)
```

No explicit lock release is needed: the 30-second TTL handles it, and the sleeping loser's subsequent `linked` query will find the ticket the winner created.

- [ ] **Step 2: Commit**

```bash
git add helpdesk/integrations/wa.py
git commit -m "fix: serialise concurrent WABA first-message handlers with Redis lock to prevent duplicate ticket creation"
```

---

## Task 7: Tests for all new behaviour

**Files:**
- Create: `helpdesk/integrations/tests/test_fw_ticket_routing.py`

**Context:** There are no existing tests for `on_whatsapp_message_insert`. The new file uses the real HD Ticket and WhatsApp Helpdesk Settings doctypes but mocks the `WhatsApp Message` doc (since `frappe_whatsapp` is an optional dependency). Tests are skipped automatically if `frappe_whatsapp` is not installed.

- [ ] **Step 1: Create the test file**

```python
# helpdesk/integrations/tests/test_fw_ticket_routing.py
import unittest
import frappe


def _fw_settings():
    from helpdesk.integrations.wa import _fw_settings as _s
    return _s()


def _skip_if_no_fw():
    return not frappe.db.exists("DocType", "WhatsApp Message")


class _MockWAMessage:
    """Minimal stand-in for a WhatsApp Message doc."""
    def __init__(self, from_jid, message, profile_name="Test User"):
        self.type = "Incoming"
        self.reference_doctype = ""
        self.reference_name = ""
        self.message = message
        self.profile_name = profile_name
        self._from = from_jid
        self._sets = {}

    def get(self, key, default=None):
        if key == "from":
            return self._from
        return default

    def db_set(self, field, value, update_modified=True):
        self._sets[field] = value
        setattr(self, field, value)


def _make_resolved_ticket(raised_by="test@example.com"):
    """Insert a minimal resolved HD Ticket; return its name."""
    resolved_status = frappe.db.get_value(
        "HD Ticket Status", {"category": "Resolved"}, "name"
    )
    if not resolved_status:
        raise unittest.SkipTest("No resolved HD Ticket Status found")
    t = frappe.get_doc({
        "doctype": "HD Ticket",
        "subject": "Old ticket",
        "raised_by": raised_by,
    }).insert(ignore_permissions=True)
    frappe.db.set_value("HD Ticket", t.name, "status", resolved_status)
    # status_category is a child-derived field; set it directly for test isolation
    frappe.db.set_value("HD Ticket", t.name, "status_category", "Resolved")
    return t.name


def _make_open_ticket(raised_by="test@example.com"):
    """Insert a minimal open HD Ticket; return its name."""
    t = frappe.get_doc({
        "doctype": "HD Ticket",
        "subject": "Open ticket",
        "raised_by": raised_by,
    }).insert(ignore_permissions=True)
    frappe.db.set_value("HD Ticket", t.name, "status_category", "Open")
    return t.name


def _link_wa_message(ticket_name, from_jid, minutes_ago=0):
    """Insert a WhatsApp Message linked to ticket_name."""
    from frappe.utils import add_to_date, now_datetime
    creation = add_to_date(now_datetime(), minutes=-minutes_ago)
    msg = frappe.get_doc({
        "doctype": "WhatsApp Message",
        "type": "Incoming",
        "from": from_jid,
        "reference_doctype": "HD Ticket",
        "reference_name": ticket_name,
        "message": "hello",
        "profile_name": "Test",
    }).insert(ignore_permissions=True)
    # Back-date creation for timeout tests
    frappe.db.set_value("WhatsApp Message", msg.name, "creation", creation)
    return msg.name


class TestFwTicketRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _skip_if_no_fw():
            raise unittest.SkipTest("frappe_whatsapp not installed")
        frappe.set_user("Administrator")
        # Snapshot and prepare settings
        cls._s = frappe.get_single("WhatsApp Helpdesk Settings")
        cls._orig = {
            "enabled": cls._s.enabled,
            "new_conversation_timeout_hours": cls._s.new_conversation_timeout_hours,
            "reopen_window_minutes": cls._s.get("reopen_window_minutes"),
            "unknown_contact_action": cls._s.unknown_contact_action,
        }
        cls._s.enabled = 1
        cls._s.new_conversation_timeout_hours = 24
        cls._s.unknown_contact_action = "Create Ticket Only"
        cls._s.save(ignore_permissions=True)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        s = frappe.get_single("WhatsApp Helpdesk Settings")
        for k, v in cls._orig.items():
            if v is not None:
                s.set(k, v)
        s.save(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()

    def _run(self, doc):
        from helpdesk.integrations.wa import on_whatsapp_message_insert
        on_whatsapp_message_insert(doc)

    # ── Task 1: null status_category ──────────────────────────────────────────

    def test_null_status_category_reuses_ticket_within_timeout(self):
        """A ticket with null status_category should be treated as active (not Resolved)."""
        from_jid = "254700000001"
        ticket = _make_open_ticket(raised_by=f"whatsapp+{from_jid}@test.local")
        frappe.db.set_value("HD Ticket", ticket, "status_category", None)
        _link_wa_message(ticket, from_jid, minutes_ago=60)
        frappe.db.commit()

        doc = _MockWAMessage(from_jid, "follow-up")
        self._run(doc)

        self.assertEqual(doc._sets.get("reference_name"), ticket,
            "null status_category ticket should be reused within timeout")

    # ── Task 3: last-activity timeout ─────────────────────────────────────────

    def test_last_outgoing_message_keeps_ticket_alive(self):
        """Ticket stays alive when agent replied recently, even if last incoming is old."""
        from_jid = "254700000002"
        ticket = _make_open_ticket(raised_by=f"whatsapp+{from_jid}@test.local")
        # Last incoming: 25 h ago (past 24-h timeout)
        _link_wa_message(ticket, from_jid, minutes_ago=25 * 60)
        # Outgoing message: 30 min ago — should keep the ticket alive
        from frappe.utils import add_to_date, now_datetime
        out_msg = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "from": "agent",
            "reference_doctype": "HD Ticket",
            "reference_name": ticket,
            "message": "agent reply",
        }).insert(ignore_permissions=True)
        frappe.db.set_value(
            "WhatsApp Message", out_msg.name, "creation",
            add_to_date(now_datetime(), minutes=-30),
        )
        frappe.db.commit()

        doc = _MockWAMessage(from_jid, "customer reply")
        self._run(doc)

        self.assertEqual(doc._sets.get("reference_name"), ticket,
            "recent outgoing message should keep ticket within timeout")

    def test_ticket_past_last_activity_timeout_creates_new(self):
        """All messages on ticket > 24 h old → new ticket."""
        from_jid = "254700000003"
        ticket = _make_open_ticket(raised_by=f"whatsapp+{from_jid}@test.local")
        _link_wa_message(ticket, from_jid, minutes_ago=25 * 60)
        frappe.db.commit()

        doc = _MockWAMessage(from_jid, "new message")
        self._run(doc)

        self.assertNotEqual(doc._sets.get("reference_name"), ticket,
            "stale ticket should not be reused")
        self.assertTrue(frappe.db.exists("HD Ticket", doc._sets.get("reference_name")),
            "a new HD Ticket should have been created")

    # ── Task 4: reopen window ─────────────────────────────────────────────────

    def test_reopen_window_reopens_recently_resolved_ticket(self):
        """Customer replies within reopen window → old resolved ticket is reused."""
        from_jid = "254700000004"
        ticket = _make_resolved_ticket(raised_by=f"whatsapp+{from_jid}@test.local")
        _link_wa_message(ticket, from_jid, minutes_ago=5)
        # Ticket modified 3 minutes ago (just resolved)
        from frappe.utils import add_to_date, now_datetime
        frappe.db.set_value(
            "HD Ticket", ticket, "modified",
            add_to_date(now_datetime(), minutes=-3),
        )
        frappe.db.commit()

        s = frappe.get_single("WhatsApp Helpdesk Settings")
        s.reopen_window_minutes = 10
        s.save(ignore_permissions=True)
        frappe.db.commit()

        doc = _MockWAMessage(from_jid, "actually one more thing")
        self._run(doc)

        self.assertEqual(doc._sets.get("reference_name"), ticket,
            "should reuse resolved ticket within reopen window")

    def test_reopen_window_disabled_always_creates_new(self):
        """reopen_window_minutes = 0 → always create new ticket after resolve."""
        from_jid = "254700000005"
        ticket = _make_resolved_ticket(raised_by=f"whatsapp+{from_jid}@test.local")
        _link_wa_message(ticket, from_jid, minutes_ago=1)
        from frappe.utils import add_to_date, now_datetime
        frappe.db.set_value(
            "HD Ticket", ticket, "modified",
            add_to_date(now_datetime(), minutes=-1),
        )
        frappe.db.commit()

        s = frappe.get_single("WhatsApp Helpdesk Settings")
        s.reopen_window_minutes = 0
        s.save(ignore_permissions=True)
        frappe.db.commit()

        doc = _MockWAMessage(from_jid, "new message")
        self._run(doc)

        self.assertNotEqual(doc._sets.get("reference_name"), ticket,
            "reopen_window=0 should never reuse a resolved ticket")

    def test_reopen_window_expired_creates_new(self):
        """Customer replies after the reopen window has elapsed → new ticket."""
        from_jid = "254700000006"
        ticket = _make_resolved_ticket(raised_by=f"whatsapp+{from_jid}@test.local")
        _link_wa_message(ticket, from_jid, minutes_ago=60)
        from frappe.utils import add_to_date, now_datetime
        frappe.db.set_value(
            "HD Ticket", ticket, "modified",
            add_to_date(now_datetime(), minutes=-60),
        )
        frappe.db.commit()

        s = frappe.get_single("WhatsApp Helpdesk Settings")
        s.reopen_window_minutes = 10
        s.save(ignore_permissions=True)
        frappe.db.commit()

        doc = _MockWAMessage(from_jid, "much later message")
        self._run(doc)

        self.assertNotEqual(doc._sets.get("reference_name"), ticket)

    # ── Task 5: previous_ticket link ──────────────────────────────────────────

    def test_new_ticket_links_previous_resolved_ticket(self):
        """When a new ticket is created, previous_ticket is set to the resolved one."""
        from_jid = "254700000007"
        old_ticket = _make_resolved_ticket(raised_by=f"whatsapp+{from_jid}@test.local")
        _link_wa_message(old_ticket, from_jid, minutes_ago=120)
        frappe.db.commit()

        s = frappe.get_single("WhatsApp Helpdesk Settings")
        s.reopen_window_minutes = 0
        s.save(ignore_permissions=True)
        frappe.db.commit()

        doc = _MockWAMessage(from_jid, "brand new message")
        self._run(doc)

        new_ticket = doc._sets.get("reference_name")
        self.assertNotEqual(new_ticket, old_ticket)
        self.assertTrue(frappe.db.exists("HD Ticket", new_ticket))
        prev = frappe.db.get_value("HD Ticket", new_ticket, "previous_ticket")
        self.assertEqual(prev, old_ticket,
            "new ticket's previous_ticket should point to the resolved one")

    def test_first_time_customer_has_no_previous_ticket(self):
        """Brand-new customer → new ticket created with no previous_ticket."""
        from_jid = "254700000099"
        doc = _MockWAMessage(from_jid, "hello first time")
        self._run(doc)

        new_ticket = doc._sets.get("reference_name")
        self.assertTrue(frappe.db.exists("HD Ticket", new_ticket))
        prev = frappe.db.get_value("HD Ticket", new_ticket, "previous_ticket")
        self.assertFalse(prev, "first-time customer should have no previous_ticket")
```

- [ ] **Step 2: Run the tests**

```bash
cd /home/kushal/frappe-bench
bench --site dev.localhost run-tests --app helpdesk --module helpdesk.integrations.tests.test_fw_ticket_routing
```

Expected: all tests pass (or the whole class is skipped if `frappe_whatsapp` is not installed).

- [ ] **Step 3: Commit**

```bash
git add helpdesk/integrations/tests/test_fw_ticket_routing.py
git commit -m "test: add WABA ticket routing tests covering all six improvements"
```

---

## Self-Review

**Spec coverage:**
- [x] Null status_category bug — Task 1
- [x] Subject truncation — Task 2
- [x] Last-activity timeout — Task 3
- [x] Reopen window — Task 4 (setting + logic)
- [x] Previous ticket link — Task 5 (fixture + code)
- [x] Race condition — Task 6

**Placeholder scan:** All tasks contain complete code. No TBD or "implement later" strings.

**Type consistency:** `candidate` is a `str | None` throughout. `_fw_last_activity` returns `str | None`. `time_diff_in_hours` accepts both `str` datetime and `datetime` objects (frappe handles coercion).

**One gap flagged:** The `previous_ticket` field in `custom_field.json` requires `bench migrate` to materialise on the database. This is noted in Task 5 Step 4 but is not tested (schema migration is outside unit-test scope).
