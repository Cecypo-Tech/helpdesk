# Finish report: Bot KB category allowlist, semantic search, prior-customer context

Executed 2026-07-14 ~01:40–02:15 EAT by the scheduled local agent, per the approved plan
(`artifacts/superpowers/2026-07-13-bot-category-allowlist-plan.md`). All three tasks done,
verified live on dev.localhost, pushed to develop.

## What was built

### 1. Category allowlist (plan Task 1)
- New child DocType `HD Bot Allowed Category` (Link → HD Article Category).
- `Helpdesk Bot Settings` gains a **KB Access** section with an **Allowed Categories**
  Table MultiSelect. Empty = all published, non-internal articles (behavior unchanged);
  non-empty = the bot only uses those categories.

### 2. Semantic KB search + allowlist enforcement (plan Task 2)
- New DocType `HD Article Embedding` (article/title/embedding, cache-invalidation hooks).
- `embeddings.py`: `embed_articles()` daily job (embeds Published articles, re-embeds
  modified ones), `search_articles()` (cosine ≥ 0.65, fetches article data **fresh** from
  HD Article at query time so stale vectors can't leak unpublished/internal/dis-allowed
  articles), `on_article_update()` doc hook (drops stale embedding on edit/unpublish).
- `bot.py`: the `LIKE %term%` stopword expansion is **gone**. `_search_kb()` is semantic-
  first with a single whole-query LIKE fallback (used until the index is built or when the
  embedding API fails); both paths enforce the allowlist. Outline results are mapped back
  via `outline_doc_id` → HD Article.category and **dropped when unmappable** while an
  allowlist is set. `suggest_agent_reply` and gap tracking inherit the filtering.
- `hooks.py`: `embed_articles` registered daily; `HD Article on_update` hook registered.

### 3. Prior-customer context (plan Task 3)
- `_get_prior_customer_context()` pulls the last **15** messages (`_PRIOR_CONTEXT_MESSAGES`,
  tunable constant) from the customer's *previous* conversations — same JID for WA Line,
  same contact's other tickets for WABA — compacted (200 chars/message, 2.5k total) and
  injected into the system prompt in both `process_message()` and `suggest_agent_reply()`.
  Fully defensive: returns "" on any failure.

## Unplanned but necessary: embedding model was dead (fix included)

`models/text-embedding-004` has been **retired by Google — the API now returns 404**, which
means the existing resolved-ticket RAG had been silently broken (its try/except swallowed
the failures; `HD Ticket Embedding` was empty so nothing was lost). Fixes:
- `embed()` now uses `models/gemini-embedding-001` with `output_dimensionality=768`.
- Cache keys bumped to `_v2`; `cosine_similarity` now returns 0.0 on dimension mismatch
  instead of silently truncating via `zip`.
- Thresholds recalibrated for the new model's similarity scale (measured live on the
  dev.localhost corpus: nonsense/greeting queries top out ≈0.64, genuine matches ≥0.67):
  articles 0.65, tickets 0.75 → **0.70**. Ticket threshold change is safe — the ticket
  table was empty, so all ticket vectors will be built fresh with the new model.

## Incident during verification (resolved, root-caused, regression-proofed)

Running `bench run-tests` for `test_llm`/`test_bot` **wrote test values into the live
Helpdesk Bot Settings** (system prompt, provider, flags, and the API keys in `__Auth`) —
these tests have always done this; tonight it actually bit. Recovery: restored every field
plus the encrypted Gemini key from the 00:00 site backup
(`20260714_023001-dev_localhost-database.sql.gz`) and verified the key decrypts and works
(live embed call OK). Anthropic key was empty before and is empty again.
**Regression fix**: new `helpdesk/tests/settings_guard.py` — both test modules now
snapshot/restore the settings singleton **and its `__Auth` rows** in
setUpModule/tearDownModule. Verified: full suite runs leave settings byte-identical.

## Verification results

- `python -m py_compile` on all changed files: OK. All doctype JSONs parse.
- `bench --site dev.localhost migrate`: OK; both new DocTypes present.
- Article index built: **175/175** published articles embedded.
- Live console checks: exact-title query ranks its article #1; allowlist restricts results
  to the allowed category (5/5 in-category, off-category query returns nothing — no leaks);
  fallback LIKE respects the category filter; prior-context returns "" for
  ticket-without-history and bad args, and produced a correct 726-char transcript for a
  real WA Line ticket. Settings restored to empty allowlist after testing.
- Tests: `test_bot` **18/18 OK** (was 10/14 before: 2 KB-search tests updated for semantic
  behavior + 2 pre-existing stale escalation tests fixed to use `_BotState`; 4 new tests
  added for fallback, allowlist, and prior-context). `test_llm` **3/3 OK** (fixed
  pre-existing stale provider labels). Settings verified intact after both suites.
- gunicorn restarted on final code; `/api/method/ping` 200.

## Reasonable calls made (per "don't ask, note it")

- `on_article_update` deletes the stale embedding row inline (cheap) instead of enqueuing.
- Outline overfetch happens inside `outline.search()`; after category filtering the
  combined result can come back with fewer than `limit` rows — acceptable.
- WA Line messages that predate ticket-linking (no `reference_name`) count as "prior"
  context even if they belong to the current conversation — edge case, harmless.
- `embed_articles` processes up to 500 most-recently-modified published articles per run.

## Deploy notes (production / dev.cecypo.tech)

1. `git pull` on develop, then `bench --site <site> migrate`.
2. Restart gunicorn. No frontend build needed (backend only).
3. Helpdesk Bot Settings → **KB Access → Allowed Categories**: pick the categories the bot
   may use (leave empty for current behavior). Internal-collection articles were already
   excluded; the allowlist is on top of that.
4. Build the index immediately (else it waits for the daily job, which only runs while
   **Enable Bot** is on): `bench --site <site> execute helpdesk.integrations.embeddings.embed_articles`
5. Note: any `HD Ticket Embedding` rows on production were built with the retired model and
   score 0 against new queries (dimension/model mismatch is guarded). Delete them and let
   the daily job re-embed: `DELETE FROM \`tabHD Ticket Embedding\`;` then
   `bench execute helpdesk.integrations.embeddings.embed_resolved_tickets`.
