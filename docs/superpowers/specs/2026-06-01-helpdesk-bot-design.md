# Helpdesk Bot Design Spec

**Date:** 2026-06-01
**App:** `helpdesk` (Cecypo fork)
**Bench:** `/home/debian/frappe-workspace/frappe-bench` · Frappe v15/v16 · site `dev.localhost`

---

## Context

The helpdesk fork has two WhatsApp integrations: WABA (via `frappe_whatsapp`, `WhatsApp Message` DocType) and WA Line (Evolution API, `WA Message` DocType). Customers frequently send screenshots of errors alongside text queries. We want an LLM-powered bot that:

- Auto-answers customer queries using the helpdesk knowledge base
- Handles images (screenshots) via multimodal LLM
- Tracks queries where no KB article existed so new articles can be created
- Can be enabled/disabled per channel (per `WhatsApp Account` and per `WA Line`)
- Supports two LLM providers interchangeably

---

## Architecture

Background job pattern: the `after_insert` doc_event enqueues a Frappe background job immediately so the incoming webhook returns without blocking. The bot reply arrives seconds later. Both channel paths converge on the existing `send_wa_reply(ticket=..., message=...)` function in `wa.py`, which already handles WABA vs Evolution routing internally.

---

## DocTypes

### `Helpdesk Bot Settings` (singleton)

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `is_enabled` | Check | 0 | Master switch |
| `llm_provider` | Select | Gemini Flash 2.0 | Gemini Flash 2.0 / Claude Haiku 4.5 |
| `gemini_api_key` | Password | — | |
| `anthropic_api_key` | Password | — | |
| `conversation_mode` | Select | Single Reply | Single Reply / Multi-turn |
| `max_bot_replies` | Int | 3 | Only shown in Multi-turn mode |
| `min_message_words` | Int | 3 | Pre-filter; skip messages below this word count |
| `kb_search_limit` | Int | 3 | Number of KB articles to fetch per query |
| `escalation_message_enabled` | Check | 1 | |
| `escalation_message` | Small Text | "Connecting you with a human agent, please hold on." | |
| `auto_escalate_on_no_kb` | Check | 0 | Escalate immediately when KB search returns nothing |
| `enable_gap_tracking` | Check | 1 | Save HD Bot Missing KB Query on empty KB results |
| `system_prompt` | Long Text | (see default below) | |

**Default system prompt:**
```
You are a helpful support assistant. Answer the customer's question clearly and concisely based on the knowledge base articles provided. Keep responses to 2–4 sentences. If no relevant article is available, give your best general answer. Never invent specific policies, pricing, or product details. If the issue is complex or the customer is frustrated, acknowledge their concern and let them know a human agent will assist shortly.
```

### `HD Bot Missing KB Query`

| Field | Type | Notes |
|-------|------|-------|
| `ticket` | Link → HD Ticket | |
| `channel` | Select: WA Line / WABA | |
| `query_text` | Long Text | Raw customer message |
| `suggested_title` | Data | LLM-generated article title suggestion |
| `suggested_category` | Data | LLM-generated category suggestion |
| `status` | Select: Pending / Article Created / Dismissed | Default: Pending |
| `created_article` | Link → HD Article | Filled when article is created from gap |

### Fields added to existing DocTypes

Via direct JSON modification (both owned by the helpdesk app):

**`WA Line`** — add `bot_enabled` Check (default 0), in "Ticket Defaults" section.

**`HD Ticket`** — add three fields:
- `bot_reply_count` Int (default 0)
- `bot_escalated` Check (default 0)
- `bot_active` Check (default 0)

Via Custom Field fixture (owned by `frappe_whatsapp`):

**`WhatsApp Account`** — add `bot_enabled` Check (default 0).

---

## Bot Logic Flow

```
after_insert on WA Message (direction=Incoming)
after_insert on WhatsApp Message (type=Incoming)
  │
  ├─ Global is_enabled check → skip if off
  ├─ Channel bot_enabled check:
  │   WA Message: WA Line.bot_enabled via msg.line
  │   WhatsApp Message: WhatsApp Account.bot_enabled via msg.whatsapp_account
  │
  └─ frappe.enqueue("helpdesk.integrations.bot.process_message",
                    queue="short", msg_name=doc.name, channel="wa_line"|"waba")

process_message(msg_name, channel)  [background worker]
  │
  ├─ Load message doc, extract: ticket_name, text, content_type, image_source
  ├─ ticket.bot_escalated=1 → return
  ├─ word count < min_message_words → return (silent skip)
  ├─ Multi-turn: bot_reply_count >= max_bot_replies → escalate + return
  │
  ├─ Download image bytes if content_type=image
  │   WA Line: GET media_url via _evo_session with instance auth headers
  │   WABA: frappe.utils.file_manager.get_file(attach_url)
  │
  ├─ _search_kb(text, limit) → articles[]
  │
  ├─ No articles found + enable_gap_tracking:
  │   └─ LLM text-only call → {"title": "...", "category": "..."} JSON
  │        └─ insert HD Bot Missing KB Query
  │   auto_escalate_on_no_kb → escalate + return
  │
  ├─ Build prompt:
  │   system = system_prompt + KB articles context
  │   history = prior WA Message / WhatsApp Message turns (last 10, oldest first)
  │   user = current message text
  │
  ├─ llm.chat(messages, images) → reply_text
  │   (on exception → escalate + return)
  │
  ├─ send_wa_reply(ticket=ticket_name, message=reply_text)
  │   (wa.py routes to WABA or Evolution API automatically)
  │
  ├─ Increment HD Ticket.bot_reply_count
  │
  └─ Single Reply mode → escalate
     escalate():
       escalation_message_enabled → send_wa_reply(ticket, escalation_message)
       db_set HD Ticket: bot_escalated=1, bot_active=0
```

---

## LLM Abstraction (`llm.py`)

```
chat(messages: list[dict], images: list[bytes] | None) -> str

messages format: [{"role": "system"|"user"|"assistant", "content": str}, ...]
images: raw bytes list appended to last user message

Gemini Flash 2.0:
  - SDK: google-generativeai
  - Model: gemini-2.0-flash
  - System prompt via system_instruction=
  - Images as inline_data base64 parts

Claude Haiku 4.5:
  - SDK: anthropic
  - Model: claude-haiku-4-5-20251001
  - System prompt via system= parameter
  - Images as base64 content blocks
```

Provider selected via `Helpdesk Bot Settings.llm_provider`. Swap in settings, zero code changes.

---

## File Structure

| File | Action |
|------|--------|
| `helpdesk/integrations/bot.py` | Create — orchestration, background job, doc_event handlers |
| `helpdesk/integrations/llm.py` | Create — Gemini + Haiku provider adapters |
| `helpdesk/helpdesk/helpdesk/doctype/helpdesk_bot_settings/` | Create — singleton DocType |
| `helpdesk/helpdesk/helpdesk/doctype/hd_bot_missing_kb_query/` | Create — gap tracking DocType |
| `helpdesk/helpdesk/helpdesk/doctype/wa_line/wa_line.json` | Modify — add bot_enabled field |
| `helpdesk/helpdesk/helpdesk/doctype/hd_ticket/hd_ticket.json` | Modify — add bot_reply_count, bot_escalated, bot_active |
| `helpdesk/helpdesk/fixtures/whatsapp_account_bot_field.json` | Create — Custom Field for WhatsApp Account.bot_enabled |
| `helpdesk/hooks.py` | Modify — add doc_events + fixtures entry |
| `helpdesk/pyproject.toml` | Modify — add google-generativeai, anthropic |
| `helpdesk/helpdesk/tests/test_bot.py` | Create — bot logic tests |
| `helpdesk/helpdesk/tests/test_llm.py` | Create — LLM provider routing tests |

---

## Error Handling

- LLM call failure → log error → escalate ticket (never leave customer unresponded)
- Image download failure → log error → proceed without image (text-only LLM call)
- Reply send failure → log error → return (do not increment reply count)
- Gap tracking failure → log error → continue (non-critical path)
- All handlers wrapped in try/except; `frappe.log_error` used throughout

---

## Key Constraints

- `send_wa_reply(ticket=ticket_name, message=reply)` is the single send path for both channels — it routes internally based on `HD Ticket.baileys_jid`
- `WA Message.line` → Link to WA Line (for bot_enabled check)
- `WhatsApp Message.whatsapp_account` → Link to WhatsApp Account (for bot_enabled check)
- `WA Message.direction` field values: "Incoming" / "Outgoing"
- `WhatsApp Message.type` field values: "Incoming" / "Outgoing"
- Bot only fires on Incoming messages
