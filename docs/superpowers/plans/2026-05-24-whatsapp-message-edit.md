# WhatsApp Message Edit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bidirectional WhatsApp message edit support — incoming edits update existing Baileys Message records in place; agents can edit their own sent text messages from the Helpdesk UI with changes pushed to WhatsApp via Evolution API.

**Architecture:** A new `Baileys Message Edit History` child DocType stores prior versions. A shared `_apply_edit()` helper handles both directions (update record, append history, publish `helpdesk:whatsapp-message-edit` realtime event). The Vue chat tab mutates the in-memory message list in place — no reload needed.

**Tech Stack:** Python/Frappe backend (PyPika query builder, `requests` for Evolution API HTTP), Vue 3 Composition API + TypeScript frontend (frappe-ui `call`, socket.io via `$socket`), MariaDB child table.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `helpdesk/helpdesk/doctype/baileys_message_edit_history/baileys_message_edit_history.json` | Child DocType schema |
| Create | `helpdesk/helpdesk/doctype/baileys_message_edit_history/baileys_message_edit_history.py` | Empty controller |
| Create | `helpdesk/helpdesk/doctype/baileys_message_edit_history/__init__.py` | Package marker |
| Modify | `helpdesk/helpdesk/doctype/baileys_message/baileys_message.json` | Add `is_edited` + `edit_history` fields |
| Modify | `helpdesk/integrations/evolution.py` | Add `_extract_edit`, `_apply_edit`, edit detection in `_handle_upsert`, new `edit_evolution_message` whitelist fn, update `get_whatsapp_messages` |
| Modify | `helpdesk/integrations/tests/test_evolution_webhook.py` | Add tests for all new backend logic |
| Modify | `desk/src/components/whatsapp/WhatsAppChatTab.vue` | Add `handleMessageEdit`, `applyEdit`, socket wiring, `@edit` on bubble |
| Modify | `desk/src/components/whatsapp/WhatsAppBubble.vue` | Edit button, inline editor, "Edited" badge, history tooltip |

---

## Task 1: Create `Baileys Message Edit History` child DocType

**Files:**
- Create: `helpdesk/helpdesk/doctype/baileys_message_edit_history/baileys_message_edit_history.json`
- Create: `helpdesk/helpdesk/doctype/baileys_message_edit_history/baileys_message_edit_history.py`
- Create: `helpdesk/helpdesk/doctype/baileys_message_edit_history/__init__.py`

- [ ] **Step 1: Create the directory and three files**

```bash
mkdir -p helpdesk/helpdesk/doctype/baileys_message_edit_history
```

`helpdesk/helpdesk/doctype/baileys_message_edit_history/__init__.py` — empty file.

`helpdesk/helpdesk/doctype/baileys_message_edit_history/baileys_message_edit_history.py`:
```python
import frappe
from frappe.model.document import Document


class BaileysMessageEditHistory(Document):
	pass
```

`helpdesk/helpdesk/doctype/baileys_message_edit_history/baileys_message_edit_history.json`:
```json
{
 "actions": [],
 "autoname": "hash",
 "creation": "2026-05-24 00:00:00.000000",
 "doctype": "DocType",
 "editable_grid": 1,
 "engine": "InnoDB",
 "field_order": [
  "old_message",
  "edited_at",
  "edited_by"
 ],
 "fields": [
  {
   "fieldname": "old_message",
   "fieldtype": "Text",
   "in_list_view": 1,
   "label": "Old Message"
  },
  {
   "fieldname": "edited_at",
   "fieldtype": "Datetime",
   "in_list_view": 1,
   "label": "Edited At"
  },
  {
   "fieldname": "edited_by",
   "fieldtype": "Data",
   "in_list_view": 1,
   "label": "Edited By"
  }
 ],
 "index_web_pages_for_search": 0,
 "istable": 1,
 "links": [],
 "modified": "2026-05-24 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "Helpdesk",
 "name": "Baileys Message Edit History",
 "naming_rule": "Random",
 "owner": "Administrator",
 "permissions": [],
 "row_format": "Dynamic",
 "sort_field": "creation",
 "sort_order": "ASC",
 "states": []
}
```

- [ ] **Step 2: Commit**

```bash
git add helpdesk/helpdesk/doctype/baileys_message_edit_history/
git commit -m "feat(whatsapp): add Baileys Message Edit History child DocType"
```

---

## Task 2: Add `is_edited` and `edit_history` fields to `Baileys Message`

**Files:**
- Modify: `helpdesk/helpdesk/doctype/baileys_message/baileys_message.json`

- [ ] **Step 1: Add fields to the JSON**

In `baileys_message.json`, add `"is_edited"` and `"edit_history"` to `field_order` after `"profile_name"`:

```json
"field_order": [
  "direction",
  "jid",
  "col_break_jid",
  "sender_jid",
  "sender_name",
  "col_break_ref",
  "reference_doctype",
  "reference_name",
  "section_message",
  "message",
  "content_type",
  "col_break_media",
  "media_url",
  "message_id",
  "reply_to_message_id",
  "section_meta",
  "status",
  "line",
  "is_read",
  "profile_name",
  "is_edited",
  "edit_history"
],
```

Add these two entries to the `fields` array:

```json
  {
   "default": "0",
   "fieldname": "is_edited",
   "fieldtype": "Check",
   "label": "Is Edited",
   "read_only": 1
  },
  {
   "fieldname": "edit_history",
   "fieldtype": "Table",
   "label": "Edit History",
   "options": "Baileys Message Edit History",
   "read_only": 1
  }
```

- [ ] **Step 2: Commit**

```bash
git add helpdesk/helpdesk/doctype/baileys_message/baileys_message.json
git commit -m "feat(whatsapp): add is_edited and edit_history fields to Baileys Message"
```

---

## Task 3: Run database migration

**Files:** (none — schema only)

- [ ] **Step 1: Migrate**

Run from `/home/frappeuser/bench16`:
```bash
bench --site site16.local migrate
```

Expected output: lines mentioning `Baileys Message Edit History` table creation and `baileys_message` column additions. No errors.

- [ ] **Step 2: Verify**

```bash
bench --site site16.local console
```

In the console:
```python
frappe.get_doc("Baileys Message", frappe.db.get_all("Baileys Message", limit=1)[0].name).edit_history
# → [] (empty list, no error)
frappe.db.get_value("Baileys Message", frappe.db.get_all("Baileys Message", limit=1)[0].name, "is_edited")
# → 0
```

Type `exit()` to quit.

---

## Task 4: Add `_extract_edit` helper and tests

**Files:**
- Modify: `helpdesk/integrations/evolution.py`
- Modify: `helpdesk/integrations/tests/test_evolution_webhook.py`

- [ ] **Step 1: Write the failing tests first**

Add this class to `test_evolution_webhook.py`:

```python
class TestExtractEdit(unittest.TestCase):
    def test_shape1_extracts_text(self):
        from helpdesk.integrations.evolution import _extract_edit
        raw = {
            "editedMessage": {
                "message": {
                    "protocolMessage": {
                        "type": 14,
                        "editedMessage": {"conversation": "new text shape1"}
                    }
                }
            }
        }
        text, is_edit = _extract_edit(raw)
        self.assertTrue(is_edit)
        self.assertEqual(text, "new text shape1")

    def test_shape2_extracts_text(self):
        from helpdesk.integrations.evolution import _extract_edit
        raw = {
            "protocolMessage": {
                "type": 14,
                "editedMessage": {"conversation": "new text shape2"}
            }
        }
        text, is_edit = _extract_edit(raw)
        self.assertTrue(is_edit)
        self.assertEqual(text, "new text shape2")

    def test_shape2_extended_text_message(self):
        from helpdesk.integrations.evolution import _extract_edit
        raw = {
            "protocolMessage": {
                "type": 14,
                "editedMessage": {
                    "extendedTextMessage": {"text": "new text extended"}
                }
            }
        }
        text, is_edit = _extract_edit(raw)
        self.assertTrue(is_edit)
        self.assertEqual(text, "new text extended")

    def test_regular_message_returns_false(self):
        from helpdesk.integrations.evolution import _extract_edit
        raw = {"conversation": "hello"}
        text, is_edit = _extract_edit(raw)
        self.assertFalse(is_edit)
        self.assertEqual(text, "")

    def test_empty_dict_returns_false(self):
        from helpdesk.integrations.evolution import _extract_edit
        text, is_edit = _extract_edit({})
        self.assertFalse(is_edit)
        self.assertEqual(text, "")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk --module helpdesk.integrations.tests.test_evolution_webhook
```

Expected: 5 failures with `ImportError: cannot import name '_extract_edit'`.

- [ ] **Step 3: Implement `_extract_edit` in `evolution.py`**

Add after the `_is_group` helper (around line 50, before `_upsert_contact`):

```python
def _extract_edit(raw_msg: dict) -> tuple[str, bool]:
	"""Detect an edited-message payload and return (new_text, True) or ('', False)."""
	# Shape 1: editedMessage wrapper → message → protocolMessage → editedMessage
	proto_via_edit = (
		(raw_msg.get("editedMessage") or {})
		.get("message", {})
		.get("protocolMessage") or {}
	)
	if proto_via_edit:
		inner = proto_via_edit.get("editedMessage") or {}
		text = inner.get("conversation") or (inner.get("extendedTextMessage") or {}).get("text") or ""
		if text:
			return text, True

	# Shape 2: direct protocolMessage with type 14
	proto = raw_msg.get("protocolMessage") or {}
	if proto.get("type") == 14:
		inner = proto.get("editedMessage") or {}
		text = inner.get("conversation") or (inner.get("extendedTextMessage") or {}).get("text") or ""
		if text:
			return text, True

	return "", False
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
bench --site site16.local run-tests --app helpdesk --module helpdesk.integrations.tests.test_evolution_webhook
```

Expected: `TestExtractEdit` — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add helpdesk/integrations/evolution.py helpdesk/integrations/tests/test_evolution_webhook.py
git commit -m "feat(whatsapp): add _extract_edit helper for incoming edit detection"
```

---

## Task 5: Add `_apply_edit` helper and tests

**Files:**
- Modify: `helpdesk/integrations/evolution.py`
- Modify: `helpdesk/integrations/tests/test_evolution_webhook.py`

- [ ] **Step 1: Write failing test**

Add to `test_evolution_webhook.py` inside the existing `TestEvolutionWebhook` class:

```python
    def test_apply_edit_updates_message_and_history(self):
        from helpdesk.integrations.evolution import _apply_edit, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "Baileys Message",
            "direction": "Incoming",
            "jid": "254799000001@s.whatsapp.net",
            "message": "original text",
            "content_type": "text",
            "message_id": "_test-apply-edit-001",
            "status": "Pending",
            "line": line.name,
            "is_read": 0,
        }).insert(ignore_permissions=True)

        _apply_edit(msg.name, "edited text", "incoming", msg.jid, line)

        updated = frappe.get_doc("Baileys Message", msg.name)
        self.assertEqual(updated.message, "edited text")
        self.assertEqual(updated.is_edited, 1)
        self.assertEqual(len(updated.edit_history), 1)
        self.assertEqual(updated.edit_history[0].old_message, "original text")
        self.assertEqual(updated.edit_history[0].edited_by, "incoming")

    def test_apply_edit_appends_on_second_edit(self):
        from helpdesk.integrations.evolution import _apply_edit, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "Baileys Message",
            "direction": "Incoming",
            "jid": "254799000002@s.whatsapp.net",
            "message": "v1",
            "content_type": "text",
            "message_id": "_test-apply-edit-002",
            "status": "Pending",
            "line": line.name,
            "is_read": 0,
        }).insert(ignore_permissions=True)

        _apply_edit(msg.name, "v2", "incoming", msg.jid, line)
        _apply_edit(msg.name, "v3", "incoming", msg.jid, line)

        updated = frappe.get_doc("Baileys Message", msg.name)
        self.assertEqual(updated.message, "v3")
        self.assertEqual(len(updated.edit_history), 2)
        # History rows are in insertion order (oldest first)
        messages_in_history = [r.old_message for r in updated.edit_history]
        self.assertIn("v1", messages_in_history)
        self.assertIn("v2", messages_in_history)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
bench --site site16.local run-tests --app helpdesk --module helpdesk.integrations.tests.test_evolution_webhook
```

Expected: 2 failures with `cannot import name '_apply_edit'`.

- [ ] **Step 3: Implement `_apply_edit` in `evolution.py`**

Add directly after `_extract_edit`:

```python
def _apply_edit(msg_name: str, new_text: str, edited_by: str, jid: str, line) -> None:
	"""Append old text to history, update message, publish realtime edit event."""
	doc = frappe.get_doc("Baileys Message", msg_name)
	doc.append("edit_history", {
		"old_message": doc.message or "",
		"edited_at": frappe.utils.now(),
		"edited_by": edited_by,
	})
	doc.message = new_text
	doc.is_edited = 1
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.publish_realtime(
		"helpdesk:whatsapp-message-edit",
		message={
			"message_id": doc.message_id,
			"new_text": new_text,
			"name": msg_name,
			"jid": jid,
			"line": line.name,
		},
		after_commit=True,
	)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
bench --site site16.local run-tests --app helpdesk --module helpdesk.integrations.tests.test_evolution_webhook
```

Expected: `test_apply_edit_*` — 2 passed (plus all prior tests still passing).

- [ ] **Step 5: Commit**

```bash
git add helpdesk/integrations/evolution.py helpdesk/integrations/tests/test_evolution_webhook.py
git commit -m "feat(whatsapp): add _apply_edit helper — update message, append history, publish event"
```

---

## Task 6: Wire incoming edit detection in `_handle_upsert`

**Files:**
- Modify: `helpdesk/integrations/evolution.py`
- Modify: `helpdesk/integrations/tests/test_evolution_webhook.py`

- [ ] **Step 1: Write failing integration test**

Add to `TestEvolutionWebhook` in `test_evolution_webhook.py`:

```python
    def test_incoming_edit_updates_existing_message(self):
        from helpdesk.integrations.evolution import _handle_upsert, _line, _settings
        line = _line("_test-evo")
        # Create the original message
        frappe.get_doc({
            "doctype": "Baileys Message",
            "direction": "Incoming",
            "jid": "254799000003@s.whatsapp.net",
            "message": "original from customer",
            "content_type": "text",
            "message_id": "_test-incoming-edit-001",
            "status": "Pending",
            "line": line.name,
            "is_read": 0,
        }).insert(ignore_permissions=True)

        edit_data = {
            "key": {
                "remoteJid": "254799000003@s.whatsapp.net",
                "fromMe": False,
                "id": "_test-incoming-edit-001",
            },
            "message": {
                "editedMessage": {
                    "message": {
                        "protocolMessage": {
                            "type": 14,
                            "editedMessage": {"conversation": "edited by customer"},
                        }
                    }
                }
            },
            "pushName": "Test Customer",
        }
        result = _handle_upsert(edit_data, line, _settings())
        self.assertEqual(result, {"status": "ok", "edited": True})

        msgs = frappe.db.get_all(
            "Baileys Message",
            filters={"message_id": "_test-incoming-edit-001"},
            fields=["name", "message", "is_edited"],
        )
        self.assertEqual(len(msgs), 1)  # No duplicate created
        self.assertEqual(msgs[0]["message"], "edited by customer")
        self.assertEqual(msgs[0]["is_edited"], 1)

    def test_edit_before_original_falls_through_as_new_message(self):
        from helpdesk.integrations.evolution import _handle_upsert, _line, _settings
        line = _line("_test-evo")
        # No original message exists — should fall through to normal insert
        edit_data = {
            "key": {
                "remoteJid": "254799000004@s.whatsapp.net",
                "fromMe": False,
                "id": "_test-edit-no-original-001",
            },
            "message": {
                "editedMessage": {
                    "message": {
                        "protocolMessage": {
                            "type": 14,
                            "editedMessage": {"conversation": "orphan edit"},
                        }
                    }
                }
            },
            "pushName": "Ghost",
        }
        result = _handle_upsert(edit_data, line, _settings())
        # Falls through — a new message record is created (content_type ends up as "text" from _extract_text)
        self.assertIn(result.get("status"), ("ok", "duplicate"))
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
bench --site site16.local run-tests --app helpdesk --module helpdesk.integrations.tests.test_evolution_webhook
```

Expected: `test_incoming_edit_*` — 2 failures.

- [ ] **Step 3: Add edit detection block in `_handle_upsert`**

In `evolution.py`, find `_handle_upsert`. Locate this comment/block:

```python
    # Deduplicate
    if message_id and frappe.db.exists("Baileys Message", {"message_id": message_id}):
        return {"status": "duplicate"}
```

Insert the following **immediately before** that dedup block:

```python
	# Detect and handle incoming edit before dedup check
	new_text, is_edit = _extract_edit(raw_msg)
	if is_edit and message_id:
		existing = frappe.db.get_value("Baileys Message", {"message_id": message_id}, "name")
		if existing:
			frappe.set_user("Administrator")
			_apply_edit(existing, new_text, edited_by="incoming", jid=jid, line=line)
			return {"status": "ok", "edited": True}
		# Fall through — original not yet stored (edge case: creates new record below)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
bench --site site16.local run-tests --app helpdesk --module helpdesk.integrations.tests.test_evolution_webhook
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add helpdesk/integrations/evolution.py helpdesk/integrations/tests/test_evolution_webhook.py
git commit -m "feat(whatsapp): detect and apply incoming WhatsApp message edits in webhook handler"
```

---

## Task 7: Add `edit_evolution_message` whitelist function

**Files:**
- Modify: `helpdesk/integrations/evolution.py`
- Modify: `helpdesk/integrations/tests/test_evolution_webhook.py`

- [ ] **Step 1: Write failing tests**

Add to `TestEvolutionWebhook` in `test_evolution_webhook.py`:

```python
    def test_edit_evolution_message_updates_record(self):
        from unittest.mock import patch, MagicMock
        from helpdesk.integrations.evolution import edit_evolution_message, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "Baileys Message",
            "direction": "Outgoing",
            "jid": "254799000005@s.whatsapp.net",
            "message": "original agent text",
            "content_type": "text",
            "message_id": "_test-out-edit-001",
            "status": "Read",
            "line": line.name,
            "is_read": 1,
        }).insert(ignore_permissions=True)

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {}

        with patch("helpdesk.integrations.evolution._requests") as mock_req:
            mock_req.put.return_value = mock_resp
            result = edit_evolution_message(msg.name, "updated agent text")

        self.assertEqual(result["status"], "ok")
        updated = frappe.get_doc("Baileys Message", msg.name)
        self.assertEqual(updated.message, "updated agent text")
        self.assertEqual(updated.is_edited, 1)
        self.assertEqual(len(updated.edit_history), 1)
        self.assertEqual(updated.edit_history[0].old_message, "original agent text")

        # Verify correct Evolution API endpoint was called
        call_args = mock_req.put.call_args
        self.assertIn("updateMessage", call_args[0][0])
        payload = call_args[1]["json"]
        self.assertEqual(payload["number"], "254799000005@s.whatsapp.net")
        self.assertEqual(payload["key"]["id"], "_test-out-edit-001")
        self.assertEqual(payload["text"], "updated agent text")

    def test_edit_evolution_message_rejects_incoming(self):
        from helpdesk.integrations.evolution import edit_evolution_message, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "Baileys Message",
            "direction": "Incoming",
            "jid": "254799000006@s.whatsapp.net",
            "message": "customer text",
            "content_type": "text",
            "message_id": "_test-in-reject-001",
            "status": "Pending",
            "line": line.name,
            "is_read": 0,
        }).insert(ignore_permissions=True)

        with self.assertRaises(frappe.ValidationError):
            edit_evolution_message(msg.name, "attempt to edit incoming")

    def test_edit_evolution_message_rejects_media(self):
        from helpdesk.integrations.evolution import edit_evolution_message, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "Baileys Message",
            "direction": "Outgoing",
            "jid": "254799000007@s.whatsapp.net",
            "message": "",
            "content_type": "image",
            "message_id": "_test-media-reject-001",
            "status": "Sent",
            "line": line.name,
            "is_read": 1,
        }).insert(ignore_permissions=True)

        with self.assertRaises(frappe.ValidationError):
            edit_evolution_message(msg.name, "try to edit image")

    def test_edit_evolution_message_rejects_empty_text(self):
        from helpdesk.integrations.evolution import edit_evolution_message, _line
        line = _line("_test-evo")
        msg = frappe.get_doc({
            "doctype": "Baileys Message",
            "direction": "Outgoing",
            "jid": "254799000008@s.whatsapp.net",
            "message": "some text",
            "content_type": "text",
            "message_id": "_test-empty-reject-001",
            "status": "Sent",
            "line": line.name,
            "is_read": 1,
        }).insert(ignore_permissions=True)

        with self.assertRaises(frappe.ValidationError):
            edit_evolution_message(msg.name, "")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
bench --site site16.local run-tests --app helpdesk --module helpdesk.integrations.tests.test_evolution_webhook
```

Expected: 4 failures with `cannot import name 'edit_evolution_message'`.

- [ ] **Step 3: Implement `edit_evolution_message` in `evolution.py`**

Add after `send_evolution_reaction` (around the end of the "Agent send" section):

```python
@frappe.whitelist()
def edit_evolution_message(message_name: str, new_text: str) -> dict:
	"""Edit an outgoing text message via Evolution API and update local record."""
	settings = _settings()
	if not settings.enabled:
		frappe.throw(_("Evolution API is not enabled."))

	doc = frappe.get_doc("Baileys Message", message_name)

	if doc.direction != "Outgoing":
		frappe.throw(_("Only outgoing messages can be edited."))
	if doc.content_type != "text":
		frappe.throw(_("Only text messages can be edited."))
	if not (new_text or "").strip():
		frappe.throw(_("Edit text cannot be empty."))

	line = frappe.get_doc("Evolution Line", doc.line)

	try:
		resp = _requests.put(
			_url("message/updateMessage", line.instance_name),
			json={
				"number": doc.jid,
				"key": {
					"id": doc.message_id,
					"fromMe": True,
					"remoteJid": doc.jid,
				},
				"text": new_text,
			},
			headers=_headers(line),
			timeout=15,
		)
		resp.raise_for_status()
	except Exception as e:
		frappe.throw(_("Evolution API edit failed: {0}").format(str(e)))

	agent_name = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
	_apply_edit(doc.name, new_text, edited_by=agent_name, jid=doc.jid, line=line)

	return {"status": "ok", "name": doc.name}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
bench --site site16.local run-tests --app helpdesk --module helpdesk.integrations.tests.test_evolution_webhook
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add helpdesk/integrations/evolution.py helpdesk/integrations/tests/test_evolution_webhook.py
git commit -m "feat(whatsapp): add edit_evolution_message — outgoing message edit via Evolution API"
```

---

## Task 8: Include `is_edited` and history in `get_whatsapp_messages`

**Files:**
- Modify: `helpdesk/integrations/evolution.py`

- [ ] **Step 1: Update `get_whatsapp_messages`**

Locate `get_whatsapp_messages` (around line 1162). The `select()` call currently ends with:

```python
			User.full_name.as_("sender_full_name"),
			BC.phone.as_("sender_phone"),
```

Add `BM.is_edited` to the select:

```python
			User.full_name.as_("sender_full_name"),
			BC.phone.as_("sender_phone"),
			BM.is_edited,
```

Then find the post-processing loop:

```python
	for m in rows:
		if m.get("creation") and not isinstance(m["creation"], str):
			m["creation"] = str(m["creation"])
		m["type"] = "Outgoing" if m["direction"] == "Outgoing" else "Incoming"
		m["attach"] = m.get("media_url") or ""
		m["is_reply"] = 1 if (m.get("reply_to_message_id") and m.get("content_type") != "reaction") else 0

	return rows
```

Replace with:

```python
	for m in rows:
		if m.get("creation") and not isinstance(m["creation"], str):
			m["creation"] = str(m["creation"])
		m["type"] = "Outgoing" if m["direction"] == "Outgoing" else "Incoming"
		m["attach"] = m.get("media_url") or ""
		m["is_reply"] = 1 if (m.get("reply_to_message_id") and m.get("content_type") != "reaction") else 0
		m["edit_history"] = []

	edited_names = [m["name"] for m in rows if m.get("is_edited")]
	if edited_names:
		history_rows = frappe.db.get_all(
			"Baileys Message Edit History",
			filters={"parent": ["in", edited_names]},
			fields=["parent", "old_message", "edited_at", "edited_by"],
			order_by="edited_at asc",
		)
		history_map: dict = {}
		for h in history_rows:
			if h.get("edited_at") and not isinstance(h["edited_at"], str):
				h["edited_at"] = str(h["edited_at"])
			history_map.setdefault(h["parent"], []).append(h)
		for m in rows:
			if m.get("is_edited"):
				m["edit_history"] = history_map.get(m["name"], [])

	return rows
```

- [ ] **Step 2: Restart gunicorn and verify**

```bash
pkill -f "frappe.app" && bench serve --port 8002 &
```

Open a ticket with WhatsApp messages in the browser. Open devtools → Network tab → look for the `get_whatsapp_messages` response. Confirm `is_edited` and `edit_history` are present in each message object.

- [ ] **Step 3: Commit**

```bash
git add helpdesk/integrations/evolution.py
git commit -m "feat(whatsapp): include is_edited and edit_history in get_whatsapp_messages response"
```

---

## Task 9: Update `WhatsAppChatTab.vue` — socket listener and `applyEdit`

**Files:**
- Modify: `desk/src/components/whatsapp/WhatsAppChatTab.vue`

- [ ] **Step 1: Add `handleMessageEdit` function**

Locate `handleStatusUpdate` in the `<script setup>` block (around line 293):

```ts
function handleStatusUpdate(data: { ticket: string; message_name: string; status: string }) {
```

Add the following function immediately after `handleStatusUpdate`:

```ts
function handleMessageEdit(data: { message_id: string; new_text: string; name: string }) {
  const list = messages.data
  if (!list) return
  const msg = list.find((m: any) => m.message_id === data.message_id)
  if (msg) {
    msg.message = data.new_text
    msg.is_edited = 1
  }
}
```

- [ ] **Step 2: Add `applyEdit` function**

Add after `handleMessageEdit`:

```ts
async function applyEdit(messageName: string, newText: string) {
  await call("helpdesk.integrations.evolution.edit_evolution_message", {
    message_name: messageName,
    new_text: newText,
  })
}
```

- [ ] **Step 3: Wire socket listener**

Find the `onMounted` block:

```ts
onMounted(() => {
  $socket.on("helpdesk:whatsapp-message", handleRealtimeMessage);
  $socket.on("helpdesk:whatsapp-status-update", handleStatusUpdate);
```

Add the third listener:

```ts
onMounted(() => {
  $socket.on("helpdesk:whatsapp-message", handleRealtimeMessage);
  $socket.on("helpdesk:whatsapp-status-update", handleStatusUpdate);
  $socket.on("helpdesk:whatsapp-message-edit", handleMessageEdit);
```

Find the `onBeforeUnmount` block:

```ts
onBeforeUnmount(() => {
  $socket.off("helpdesk:whatsapp-message", handleRealtimeMessage);
  $socket.off("helpdesk:whatsapp-status-update", handleStatusUpdate);
```

Add:

```ts
onBeforeUnmount(() => {
  $socket.off("helpdesk:whatsapp-message", handleRealtimeMessage);
  $socket.off("helpdesk:whatsapp-status-update", handleStatusUpdate);
  $socket.off("helpdesk:whatsapp-message-edit", handleMessageEdit);
```

- [ ] **Step 4: Pass `@edit` prop to `WhatsAppBubble`**

Find the `<WhatsAppBubble` usage in the template:

```html
            <WhatsAppBubble
              v-for="msg in group"
              :key="msg.name"
              :data-msg-id="msg.message_id"
              :message="msg"
              :reactions="reactionsMap[msg.message_id] || []"
              :replyToMessage="msg.is_reply && msg.reply_to_message_id ? messageByMsgId[msg.reply_to_message_id] || null : null"
              @reply="startReply"
              @react="sendReaction"
              @scrollToReply="scrollToMessage"
            />
```

Add `@edit="applyEdit"`:

```html
            <WhatsAppBubble
              v-for="msg in group"
              :key="msg.name"
              :data-msg-id="msg.message_id"
              :message="msg"
              :reactions="reactionsMap[msg.message_id] || []"
              :replyToMessage="msg.is_reply && msg.reply_to_message_id ? messageByMsgId[msg.reply_to_message_id] || null : null"
              @reply="startReply"
              @react="sendReaction"
              @scrollToReply="scrollToMessage"
              @edit="applyEdit"
            />
```

- [ ] **Step 5: Commit**

```bash
git add desk/src/components/whatsapp/WhatsAppChatTab.vue
git commit -m "feat(whatsapp): wire message-edit realtime handler and applyEdit in chat tab"
```

---

## Task 10: Update `WhatsAppBubble.vue` — edit button, inline editor, badge, tooltip

**Files:**
- Modify: `desk/src/components/whatsapp/WhatsAppBubble.vue`

- [ ] **Step 1: Add `edit` to the emits declaration**

Find the `defineEmits` call in `<script setup>`:

```ts
const emit = defineEmits<{
  (e: "reply", message: Record<string, any>): void;
  (e: "react", emoji: string, targetMessageId: string): void;
  (e: "scrollToReply", messageId: string): void;
}>();
```

Replace with:

```ts
const emit = defineEmits<{
  (e: "reply", message: Record<string, any>): void;
  (e: "react", emoji: string, targetMessageId: string): void;
  (e: "scrollToReply", messageId: string): void;
  (e: "edit", messageName: string, newText: string): void;
}>();
```

- [ ] **Step 2: Add inline-editor reactive state**

Find the existing reactive state near the top of `<script setup>` (after `const props = ...`). Add:

```ts
const editing = ref(false);
const editText = ref("");
const editTextareaRef = ref<HTMLTextAreaElement | null>(null);
const showEditHistory = ref(false);
```

- [ ] **Step 3: Add `editHistoryList` computed and `formatEditTime` helper**

After the existing `formattedTime` computed, add:

```ts
const editHistoryList = computed(() => {
  const history = props.message.edit_history || [];
  return [...history].sort((a, b) =>
    new Date(b.edited_at).getTime() - new Date(a.edited_at).getTime()
  );
});

function formatEditTime(ts: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
```

- [ ] **Step 4: Add `saveEdit` and `cancelEdit` functions**

Add after `copyText`:

```ts
function startEdit() {
  editText.value = props.message.message || "";
  editing.value = true;
  nextTick(() => editTextareaRef.value?.focus());
}

function saveEdit() {
  if (editSaving.value) return;
  const trimmed = editText.value.trim();
  if (!trimmed) return;
  // Fire-and-forget: parent's applyEdit handles errors via toast.
  // The realtime event updates the bubble text; close optimistically.
  emit("edit", props.message.name, trimmed);
  editing.value = false;
}

function cancelEdit() {
  editing.value = false;
  editText.value = "";
}
```

> **Note on the emit/await pattern:** The `edit` emit fires synchronously into `applyEdit` in the parent, which is `async`. Since Vue emits are fire-and-forget (no promise return), `saveEdit` resolves immediately and relies on the `helpdesk:whatsapp-message-edit` socket event to update the displayed text. If the API call fails, the parent should handle the error via `toast.error` — wire this up in Task 9's `applyEdit` function:

Return to `WhatsAppChatTab.vue` and update `applyEdit` to handle errors:

```ts
async function applyEdit(messageName: string, newText: string) {
  try {
    await call("helpdesk.integrations.evolution.edit_evolution_message", {
      message_name: messageName,
      new_text: newText,
    })
  } catch (e: any) {
    toast.error(e?.messages?.[0] || "Could not save edit")
  }
}
```

Also add `toast` to the import line in `WhatsAppChatTab.vue` if not already present:
```ts
import { createResource, LoadingIndicator, toast } from "frappe-ui";
```

- [ ] **Step 5: Add the Edit button to the action bar in the template**

Find the Copy button in the action bar:

```html
        <!-- Copy button -->
        <button
          v-if="message.message"
          class="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-full border border-outline-gray-2 bg-surface-white text-ink-gray-5 shadow-sm hover:bg-surface-gray-1 hover:text-ink-gray-8"
          :title="copied ? 'Copied!' : 'Copy text'"
          @click.stop="copyText"
        >
```

Add the Edit button **after** the Copy button and **before** the React button:

```html
        <!-- Edit button (outgoing text only) -->
        <button
          v-if="isOutgoing && message.content_type === 'text' && message.message_id"
          class="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-full border border-outline-gray-2 bg-surface-white text-ink-gray-5 shadow-sm hover:bg-surface-gray-1 hover:text-ink-gray-8"
          title="Edit message"
          @click.stop="startEdit"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>
```

- [ ] **Step 6: Replace message text area with inline editor when editing**

Find the message text div:

```html
        <!-- Message text -->
        <div v-if="message.message" class="whitespace-pre-wrap break-words" v-html="formattedMessage" />
```

Replace with:

```html
        <!-- Message text / inline editor -->
        <template v-if="editing">
          <textarea
            ref="editTextareaRef"
            v-model="editText"
            class="w-full resize-none rounded border border-outline-gray-3 bg-surface-white px-2 py-1 text-sm text-ink-gray-9 focus:outline-none dark:bg-surface-gray-1 dark:text-ink-gray-9"
            rows="3"
            @keydown.ctrl.enter="saveEdit"
            @keydown.esc="cancelEdit"
          />
          <div class="mt-1 flex justify-end gap-1.5">
            <button
              class="text-xs text-ink-gray-5 hover:text-ink-gray-8"
              @click.stop="cancelEdit"
            >Cancel</button>
            <button
              :disabled="editSaving"
              class="text-xs font-medium text-blue-600 hover:underline"
              @click.stop="saveEdit"
            >Save</button>
          </div>
        </template>
        <div v-else-if="message.message" class="whitespace-pre-wrap break-words" v-html="formattedMessage" />
```

- [ ] **Step 7: Add "Edited" badge with history tooltip to the footer**

Find the footer time/status row:

```html
        <!-- Footer: time + status -->
        <div class="mt-1 flex items-center justify-end gap-1">
          <span class="text-[10px] text-ink-gray-5">{{ formattedTime }}</span>
```

Add the "edited" badge span immediately after the `formattedTime` span:

```html
        <!-- Footer: time + status -->
        <div class="mt-1 flex items-center justify-end gap-1">
          <span class="text-[10px] text-ink-gray-5">{{ formattedTime }}</span>
          <!-- Edited badge + history tooltip -->
          <span
            v-if="message.is_edited"
            class="relative text-[10px] italic text-ink-gray-4 cursor-default select-none"
            @mouseenter="showEditHistory = true"
            @mouseleave="showEditHistory = false"
          >
            · edited
            <div
              v-if="showEditHistory && editHistoryList.length"
              class="absolute bottom-full right-0 mb-1 z-30 w-56 rounded border border-outline-gray-2 bg-surface-white shadow-md overflow-y-auto"
              style="max-height: 140px;"
            >
              <div
                v-for="h in editHistoryList"
                :key="h.edited_at"
                class="border-b border-outline-gray-1 px-2 py-1.5 last:border-0 text-[11px] text-ink-gray-7"
              >
                <div class="mb-0.5 text-ink-gray-4 font-medium">{{ formatEditTime(h.edited_at) }} · {{ h.edited_by }}</div>
                <div class="whitespace-pre-wrap break-words">{{ h.old_message || '(empty)' }}</div>
              </div>
            </div>
          </span>
```

- [ ] **Step 8: Commit**

```bash
git add desk/src/components/whatsapp/WhatsAppBubble.vue desk/src/components/whatsapp/WhatsAppChatTab.vue
git commit -m "feat(whatsapp): add inline edit UI, Edited badge, and history tooltip to WhatsAppBubble"
```

---

## Task 11: Build assets and smoke test

**Files:** (none)

- [ ] **Step 1: Build**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk
```

Expected: no TypeScript/Vue compilation errors. Ends with "✓ built in Xs".

- [ ] **Step 2: Restart**

```bash
pkill -f "frappe.app" && bench serve --port 8002 &
```

- [ ] **Step 3: Smoke test outgoing edit**

1. Open a WhatsApp ticket in Helpdesk at `http://site16.local:8002`
2. Hover over an outgoing (green) text message bubble — the pencil icon should appear in the action bar
3. Click the pencil icon — the bubble body should become an editable textarea with Save/Cancel
4. Change the text and click Save (or press Ctrl+Enter)
5. The bubble should update in place with the new text; `· edited` should appear in the footer
6. Hover over `· edited` — a tooltip should show the original text with timestamp

- [ ] **Step 4: Smoke test "Edited" badge on incoming (simulate via console)**

```bash
bench --site site16.local console
```

```python
# Pick any existing Baileys Message with direction=Incoming
msg = frappe.get_doc("Baileys Message", frappe.db.get_all("Baileys Message", filters={"direction": "Incoming"}, limit=1)[0].name)
msg.append("edit_history", {"old_message": "original text here", "edited_at": frappe.utils.now(), "edited_by": "incoming"})
msg.message = "edited by customer"
msg.is_edited = 1
msg.save(ignore_permissions=True)
frappe.db.commit()
```

Reload the ticket page — the incoming message should show `· edited` with tooltip.

- [ ] **Step 5: Final commit if any last-minute fixes**

```bash
git add -p   # stage only intentional changes
git commit -m "fix(whatsapp): post-build corrections"
```

---

## Migrations Checklist

- [ ] `bench --site site16.local migrate` (done in Task 3)
- [ ] `bench build --app helpdesk` (done in Task 11)
