# Plan: Bot KB category allowlist, semantic KB search, prior-customer context

Scheduled for autonomous execution by a cloud agent (approved by Kushal, 2026-07-13).
Deliverable: commit and push directly to `develop` on `Cecypo-Tech/helpdesk`. No PR needed.
Do NOT add `Co-Authored-By` trailers to commits. Match existing commit style, e.g.
`feat(bot): restrict KB search to allowed article categories`.

Python files in this app are indented with TABS — match the existing style of each file you touch.
Backend-only work; do not touch `desk/` frontend code.

## Context

- Bot logic: `helpdesk/integrations/bot.py` (WhatsApp auto-responder + `suggest_agent_reply`).
- LLM abstraction: `helpdesk/integrations/llm.py` (Gemini default, Claude Haiku alt).
- Embeddings infra (Gemini text-embedding-004): `helpdesk/integrations/embeddings.py`,
  storage doctype `helpdesk/helpdesk/doctype/hd_ticket_embedding/`.
- KB sources: local `HD Article` (fields: title, content, status, internal, category, outline_doc_id)
  and live Outline search in `helpdesk/integrations/outline.py::search()`.
  Outline collections sync 1:1 to `HD Article Category` via `_get_or_create_category(col_name.title())`.
- Settings: `helpdesk/helpdesk/doctype/helpdesk_bot_settings/` (singleton "Helpdesk Bot Settings").
- Scheduler + doc_events: `helpdesk/hooks.py`.

## Task 1 — Category allowlist in Helpdesk Bot Settings

1. New child DocType `HD Bot Allowed Category` at
   `helpdesk/helpdesk/doctype/hd_bot_allowed_category/`:
   - `istable: 1`, single field `category` — Link → `HD Article Category`, reqd, in_list_view.
   - Mirror the JSON/py/`__init__.py` structure of an existing child doctype in the app.
2. In `helpdesk_bot_settings.json`, add a "KB Access" section (place it after the existing
   `kb_search_limit` area / before the escalation section) with field:
   - `allowed_categories` — fieldtype **Table MultiSelect**, options `HD Bot Allowed Category`,
     description: "Leave empty to allow all published, non-internal articles. When set, the bot
     only uses articles in these categories."
3. Semantics everywhere: **empty list = no restriction** (current behavior);
   non-empty = restrict to those categories.

## Task 2 — Replace LIKE %term% search; enforce the allowlist

### 2a. New DocType `HD Article Embedding`
At `helpdesk/helpdesk/doctype/hd_article_embedding/`, mirroring `hd_ticket_embedding`:
fields `article` (Link → HD Article, unique), `title` (Data), `embedding` (Long Text).
Include the same cache-invalidation pattern hd_ticket_embedding uses (check its .py for an
`after_insert` that invalidates a Redis cache; replicate with a new cache key).

### 2b. `embeddings.py` additions
- `_ARTICLE_CACHE_KEY = "bot:article_embeddings_v1"`, same 1h TTL pattern.
- `embed_articles()` — daily scheduled job:
  - Embed every `HD Article` with `status = Published` lacking an embedding row
    (text = title + "\n" + stripped content[:1500], task_type RETRIEVAL_DOCUMENT).
  - Also re-embed articles whose `modified` is newer than their embedding row's `modified`
    (delete row, re-insert).
  - Guard with `_settings().is_enabled` like `embed_resolved_tickets()` does.
- `search_articles(query, top_k=3, allowed_categories=None) -> list[dict]`:
  - Embed query (RETRIEVAL_QUERY), cosine against cached article vectors,
    similarity threshold **0.60** (module constant).
  - For the top matches, fetch **fresh** article data from `HD Article` with filters:
    `status = Published`, `internal = 0` (or null), and — when `allowed_categories` is a
    non-empty list — `category in allowed_categories`. Filtering on the live row (not the
    embedding row) means stale embeddings can never leak a recently-internalized article.
  - Return dicts: `name, title, content, outline_doc_id` (same shape `_search_kb` returns today).
  - Return `[]` on any failure (same defensive style as `search_resolved_tickets`).
- doc_event on `HD Article` `on_update`: enqueue a small handler that deletes the article's
  embedding row (if content/title/status changed) and invalidates the cache, so the daily job
  re-embeds it. Register in `hooks.py` doc_events.

### 2c. `bot.py` search rewrite
- **Delete** `_SEARCH_STOPWORDS` and the per-term `LIKE %term%` expansion in `_search_kb`.
- New `_search_kb(query, limit, allowed_categories=None)`:
  1. Try `embeddings.search_articles(query, top_k=limit, allowed_categories=...)`.
  2. Fallback ONLY if that returns `[]` (no embeddings yet / API failure): a single
     whole-query LIKE — `title LIKE %(q)s OR content LIKE %(q)s` with `%{query}%` — with the
     same Published/non-internal/category conditions. No term splitting.
- `_combined_kb_search(query, limit)`:
  - Read the allowlist once from `Helpdesk Bot Settings` (`allowed_categories` child rows →
    list of `HD Article Category` docnames) and pass it to `_search_kb`.
  - Outline path: when the allowlist is non-empty, filter Outline results by looking up each
    result's `outline_doc_id` → `HD Article.category`. **Drop** results whose article is not
    found locally or whose category is not in the allowlist (conservative: internal/unknown
    docs must never reach the LLM when a restriction is configured). When the allowlist is
    empty, keep current behavior unchanged.
  - Keep the existing dedupe (Outline wins over HD Article rows with the same outline_doc_id).
- `suggest_agent_reply` and `_handle_kb_gap` use `_combined_kb_search`, so they inherit the
  filtering automatically — verify, don't duplicate logic.
- Register `embed_articles` in `hooks.py` under `scheduler_events.daily`.

## Task 3 — Prior-customer conversation context

Goal: when a customer writes in, the bot should recognize repeat issues by seeing their
*previous* conversations (not just the current thread's last 10 messages, which
`_get_conversation_history` already provides).

- Module constant `_PRIOR_CONTEXT_MESSAGES = 15` (top of `bot.py`, easy to tune later).
- New helper `_get_prior_customer_context(channel, jid, line_name, ticket_name) -> str`:
  - **wa_line**: fetch the last ~40 `WA Message` rows for the `jid` (any ticket), drop rows
    whose `reference_name == ticket_name` (those are the current thread), keep the most
    recent `_PRIOR_CONTEXT_MESSAGES`, oldest first.
  - **waba**: from the current `HD Ticket` get `contact` (fall back to `raised_by`); find up
    to 3 most recent other `HD Ticket`s with the same contact; fetch their `WhatsApp Message`
    rows (reference_doctype = HD Ticket), keep the most recent `_PRIOR_CONTEXT_MESSAGES`
    across them, oldest first.
  - Format each as `Customer: …` / `Agent: …`, strip agent suffixes with
    `_strip_agent_suffix`, truncate each message to 200 chars, cap the whole block at
    ~2500 chars. Return "" when there is no prior history. Wrap everything in try/except
    returning "" — this feature must never break message processing (note: `baileys_jid` /
    custom-field queries can raise OperationalError on unmigrated sites; same defensive rule).
- In `process_message()`: when non-empty, append to `system_content`:
  `"\n\nEarlier conversations with this customer (may be a repeat issue — use for context, do not quote verbatim):\n" + block`.
- Same injection in `suggest_agent_reply()`.
- The existing semantic search over resolved tickets stays as-is — it covers the
  "search by issue" angle; this adds the "same customer, repeat issue" angle.

## Verification (cloud environment — no bench/site available)

1. `python -m py_compile` every changed `.py` file.
2. `python -c "import json; json.load(open(...))"` every changed/added DocType `.json`.
3. Re-read the final diff for: allowlist-empty backward compatibility, Outline leak path,
   tabs vs spaces, and that no frontend files changed.
4. Commit in logical units and push to `develop`.
5. End by writing `artifacts/superpowers/2026-07-14-bot-category-allowlist-finish.md`
   summarizing what was done, anything skipped, and this local deploy checklist for Kushal:
   - `bench migrate` (new doctypes + settings field)
   - restart gunicorn (`pkill -f "frappe.app" && bench serve --port 8002 &`)
   - open Helpdesk Bot Settings → set allowed categories
   - send a test WhatsApp message; confirm bot only cites allowed-category articles
   - optionally run `bench execute helpdesk.integrations.embeddings.embed_articles` to
     build the article index immediately instead of waiting for the daily job.
