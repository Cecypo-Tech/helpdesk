# Helpdesk Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an LLM-powered WhatsApp bot inside the helpdesk fork that auto-answers customer queries using the knowledge base, handles screenshots, and tracks missing KB articles.

**Architecture:** Background job pattern (Frappe enqueue) — doc_events fire instantly and enqueue a worker job; the LLM call happens asynchronously. Both WABA and WA Line channels converge on `send_wa_reply(ticket=..., message=...)` which routes internally. Provider abstraction in `llm.py` makes Gemini Flash 2.0 / Claude Haiku 4.5 interchangeable via a settings field.

**Tech Stack:** Python 3.10+, Frappe v15/v16, `google-generativeai`, `anthropic`, existing `helpdesk/integrations/wa.py`

**Working directory for all commands:** `/home/debian/frappe-workspace/frappe-bench`
**Site:** `dev.localhost`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `apps/helpdesk/helpdesk/integrations/bot.py` | Create | Orchestration: process_message job, KB search, gap tracking, escalation, doc_event handlers |
| `apps/helpdesk/helpdesk/integrations/llm.py` | Create | Gemini Flash 2.0 + Claude Haiku 4.5 provider adapters behind a single `chat()` interface |
| `apps/helpdesk/helpdesk/helpdesk/doctype/helpdesk_bot_settings/helpdesk_bot_settings.json` | Create | Singleton DocType — all bot config |
| `apps/helpdesk/helpdesk/helpdesk/doctype/helpdesk_bot_settings/helpdesk_bot_settings.py` | Create | Empty controller |
| `apps/helpdesk/helpdesk/helpdesk/doctype/helpdesk_bot_settings/__init__.py` | Create | Empty |
| `apps/helpdesk/helpdesk/helpdesk/doctype/hd_bot_missing_kb_query/hd_bot_missing_kb_query.json` | Create | Gap tracking DocType |
| `apps/helpdesk/helpdesk/helpdesk/doctype/hd_bot_missing_kb_query/hd_bot_missing_kb_query.py` | Create | Empty controller |
| `apps/helpdesk/helpdesk/helpdesk/doctype/hd_bot_missing_kb_query/__init__.py` | Create | Empty |
| `apps/helpdesk/helpdesk/helpdesk/doctype/wa_line/wa_line.json` | Modify | Add bot_enabled Check field |
| `apps/helpdesk/helpdesk/helpdesk/doctype/hd_ticket/hd_ticket.json` | Modify | Add bot_reply_count, bot_escalated, bot_active fields |
| `apps/helpdesk/helpdesk/fixtures/whatsapp_account_bot_field.json` | Create | Custom Field for WhatsApp Account.bot_enabled |
| `apps/helpdesk/helpdesk/hooks.py` | Modify | Add doc_events + fixtures entry |
| `apps/helpdesk/pyproject.toml` | Modify | Add google-generativeai, anthropic dependencies |
| `apps/helpdesk/helpdesk/tests/test_bot.py` | Create | Bot logic tests (word filter, KB search, gap tracking, escalation) |
| `apps/helpdesk/helpdesk/tests/test_llm.py` | Create | LLM provider routing tests |

---

## Task 1: Helpdesk Bot Settings DocType

**Files:**
- Create: `apps/helpdesk/helpdesk/helpdesk/doctype/helpdesk_bot_settings/__init__.py`
- Create: `apps/helpdesk/helpdesk/helpdesk/doctype/helpdesk_bot_settings/helpdesk_bot_settings.json`
- Create: `apps/helpdesk/helpdesk/helpdesk/doctype/helpdesk_bot_settings/helpdesk_bot_settings.py`

- [ ] **Step 1: Create the directory and empty files**

```bash
mkdir -p apps/helpdesk/helpdesk/helpdesk/doctype/helpdesk_bot_settings
touch apps/helpdesk/helpdesk/helpdesk/doctype/helpdesk_bot_settings/__init__.py
```

- [ ] **Step 2: Create `helpdesk_bot_settings.py`**

```python
# apps/helpdesk/helpdesk/helpdesk/doctype/helpdesk_bot_settings/helpdesk_bot_settings.py
import frappe
from frappe.model.document import Document


class HelpdeskBotSettings(Document):
	pass
```

- [ ] **Step 3: Create `helpdesk_bot_settings.json`**

```json
{
 "actions": [],
 "creation": "2026-06-01 00:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": [
  "is_enabled",
  "llm_section",
  "llm_provider",
  "gemini_api_key",
  "col_break_api",
  "anthropic_api_key",
  "behavior_section",
  "conversation_mode",
  "max_bot_replies",
  "col_break_behavior",
  "min_message_words",
  "kb_search_limit",
  "escalation_section",
  "escalation_message_enabled",
  "escalation_message",
  "col_break_escalation",
  "auto_escalate_on_no_kb",
  "gap_section",
  "enable_gap_tracking",
  "prompt_section",
  "system_prompt"
 ],
 "fields": [
  {
   "default": "0",
   "fieldname": "is_enabled",
   "fieldtype": "Check",
   "label": "Enable Bot"
  },
  {
   "fieldname": "llm_section",
   "fieldtype": "Section Break",
   "label": "LLM Provider"
  },
  {
   "default": "Gemini Flash 2.0",
   "fieldname": "llm_provider",
   "fieldtype": "Select",
   "label": "Provider",
   "options": "Gemini Flash 2.0\nClaude Haiku 4.5"
  },
  {
   "fieldname": "gemini_api_key",
   "fieldtype": "Password",
   "label": "Gemini API Key"
  },
  {
   "fieldname": "col_break_api",
   "fieldtype": "Column Break"
  },
  {
   "fieldname": "anthropic_api_key",
   "fieldtype": "Password",
   "label": "Anthropic API Key"
  },
  {
   "fieldname": "behavior_section",
   "fieldtype": "Section Break",
   "label": "Behavior"
  },
  {
   "default": "Single Reply",
   "fieldname": "conversation_mode",
   "fieldtype": "Select",
   "label": "Conversation Mode",
   "options": "Single Reply\nMulti-turn"
  },
  {
   "default": "3",
   "depends_on": "eval:doc.conversation_mode=='Multi-turn'",
   "fieldname": "max_bot_replies",
   "fieldtype": "Int",
   "label": "Max Bot Replies (Multi-turn)"
  },
  {
   "fieldname": "col_break_behavior",
   "fieldtype": "Column Break"
  },
  {
   "default": "3",
   "fieldname": "min_message_words",
   "fieldtype": "Int",
   "label": "Min Message Words (pre-filter)"
  },
  {
   "default": "3",
   "fieldname": "kb_search_limit",
   "fieldtype": "Int",
   "label": "KB Articles to Fetch"
  },
  {
   "fieldname": "escalation_section",
   "fieldtype": "Section Break",
   "label": "Escalation"
  },
  {
   "default": "1",
   "fieldname": "escalation_message_enabled",
   "fieldtype": "Check",
   "label": "Send Escalation Message"
  },
  {
   "default": "Connecting you with a human agent, please hold on.",
   "depends_on": "eval:doc.escalation_message_enabled",
   "fieldname": "escalation_message",
   "fieldtype": "Small Text",
   "label": "Escalation Message"
  },
  {
   "fieldname": "col_break_escalation",
   "fieldtype": "Column Break"
  },
  {
   "default": "0",
   "fieldname": "auto_escalate_on_no_kb",
   "fieldtype": "Check",
   "label": "Escalate Immediately When No KB Article Found"
  },
  {
   "fieldname": "gap_section",
   "fieldtype": "Section Break",
   "label": "KB Gap Tracking"
  },
  {
   "default": "1",
   "fieldname": "enable_gap_tracking",
   "fieldtype": "Check",
   "label": "Track Missing KB Queries"
  },
  {
   "fieldname": "prompt_section",
   "fieldtype": "Section Break",
   "label": "System Prompt"
  },
  {
   "default": "You are a helpful support assistant. Answer the customer's question clearly and concisely based on the knowledge base articles provided. Keep responses to 2-4 sentences. If no relevant article is available, give your best general answer. Never invent specific policies, pricing, or product details. If the issue is complex or the customer is frustrated, acknowledge their concern.",
   "fieldname": "system_prompt",
   "fieldtype": "Long Text",
   "label": "System Prompt"
  }
 ],
 "issingle": 1,
 "links": [],
 "modified": "2026-06-01 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "Helpdesk",
 "name": "Helpdesk Bot Settings",
 "owner": "Administrator",
 "permissions": [
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

- [ ] **Step 4: Run migrate to register the DocType**

```bash
bench --site dev.localhost migrate
```

Expected: `Updating DocType for Helpdesk Bot Settings` in output, no errors.

- [ ] **Step 5: Verify the singleton is accessible**

```bash
bench --site dev.localhost execute "frappe.get_doc('Helpdesk Bot Settings')" 2>&1 | tail -3
```

Expected: no exception.

- [ ] **Step 6: Commit**

```bash
cd apps/helpdesk
git add helpdesk/helpdesk/doctype/helpdesk_bot_settings/
git commit -m "feat: Helpdesk Bot Settings singleton DocType"
```

---

## Task 2: HD Bot Missing KB Query DocType

**Files:**
- Create: `apps/helpdesk/helpdesk/helpdesk/doctype/hd_bot_missing_kb_query/__init__.py`
- Create: `apps/helpdesk/helpdesk/helpdesk/doctype/hd_bot_missing_kb_query/hd_bot_missing_kb_query.json`
- Create: `apps/helpdesk/helpdesk/helpdesk/doctype/hd_bot_missing_kb_query/hd_bot_missing_kb_query.py`

- [ ] **Step 1: Create directory and empty files**

```bash
mkdir -p apps/helpdesk/helpdesk/helpdesk/doctype/hd_bot_missing_kb_query
touch apps/helpdesk/helpdesk/helpdesk/doctype/hd_bot_missing_kb_query/__init__.py
```

- [ ] **Step 2: Create `hd_bot_missing_kb_query.py`**

```python
# apps/helpdesk/helpdesk/helpdesk/doctype/hd_bot_missing_kb_query/hd_bot_missing_kb_query.py
import frappe
from frappe.model.document import Document


class HDBotMissingKBQuery(Document):
	pass
```

- [ ] **Step 3: Create `hd_bot_missing_kb_query.json`**

```json
{
 "actions": [],
 "autoname": "hash",
 "creation": "2026-06-01 00:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": [
  "ticket",
  "channel",
  "col_break_1",
  "status",
  "created_article",
  "query_section",
  "query_text",
  "suggestion_section",
  "suggested_title",
  "col_break_suggestion",
  "suggested_category"
 ],
 "fields": [
  {
   "fieldname": "ticket",
   "fieldtype": "Link",
   "in_list_view": 1,
   "label": "Ticket",
   "options": "HD Ticket"
  },
  {
   "fieldname": "channel",
   "fieldtype": "Select",
   "in_list_view": 1,
   "label": "Channel",
   "options": "WA Line\nWABA"
  },
  {
   "fieldname": "col_break_1",
   "fieldtype": "Column Break"
  },
  {
   "default": "Pending",
   "fieldname": "status",
   "fieldtype": "Select",
   "in_list_view": 1,
   "in_standard_filter": 1,
   "label": "Status",
   "options": "Pending\nArticle Created\nDismissed"
  },
  {
   "depends_on": "eval:doc.status=='Article Created'",
   "fieldname": "created_article",
   "fieldtype": "Link",
   "label": "Created Article",
   "options": "HD Article"
  },
  {
   "fieldname": "query_section",
   "fieldtype": "Section Break",
   "label": "Customer Query"
  },
  {
   "fieldname": "query_text",
   "fieldtype": "Long Text",
   "label": "Query Text"
  },
  {
   "fieldname": "suggestion_section",
   "fieldtype": "Section Break",
   "label": "LLM Suggestions"
  },
  {
   "fieldname": "suggested_title",
   "fieldtype": "Data",
   "in_list_view": 1,
   "label": "Suggested Article Title"
  },
  {
   "fieldname": "col_break_suggestion",
   "fieldtype": "Column Break"
  },
  {
   "fieldname": "suggested_category",
   "fieldtype": "Data",
   "label": "Suggested Category"
  }
 ],
 "links": [],
 "modified": "2026-06-01 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "Helpdesk",
 "name": "HD Bot Missing KB Query",
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
 "sort_field": "creation",
 "sort_order": "DESC",
 "states": [],
 "title_field": "suggested_title"
}
```

- [ ] **Step 4: Run migrate**

```bash
bench --site dev.localhost migrate
```

Expected: `Updating DocType for HD Bot Missing KB Query` in output.

- [ ] **Step 5: Commit**

```bash
cd apps/helpdesk
git add helpdesk/helpdesk/doctype/hd_bot_missing_kb_query/
git commit -m "feat: HD Bot Missing KB Query DocType for KB gap tracking"
```

---

## Task 3: Add bot fields to WA Line, HD Ticket, and WhatsApp Account

**Files:**
- Modify: `apps/helpdesk/helpdesk/helpdesk/doctype/wa_line/wa_line.json`
- Modify: `apps/helpdesk/helpdesk/helpdesk/doctype/hd_ticket/hd_ticket.json`
- Create: `apps/helpdesk/helpdesk/fixtures/whatsapp_account_bot_field.json`
- Modify: `apps/helpdesk/helpdesk/hooks.py`

- [ ] **Step 1: Add `bot_enabled` to `wa_line.json`**

In `apps/helpdesk/helpdesk/helpdesk/doctype/wa_line/wa_line.json`, add `"bot_enabled"` to `field_order` after `"placeholder_email_domain"`, and add the field object to `"fields"`:

```json
{
 "default": "0",
 "fieldname": "bot_enabled",
 "fieldtype": "Check",
 "label": "Enable Bot on This Line"
}
```

Also add `"bot_enabled"` to the `field_order` array after `"placeholder_email_domain"`.

- [ ] **Step 2: Add bot fields to `hd_ticket.json`**

In `apps/helpdesk/helpdesk/helpdesk/doctype/hd_ticket/hd_ticket.json`, add to the `fields` array:

```json
{
 "default": "0",
 "fieldname": "bot_reply_count",
 "fieldtype": "Int",
 "hidden": 1,
 "label": "Bot Reply Count"
},
{
 "default": "0",
 "fieldname": "bot_escalated",
 "fieldtype": "Check",
 "hidden": 1,
 "label": "Bot Escalated"
},
{
 "default": "0",
 "fieldname": "bot_active",
 "fieldtype": "Check",
 "hidden": 1,
 "label": "Bot Active"
}
```

Also add `"bot_reply_count"`, `"bot_escalated"`, `"bot_active"` to `field_order`.

- [ ] **Step 3: Create the Custom Field fixture for `WhatsApp Account.bot_enabled`**

Create `apps/helpdesk/helpdesk/fixtures/whatsapp_account_bot_field.json`:

```json
[
 {
  "doctype": "Custom Field",
  "dt": "WhatsApp Account",
  "fieldname": "bot_enabled",
  "fieldtype": "Check",
  "label": "Enable Bot on This Account",
  "default": "0",
  "insert_after": "allow_auto_read_receipt",
  "module": "Helpdesk"
 }
]
```

- [ ] **Step 4: Register the fixture in `hooks.py`**

In `apps/helpdesk/helpdesk/hooks.py`, find the `fixtures` list (or add one if absent) and add:

```python
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["dt", "=", "WhatsApp Account"], ["fieldname", "=", "bot_enabled"]],
    }
]
```

If a `fixtures` list already exists, append the dict to it.

- [ ] **Step 5: Run migrate**

```bash
bench --site dev.localhost migrate
```

Expected: new columns appear, no errors.

- [ ] **Step 6: Verify fields exist**

```bash
bench --site dev.localhost execute \
  "print(frappe.db.get_value('WA Line', {'instance_name': ['is', 'set']}, 'bot_enabled'))" 2>&1 | tail -3
```

Expected: `None` or `0` (field exists, no error).

- [ ] **Step 7: Commit**

```bash
cd apps/helpdesk
git add helpdesk/helpdesk/doctype/wa_line/wa_line.json \
        helpdesk/helpdesk/doctype/hd_ticket/hd_ticket.json \
        helpdesk/fixtures/whatsapp_account_bot_field.json \
        helpdesk/hooks.py
git commit -m "feat: add bot_enabled to WA Line + WhatsApp Account; add bot state fields to HD Ticket"
```

---

## Task 4: `llm.py` — Provider Abstraction

**Files:**
- Modify: `apps/helpdesk/pyproject.toml`
- Create: `apps/helpdesk/helpdesk/integrations/llm.py`
- Create: `apps/helpdesk/helpdesk/tests/test_llm.py`

- [ ] **Step 1: Write the failing test**

Create `apps/helpdesk/helpdesk/tests/test_llm.py`:

```python
import unittest
from unittest.mock import MagicMock, patch

import frappe


class TestLLMRouting(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		settings = frappe.get_single("Helpdesk Bot Settings")
		settings.is_enabled = 1
		settings.gemini_api_key = "test-gemini-key"
		settings.anthropic_api_key = "test-anthropic-key"
		settings.system_prompt = "You are a test assistant."
		settings.save(ignore_permissions=True)

	def test_routes_to_gemini(self):
		frappe.db.set_single_value("Helpdesk Bot Settings", "llm_provider", "Gemini Flash 2.0")
		frappe.clear_cache()
		with patch("helpdesk.integrations.llm._gemini") as mock_gemini:
			mock_gemini.return_value = "gemini reply"
			from helpdesk.integrations import llm

			result = llm.chat([{"role": "user", "content": "hello"}])
			mock_gemini.assert_called_once()
			self.assertEqual(result, "gemini reply")

	def test_routes_to_haiku(self):
		frappe.db.set_single_value("Helpdesk Bot Settings", "llm_provider", "Claude Haiku 4.5")
		frappe.clear_cache()
		with patch("helpdesk.integrations.llm._haiku") as mock_haiku:
			mock_haiku.return_value = "haiku reply"
			from helpdesk.integrations import llm

			result = llm.chat([{"role": "user", "content": "hello"}])
			mock_haiku.assert_called_once()
			self.assertEqual(result, "haiku reply")

	def test_images_passed_to_provider(self):
		frappe.db.set_single_value("Helpdesk Bot Settings", "llm_provider", "Gemini Flash 2.0")
		frappe.clear_cache()
		with patch("helpdesk.integrations.llm._gemini") as mock_gemini:
			mock_gemini.return_value = "reply"
			from helpdesk.integrations import llm

			fake_image = b"fake-image-bytes"
			llm.chat([{"role": "user", "content": "what is this?"}], images=[fake_image])
			args, kwargs = mock_gemini.call_args
			# images list is the second positional arg
			self.assertEqual(args[1], [fake_image])
```

- [ ] **Step 2: Run — expect ImportError**

```bash
bench --site dev.localhost run-tests --app helpdesk \
  --module helpdesk.tests.test_llm 2>&1 | tail -20
```

Expected: FAIL — `ModuleNotFoundError: No module named 'helpdesk.integrations.llm'`

- [ ] **Step 3: Add dependencies to `pyproject.toml`**

In `apps/helpdesk/pyproject.toml`, in the `[project] dependencies` section, add:

```toml
"google-generativeai>=0.8.0",
"anthropic>=0.40.0",
```

- [ ] **Step 4: Install the packages**

```bash
./env/bin/pip install "google-generativeai>=0.8.0" "anthropic>=0.40.0" -q
```

Expected: installs without errors.

- [ ] **Step 5: Create `apps/helpdesk/helpdesk/integrations/llm.py`**

```python
# helpdesk/integrations/llm.py
import base64

import frappe


def _settings():
	return frappe.get_cached_doc("Helpdesk Bot Settings")


def chat(messages: list[dict], images: list[bytes] | None = None) -> str:
	"""Send messages to the configured LLM and return the reply string.

	Args:
		messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]
		images:   Optional raw image bytes attached to the last user message.
	"""
	settings = _settings()
	provider = settings.llm_provider or "Gemini Flash 2.0"
	if provider == "Gemini Flash 2.0":
		return _gemini(messages, images, settings)
	return _haiku(messages, images, settings)


def _gemini(messages: list[dict], images: list[bytes] | None, settings) -> str:
	import google.generativeai as genai

	genai.configure(api_key=settings.get_password("gemini_api_key"))

	system_text = ""
	contents = []
	for msg in messages:
		if msg["role"] == "system":
			system_text = msg["content"]
			continue
		role = "user" if msg["role"] == "user" else "model"
		contents.append({"role": role, "parts": [{"text": msg["content"]}]})

	# Attach images to last user message
	if images:
		for i in range(len(contents) - 1, -1, -1):
			if contents[i]["role"] == "user":
				for img_bytes in images:
					contents[i]["parts"].append(
						{
							"inline_data": {
								"mime_type": "image/jpeg",
								"data": base64.b64encode(img_bytes).decode(),
							}
						}
					)
				break

	model = genai.GenerativeModel(
		"gemini-2.0-flash",
		system_instruction=system_text or None,
	)
	response = model.generate_content(contents)
	return response.text.strip()


def _haiku(messages: list[dict], images: list[bytes] | None, settings) -> str:
	import anthropic

	client = anthropic.Anthropic(api_key=settings.get_password("anthropic_api_key"))

	system_text = ""
	anthropic_messages = []
	for msg in messages:
		if msg["role"] == "system":
			system_text = msg["content"]
			continue
		anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

	# Attach images to last user message
	if images:
		for i in range(len(anthropic_messages) - 1, -1, -1):
			if anthropic_messages[i]["role"] == "user":
				parts = [{"type": "text", "text": anthropic_messages[i]["content"]}]
				for img_bytes in images:
					parts.insert(
						0,
						{
							"type": "image",
							"source": {
								"type": "base64",
								"media_type": "image/jpeg",
								"data": base64.b64encode(img_bytes).decode(),
							},
						},
					)
				anthropic_messages[i]["content"] = parts
				break

	response = client.messages.create(
		model="claude-haiku-4-5-20251001",
		max_tokens=512,
		system=system_text or "You are a helpful support assistant.",
		messages=anthropic_messages,
	)
	return response.content[0].text.strip()
```

- [ ] **Step 6: Run tests**

```bash
bench --site dev.localhost run-tests --app helpdesk \
  --module helpdesk.tests.test_llm 2>&1 | tail -20
```

Expected: 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
cd apps/helpdesk
git add helpdesk/integrations/llm.py helpdesk/tests/test_llm.py pyproject.toml
git commit -m "feat: llm.py — Gemini Flash 2.0 / Claude Haiku 4.5 provider abstraction"
```

---

## Task 5: `bot.py` — KB Search, Gap Tracking, and Core Helpers

**Files:**
- Create: `apps/helpdesk/helpdesk/integrations/bot.py` (partial — helpers only)
- Create: `apps/helpdesk/helpdesk/tests/test_bot.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/helpdesk/helpdesk/tests/test_bot.py`:

```python
import unittest

import frappe


class TestWordFilter(unittest.TestCase):
	def test_empty_string_is_short(self):
		from helpdesk.integrations.bot import _is_short_message

		self.assertTrue(_is_short_message("", 3))

	def test_none_is_short(self):
		from helpdesk.integrations.bot import _is_short_message

		self.assertTrue(_is_short_message(None, 3))

	def test_greeting_is_short(self):
		from helpdesk.integrations.bot import _is_short_message

		self.assertTrue(_is_short_message("hi", 3))
		self.assertTrue(_is_short_message("hello there", 3))

	def test_real_query_is_not_short(self):
		from helpdesk.integrations.bot import _is_short_message

		self.assertFalse(_is_short_message("my application is crashing on login", 3))

	def test_exactly_min_words_is_not_short(self):
		from helpdesk.integrations.bot import _is_short_message

		self.assertFalse(_is_short_message("one two three", 3))


class TestKBSearch(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		# Create a test KB article if HD Article doctype exists
		if not frappe.db.table_exists("HD Article"):
			return
		if not frappe.db.exists("HD Article", {"title": "Bot Test Reset Password"}):
			frappe.get_doc(
				{
					"doctype": "HD Article",
					"title": "Bot Test Reset Password",
					"content": "To reset your password, click Forgot Password on the login page.",
					"status": "Published",
				}
			).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		if not frappe.db.table_exists("HD Article"):
			return
		frappe.db.delete("HD Article", {"title": "Bot Test Reset Password"})

	def test_search_returns_matching_article(self):
		if not frappe.db.table_exists("HD Article"):
			self.skipTest("HD Article table not present")
		from helpdesk.integrations.bot import _search_kb

		results = _search_kb("reset password", limit=3)
		self.assertIsInstance(results, list)
		titles = [r["title"] for r in results]
		self.assertIn("Bot Test Reset Password", titles)

	def test_search_returns_empty_on_no_match(self):
		if not frappe.db.table_exists("HD Article"):
			self.skipTest("HD Article table not present")
		from helpdesk.integrations.bot import _search_kb

		results = _search_kb("xyzzy_nonexistent_query_12345", limit=3)
		self.assertEqual(results, [])


class TestGapTracking(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		# Create a minimal HD Ticket to link gap records to
		cls.ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "Bot gap test ticket",
				"raised_by": "Administrator",
			}
		).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.db.delete("HD Bot Missing KB Query", {"ticket": cls.ticket.name})
		frappe.delete_doc("HD Ticket", cls.ticket.name, ignore_permissions=True, force=True)

	def test_gap_record_inserted(self):
		from helpdesk.integrations.bot import _record_gap

		_record_gap(
			ticket_name=self.ticket.name,
			channel="WA Line",
			query_text="How do I export my data?",
			suggested_title="Data Export Guide",
			suggested_category="Account Management",
		)
		exists = frappe.db.exists(
			"HD Bot Missing KB Query",
			{"ticket": self.ticket.name, "query_text": "How do I export my data?"},
		)
		self.assertTrue(exists)

	def test_gap_record_default_status_is_pending(self):
		from helpdesk.integrations.bot import _record_gap

		_record_gap(
			ticket_name=self.ticket.name,
			channel="WABA",
			query_text="How do I change my billing plan?",
			suggested_title="Billing Plan Guide",
			suggested_category="Billing",
		)
		status = frappe.db.get_value(
			"HD Bot Missing KB Query",
			{"ticket": self.ticket.name, "query_text": "How do I change my billing plan?"},
			"status",
		)
		self.assertEqual(status, "Pending")
```

- [ ] **Step 2: Run — expect ImportError**

```bash
bench --site dev.localhost run-tests --app helpdesk \
  --module helpdesk.tests.test_bot 2>&1 | tail -20
```

Expected: FAIL — `ModuleNotFoundError: No module named 'helpdesk.integrations.bot'`

- [ ] **Step 3: Create `apps/helpdesk/helpdesk/integrations/bot.py`** (helpers only for now)

```python
# helpdesk/integrations/bot.py
import frappe


# ── Helpers ───────────────────────────────────────────────────────────────────


def _bot_settings():
	return frappe.get_cached_doc("Helpdesk Bot Settings")


def _is_short_message(text: str | None, min_words: int) -> bool:
	"""Return True when text has fewer words than min_words."""
	return len((text or "").split()) < min_words


def _search_kb(query: str, limit: int) -> list[dict]:
	"""Full-text search against published HD Article records.

	Returns a list of dicts with keys: name, title, content.
	"""
	if not query:
		return []
	like = f"%{query}%"
	return frappe.db.sql(
		"""
		SELECT name, title, content
		FROM `tabHD Article`
		WHERE status = 'Published'
		  AND (title LIKE %(like)s OR content LIKE %(like)s)
		LIMIT %(limit)s
		""",
		{"like": like, "limit": limit},
		as_dict=True,
	)


def _record_gap(
	ticket_name: str,
	channel: str,
	query_text: str,
	suggested_title: str,
	suggested_category: str,
) -> None:
	"""Insert an HD Bot Missing KB Query record. Swallows exceptions."""
	try:
		frappe.get_doc(
			{
				"doctype": "HD Bot Missing KB Query",
				"ticket": ticket_name,
				"channel": channel,
				"query_text": query_text,
				"suggested_title": suggested_title,
				"suggested_category": suggested_category,
				"status": "Pending",
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: _record_gap failed")
```

- [ ] **Step 4: Run tests**

```bash
bench --site dev.localhost run-tests --app helpdesk \
  --module helpdesk.tests.test_bot 2>&1 | tail -20
```

Expected: all tests in test_bot.py PASS.

- [ ] **Step 5: Commit**

```bash
cd apps/helpdesk
git add helpdesk/integrations/bot.py helpdesk/tests/test_bot.py
git commit -m "feat: bot.py helpers — word filter, KB search, gap tracking"
```

---

## Task 6: `bot.py` — `process_message` Background Job

**Files:**
- Modify: `apps/helpdesk/helpdesk/integrations/bot.py` (add process_message + escalate)

- [ ] **Step 1: Add tests for escalation and process_message to `test_bot.py`**

Append to `apps/helpdesk/helpdesk/tests/test_bot.py`:

```python
class TestEscalation(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.ticket = frappe.get_doc(
			{
				"doctype": "HD Ticket",
				"subject": "Bot escalation test",
				"raised_by": "Administrator",
			}
		).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.delete_doc("HD Ticket", cls.ticket.name, ignore_permissions=True, force=True)

	def test_escalate_sets_bot_escalated(self):
		from unittest.mock import patch

		settings = frappe.get_single("Helpdesk Bot Settings")
		settings.escalation_message_enabled = 0
		settings.save(ignore_permissions=True)

		from helpdesk.integrations.bot import _escalate

		with patch("helpdesk.integrations.bot.send_wa_reply"):
			_escalate(self.ticket.name)

		val = frappe.db.get_value("HD Ticket", self.ticket.name, "bot_escalated")
		self.assertEqual(val, 1)

	def test_escalate_sends_message_when_enabled(self):
		from unittest.mock import patch

		settings = frappe.get_single("Helpdesk Bot Settings")
		settings.escalation_message_enabled = 1
		settings.escalation_message = "Test escalation message"
		settings.save(ignore_permissions=True)

		# Reset bot_escalated
		frappe.db.set_value("HD Ticket", self.ticket.name, "bot_escalated", 0)

		from helpdesk.integrations.bot import _escalate

		with patch("helpdesk.integrations.bot.send_wa_reply") as mock_send:
			_escalate(self.ticket.name)
			mock_send.assert_called_once_with(
				ticket=self.ticket.name, message="Test escalation message"
			)
```

- [ ] **Step 2: Run — expect failing tests**

```bash
bench --site dev.localhost run-tests --app helpdesk \
  --module helpdesk.tests.test_bot 2>&1 | tail -20
```

Expected: `TestEscalation` tests FAIL — `_escalate` not yet defined.

- [ ] **Step 3: Append `_escalate`, `_get_conversation_history`, and `process_message` to `bot.py`**

```python
# ── Conversation history ───────────────────────────────────────────────────────


def _get_conversation_history(ticket_name: str, channel: str) -> list[dict]:
	"""Return last 10 messages for the ticket as role/content dicts, oldest first."""
	if channel == "waba":
		rows = frappe.db.get_all(
			"WhatsApp Message",
			filters={"reference_doctype": "HD Ticket", "reference_name": ticket_name},
			fields=["type", "message", "creation"],
			order_by="creation asc",
			limit=10,
		)
		return [
			{"role": "user" if r.type == "Incoming" else "assistant", "content": r.message or ""}
			for r in rows
		]
	# wa_line
	rows = frappe.db.get_all(
		"WA Message",
		filters={"reference_doctype": "HD Ticket", "reference_name": ticket_name},
		fields=["direction", "message", "creation"],
		order_by="creation asc",
		limit=10,
	)
	return [
		{"role": "user" if r.direction == "Incoming" else "assistant", "content": r.message or ""}
		for r in rows
	]


# ── Image download ─────────────────────────────────────────────────────────────


def _download_image_waba(attach_url: str) -> bytes | None:
	try:
		from frappe.utils.file_manager import get_file

		_, content = get_file(attach_url)
		return content
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: WABA image download failed")
		return None


def _download_image_wa_line(media_url: str, line_name: str) -> bytes | None:
	try:
		from helpdesk.integrations.wa import _evo_session, _headers, _line

		line_doc = _line(line_name)
		resp = _evo_session.get(media_url, headers=_headers(line_doc), timeout=15)
		resp.raise_for_status()
		return resp.content
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: WA Line image download failed")
		return None


# ── Escalation ────────────────────────────────────────────────────────────────


def _escalate(ticket_name: str) -> None:
	"""Optionally send an escalation message then mark the ticket as escalated."""
	from helpdesk.integrations.wa import send_wa_reply

	settings = _bot_settings()
	if settings.escalation_message_enabled and settings.escalation_message:
		try:
			send_wa_reply(ticket=ticket_name, message=settings.escalation_message)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: escalation message failed")

	frappe.db.set_value(
		"HD Ticket",
		ticket_name,
		{"bot_escalated": 1, "bot_active": 0},
		update_modified=False,
	)


# ── Background job ────────────────────────────────────────────────────────────


def process_message(msg_name: str, channel: str) -> None:
	"""Background job: process one incoming message and send a bot reply.

	channel: "waba" or "wa_line"
	"""
	settings = _bot_settings()
	if not settings.is_enabled:
		return

	# Load message and extract fields
	if channel == "waba":
		msg = frappe.get_doc("WhatsApp Message", msg_name)
		ticket_name = msg.reference_name if msg.reference_doctype == "HD Ticket" else None
		text = msg.message or ""
		has_image = msg.content_type == "image"
		image_source = msg.attach if has_image else None
		channel_label = "WABA"
	else:
		msg = frappe.get_doc("WA Message", msg_name)
		ticket_name = msg.reference_name if msg.reference_doctype == "HD Ticket" else None
		text = msg.message or ""
		has_image = msg.content_type == "image"
		image_source = msg.media_url if has_image else None
		line_name = msg.line
		channel_label = "WA Line"

	if not ticket_name:
		return

	ticket = frappe.get_doc("HD Ticket", ticket_name)

	if ticket.bot_escalated:
		return

	if _is_short_message(text, settings.min_message_words or 3):
		return

	# Multi-turn reply limit
	if settings.conversation_mode == "Multi-turn":
		if (ticket.bot_reply_count or 0) >= (settings.max_bot_replies or 3):
			_escalate(ticket_name)
			return

	# Download image
	images = []
	if has_image and image_source:
		if channel == "waba":
			img_bytes = _download_image_waba(image_source)
		else:
			img_bytes = _download_image_wa_line(image_source, line_name)
		if img_bytes:
			images.append(img_bytes)

	# KB search
	articles = _search_kb(text, settings.kb_search_limit or 3)

	# Gap tracking
	if not articles and settings.enable_gap_tracking:
		_handle_kb_gap(ticket_name, channel_label, text, settings, images)
		if settings.auto_escalate_on_no_kb:
			_escalate(ticket_name)
			return

	# Build prompt
	kb_context = "\n\n".join(f"Article: {a.title}\n{a.content}" for a in articles)
	system_content = (settings.system_prompt or "You are a helpful support assistant.")
	if kb_context:
		system_content += f"\n\nKnowledge Base:\n{kb_context}"

	history = _get_conversation_history(ticket_name, channel)
	messages = [{"role": "system", "content": system_content}]
	# Include history excluding the current message (last item)
	for h in history[:-1]:
		messages.append(h)
	messages.append({"role": "user", "content": text})

	# LLM call
	from helpdesk.integrations.llm import chat as llm_chat

	try:
		reply = llm_chat(messages, images or None)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: LLM call failed")
		_escalate(ticket_name)
		return

	# Send reply
	from helpdesk.integrations.wa import send_wa_reply

	try:
		send_wa_reply(ticket=ticket_name, message=reply)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Helpdesk Bot: send reply failed")
		return

	# Update ticket state
	frappe.db.set_value(
		"HD Ticket",
		ticket_name,
		{"bot_reply_count": (ticket.bot_reply_count or 0) + 1, "bot_active": 1},
		update_modified=False,
	)

	if settings.conversation_mode == "Single Reply":
		_escalate(ticket_name)


def _handle_kb_gap(
	ticket_name: str,
	channel_label: str,
	text: str,
	settings,
	images: list[bytes],
) -> None:
	"""Ask the LLM for a gap suggestion and record it. Non-critical; swallows errors."""
	import json

	try:
		from helpdesk.integrations.llm import chat as llm_chat

		gap_messages = [
			{
				"role": "system",
				"content": 'Given a customer support query, suggest a knowledge base article title and category. Respond ONLY with valid JSON: {"title": "...", "category": "..."}',
			},
			{"role": "user", "content": text},
		]
		raw = llm_chat(gap_messages)
		gap_data = json.loads(raw)
		suggested_title = gap_data.get("title", "")
		suggested_category = gap_data.get("category", "")
	except Exception:
		suggested_title = ""
		suggested_category = ""

	_record_gap(ticket_name, channel_label, text, suggested_title, suggested_category)
```

- [ ] **Step 4: Run tests**

```bash
bench --site dev.localhost run-tests --app helpdesk \
  --module helpdesk.tests.test_bot 2>&1 | tail -20
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
cd apps/helpdesk
git add helpdesk/integrations/bot.py helpdesk/tests/test_bot.py
git commit -m "feat: bot.py — process_message background job, escalation, conversation history"
```

---

## Task 7: Doc-event Handlers and `hooks.py` Wiring

**Files:**
- Modify: `apps/helpdesk/helpdesk/integrations/bot.py` (add handlers)
- Modify: `apps/helpdesk/helpdesk/hooks.py`

- [ ] **Step 1: Append the two doc-event handlers to `bot.py`**

```python
# ── Doc-event handlers ────────────────────────────────────────────────────────


def handle_whatsapp_message(doc, method=None) -> None:
	"""after_insert handler for WhatsApp Message (WABA path)."""
	if doc.type != "Incoming":
		return
	if doc.reference_doctype != "HD Ticket" or not doc.reference_name:
		return

	settings = _bot_settings()
	if not settings.is_enabled:
		return

	if doc.whatsapp_account:
		bot_enabled = frappe.db.get_value("WhatsApp Account", doc.whatsapp_account, "bot_enabled")
		if not bot_enabled:
			return

	frappe.enqueue(
		"helpdesk.integrations.bot.process_message",
		queue="short",
		msg_name=doc.name,
		channel="waba",
	)


def handle_wa_message(doc, method=None) -> None:
	"""after_insert handler for WA Message (Evolution API / WA Line path)."""
	if doc.direction != "Incoming":
		return
	if doc.reference_doctype != "HD Ticket" or not doc.reference_name:
		return

	settings = _bot_settings()
	if not settings.is_enabled:
		return

	if doc.line:
		bot_enabled = frappe.db.get_value("WA Line", doc.line, "bot_enabled")
		if not bot_enabled:
			return

	frappe.enqueue(
		"helpdesk.integrations.bot.process_message",
		queue="short",
		msg_name=doc.name,
		channel="wa_line",
	)
```

- [ ] **Step 2: Register the handlers in `hooks.py`**

Open `apps/helpdesk/helpdesk/hooks.py`. Find the `doc_events` dict and add:

```python
doc_events = {
    # ... existing entries ...
    "WhatsApp Message": {
        "after_insert": "helpdesk.integrations.bot.handle_whatsapp_message",
    },
    "WA Message": {
        "after_insert": "helpdesk.integrations.bot.handle_wa_message",
    },
}
```

If `doc_events` does not yet exist in hooks.py, add it as a new dict. If it already exists, merge these two entries into it.

- [ ] **Step 3: Add tests for the handlers to `test_bot.py`**

Append to `apps/helpdesk/helpdesk/tests/test_bot.py`:

```python
class TestDocEventHandlers(unittest.TestCase):
	def test_handle_wa_message_skips_outgoing(self):
		from unittest.mock import patch

		from helpdesk.integrations.bot import handle_wa_message

		doc = frappe.new_doc("WA Message")
		doc.direction = "Outgoing"
		doc.reference_doctype = "HD Ticket"
		doc.reference_name = "TEST-001"

		with patch("frappe.enqueue") as mock_enqueue:
			handle_wa_message(doc)
			mock_enqueue.assert_not_called()

	def test_handle_wa_message_skips_when_bot_disabled(self):
		from unittest.mock import patch

		frappe.db.set_single_value("Helpdesk Bot Settings", "is_enabled", 0)
		frappe.clear_cache()

		from helpdesk.integrations.bot import handle_wa_message

		doc = frappe.new_doc("WA Message")
		doc.direction = "Incoming"
		doc.reference_doctype = "HD Ticket"
		doc.reference_name = "TEST-001"
		doc.line = None

		with patch("frappe.enqueue") as mock_enqueue:
			handle_wa_message(doc)
			mock_enqueue.assert_not_called()

		frappe.db.set_single_value("Helpdesk Bot Settings", "is_enabled", 1)
		frappe.clear_cache()

	def test_handle_whatsapp_message_skips_outgoing(self):
		from unittest.mock import patch

		from helpdesk.integrations.bot import handle_whatsapp_message

		doc = frappe.new_doc("WhatsApp Message")
		doc.type = "Outgoing"
		doc.reference_doctype = "HD Ticket"
		doc.reference_name = "TEST-001"

		with patch("frappe.enqueue") as mock_enqueue:
			handle_whatsapp_message(doc)
			mock_enqueue.assert_not_called()
```

- [ ] **Step 4: Run all bot tests**

```bash
bench --site dev.localhost run-tests --app helpdesk \
  --module helpdesk.tests.test_bot 2>&1 | tail -20
```

Expected: all tests PASS.

- [ ] **Step 5: Restart and run full helpdesk test suite**

```bash
bench restart
bench --site dev.localhost run-tests --app helpdesk 2>&1 | tail -30
```

Expected: all existing tests still pass, new tests pass.

- [ ] **Step 6: Commit**

```bash
cd apps/helpdesk
git add helpdesk/integrations/bot.py helpdesk/hooks.py helpdesk/tests/test_bot.py
git commit -m "feat: wire bot doc_event handlers for WA Message and WhatsApp Message"
```

---

## Task 8: Smoke Test and Final Verification

- [ ] **Step 1: Build assets**

```bash
bench build --app helpdesk
```

Expected: build completes without errors.

- [ ] **Step 2: Verify Helpdesk Bot Settings is reachable in the desk**

Open `https://dev.localhost/app/helpdesk-bot-settings` in a browser. Confirm the form loads and all sections are visible.

- [ ] **Step 3: Verify HD Bot Missing KB Query list view**

Open `https://dev.localhost/app/hd-bot-missing-kb-query`. Confirm the list view loads.

- [ ] **Step 4: Test the word filter live**

In `Helpdesk Bot Settings`, set `is_enabled=1`, `min_message_words=3`. Enable bot on one `WA Line` record. Send a one-word message ("hi") via WhatsApp to that line. Confirm no bot reply is sent and no background job error appears in the error log.

- [ ] **Step 5: End-to-end smoke test with a real query**

Send a message with 4+ words to a bot-enabled WA Line. Confirm:
1. A background job runs (check `frappe.utils.background_jobs` or error log)
2. If no KB article matches and `enable_gap_tracking=1`, an `HD Bot Missing KB Query` record appears
3. A reply is sent to the WhatsApp number

- [ ] **Step 6: Final commit**

```bash
cd apps/helpdesk
git add -u
git commit -m "feat: helpdesk bot — LLM-powered WhatsApp auto-reply with KB lookup and gap tracking"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ Helpdesk Bot Settings singleton — Task 1
- ✅ HD Bot Missing KB Query — Task 2
- ✅ Per-channel enable/disable (WA Line + WhatsApp Account + HD Ticket fields) — Task 3
- ✅ Gemini Flash 2.0 / Claude Haiku 4.5 interchangeable — Task 4 (llm.py)
- ✅ Word count pre-filter — Task 5 (_is_short_message)
- ✅ KB full-text search — Task 5 (_search_kb)
- ✅ Gap tracking with LLM title/category suggestion — Task 6 (_handle_kb_gap + _record_gap)
- ✅ Image download (WABA + WA Line) — Task 6 (_download_image_waba, _download_image_wa_line)
- ✅ Conversation history (multi-turn) — Task 6 (_get_conversation_history)
- ✅ Single Reply / Multi-turn modes — Task 6 (process_message)
- ✅ Escalation with optional message — Task 6 (_escalate)
- ✅ auto_escalate_on_no_kb — Task 6 (process_message)
- ✅ Background job (enqueue) — Task 7 (handlers)
- ✅ doc_events for both channels — Task 7 (hooks.py)
- ✅ LLM failure → escalate — Task 6 (process_message exception handler)
- ✅ Image download failure → text-only fallback — Task 6 (images remain empty list)
