# AI Integration Review — 2026-07-13

Review of the current AI stack in the helpdesk fork and ranked improvement opportunities.
No code was changed; this is an assessment.

## Current state

| Piece | File | What it does |
|-------|------|--------------|
| LLM abstraction | `helpdesk/integrations/llm.py` | Two providers (Gemini 3.1 Flash Lite default, Claude Haiku 4.5), text + images, single-shot `chat()` |
| WhatsApp bot | `helpdesk/integrations/bot.py` | Auto-responder on both WABA and WA Line: company-name collection, KB search, RAG over resolved tickets, gap tracking, escalation, multi-turn limits, image understanding |
| Agent draft | `bot.suggest_agent_reply` | LLM-drafted reply in both WhatsApp reply boxes (never auto-sent) |
| Embeddings RAG | `helpdesk/integrations/embeddings.py` | Gemini text-embedding-004 over resolved tickets (daily job), brute-force cosine in Python, 0.75 threshold, Redis cache |
| KB autofill | `helpdesk/integrations/kb_autofill.py` | Daily job converts `HD Bot Missing KB Query` gaps into draft HD Articles via LLM |
| KB sources | `bot._combined_kb_search` | LIKE-based search over HD Articles + live Outline search, deduped |

## Key gaps

1. **AI is WhatsApp-only.** Email/portal tickets — the core helpdesk channel — get zero AI. `suggest_agent_reply` is wired only into the two WhatsApp reply boxes.
2. **KB retrieval is naive.** `_search_kb` is `LIKE %term%` with a hand-rolled stopword list, while a semantic-embedding pipeline already exists (used only for resolved tickets) and the app ships a sqlite full-text search (`search_sqlite`) the bot ignores.
3. **No voice-note support.** Bot handles images but not audio — voice notes are extremely common on WhatsApp; unhandled ones fall into the "short message" path.
4. **No summarization/handoff context.** When the bot escalates or an agent opens a long thread, there's no AI summary of what was discussed.
5. **No triage.** New tickets aren't classified (priority, team, ticket type); escalation is purely reply-count based, not intent based ("I want a human" isn't detected).
6. **Fragile structured output.** Gap suggestions parse raw ```` ```json ```` fenced text; both providers support enforced JSON.
7. **No tool use.** Bot can't answer "what's my ticket status?" or look up customer data — it only pattern-matches KB text.
8. **No reliability layer in `llm.py`.** No timeouts, no retries, no cost/usage logging (project rules require timeouts/retries/idempotency for API automations).
9. **No feedback loop.** Nothing records whether a bot answer resolved the conversation or whether agents accept/reject `suggest_agent_reply` drafts.
10. **Gap records not deduped.** The same question phrased differently creates multiple gap rows and multiple draft articles; no frequency ranking.

## Ranked recommendations

### Quick wins (small diffs, reuse existing infra)
- **QW1 — AI draft for email replies.** Reuse `suggest_agent_reply` in the ticket email reply box; add a `channel="email"` history source reading `Communication` records.
- **QW2 — Semantic KB search.** Embed HD Articles with the existing `embed()` helper (mirror `HD Ticket Embedding`), blend with the current LIKE/Outline search. Directly improves bot answer quality and gap-detection accuracy.
- **QW3 — Voice-note transcription.** Gemini accepts audio; transcribe incoming WhatsApp audio and feed as text (also display transcript to agents).
- **QW4 — Timeouts + one retry in `llm.py`,** plus per-call usage logging (provider, tokens, latency, caller) into a small log doctype.
- **QW5 — Structured JSON mode** for gap suggestion and company-name extraction instead of parsing free text.

### Medium (new surfaces, moderate effort)
- **M1 — Escalation handoff summary.** On `_escalate()` (and on agent opening an unassigned bot-handled ticket) generate a 3-bullet summary comment: what the customer wants, what the bot said, what's unresolved.
- **M2 — Auto-triage on ticket creation.** Classify priority/team/ticket type from first message; write as suggestion (or auto-set with an "AI" badge). Also auto-generate a proper subject for WhatsApp tickets.
- **M3 — Intent-based escalation.** Detect "talk to a human"/frustration in the bot flow and escalate immediately regardless of reply count.
- **M4 — Gap clustering.** Use embeddings to dedupe `HD Bot Missing KB Query` records and rank by frequency; autofill the top clusters first, sourcing content from similar resolved tickets, not just the query text.
- **M5 — Resolution-details draft on close.** When resolving, draft `resolution_details` from the thread. Feeds directly back into the RAG index quality.

### Larger bets
- **L1 — Tool-calling bot.** Give the bot tools: ticket status lookup, business-hours/SLA info, handover. Requires moving `llm.py` from single-shot to a tool loop.
- **L2 — Feedback + eval loop.** Track draft acceptance rate (sent-as-is / edited / discarded) and bot-conversation outcomes (escalated vs. silent-resolved); build a review dashboard from it.
- **L3 — Copilot sidebar tab.** A per-ticket AI tab: summary, similar resolved tickets, suggested KB articles, draft reply — one surface unifying the pieces that already exist in the backend.

## Suggested order
QW2 → QW1 → M1 → QW3 → M2, then reassess with usage data from QW4's logging.

## Review pass (severity)
- **Major (existing code, found during review):** `bot.py` KB search runs one `LIKE` per term with no relevance ranking — long messages generate many terms and can match hundreds of articles, truncated arbitrarily by `LIMIT`. QW2 addresses this.
- **Minor:** `llm.py` lacks timeouts/retries (QW4); `_handle_kb_gap` JSON parsing fragile (QW5).
- **Nit:** `_MIN_SIMILARITY = 0.75` and cache TTLs are hard-coded rather than settings fields.
