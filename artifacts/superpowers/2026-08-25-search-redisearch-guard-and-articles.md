# Search: RediSearch capability guard + HD Article in the SQLite index

Date: 2026-08-25
Branch: `fix/search-redisearch-guard-and-articles`

## Problem

`helpdesk.search.build_index` failed on every scheduler tick with
`redis.exceptions.ResponseError: unknown command 'FT.CREATE'` — 226 occurrences in
the current `worker.error.log`.

Root cause is not a dev-only Redis gap. Upstream helpdesk ships **two** search
backends and has only half-migrated between them:

| | Legacy | Current |
|---|---|---|
| Module | `helpdesk/search.py` (RediSearch) | `helpdesk/search_sqlite.py` (SQLite FTS5) |
| Wired via | `hooks.py:26` `after_migrate`, `hooks.py:38` `scheduler_events["all"]` | `hooks.py:34` `sqlite_search`, driven by frappe core |
| Serves | `api/article.py` -> `SearchArticles.vue` | `api/search.py` -> the search page |

`git show upstream/develop:helpdesk/hooks.py` carries the identical block, so this
is upstream state, not fork divergence.

The job can never succeed and never stops: `Search.index_exists()`
(`search.py:206-214`) wraps its lookup in `suppress(ResponseError)`, so a Redis
with no query engine reports "no index" rather than "no engine", and
`build_index_if_not_exists` rebuilds forever.

Two further findings from the investigation:

- `HelpdeskSearch.INDEXABLE_DOCTYPES` covered HD Ticket, HD Ticket Comment and
  Communication — **not HD Article**. Articles were reachable only through the
  broken RediSearch path.
- The dev index was stale: 8,414 HD Tickets indexed on 2026-06-02 against a site
  that now holds 0.

Bench Redis is `redis-server 8.8.0` (local build at `frappe-bench/redis-server`),
`MODULE LIST` shows only `vectorset`.

## Changes

### 1. Capability guard (`helpdesk/search.py`)

New `is_redisearch_available()` probes `FT._LIST` and distinguishes "command does
not exist" from "index does not exist". Guards `build_index_if_not_exists()` and
`build_index_in_background()` — the two automatic entry points behind the flood.

Deliberate choices:
- Guard rather than delete the `hooks.py` lines: additive, so it does not conflict
  on every `git merge upstream/develop`, and it is PR-able upstream.
- Result is **not cached**: a bench that later gains the module resumes indexing on
  its own, with no cache to clear.
- Any error other than "unknown command" reads as *available* — a broken index or
  flaky connection is not a capability answer.
- `build_index()` itself is left unguarded so a manual call still fails loudly.

### 2. HD Article in the SQLite index (`helpdesk/search_sqlite.py`)

- Added `HD Article` to `INDEXABLE_DOCTYPES`, filtered to `status = "Published"`
  so Draft/Archived never enter the index.
- Permission encoding: the base class ANDs one `reference_ticket IN (...)` clause
  onto every query and offers no way to express "OR doctype = 'HD Article'", so
  articles ride that column with sentinels — `ARTICLE_PUBLIC_KEY = 0`,
  `ARTICLE_INTERNAL_KEY = -1`. Ticket names are positive autoincrement ints cast
  with `int()`, so both sentinels are unreachable as real tickets.
- `_get_accessible_tickets()` appends the public sentinel always and the internal
  one only for agents, reproducing the visibility rule the RediSearch path applied
  on read. The raw lookup moved to `_get_accessible_ticket_names()`.
- Side benefit: an empty accessible-ticket list compiles to `1=0` in the base
  class, so a site with no visible tickets previously returned nothing at all.
  Articles now survive that.

### 3. Index rebuild

Dropped and rebuilt on `dev.localhost`, clearing the 8,414 stale ticket rows.

## Verification

Guard, against the live bench:

```
redisearch available -> False
build_index_if_not_exists -> returned cleanly (no FT.* command issued)
build_index_in_background -> returned cleanly (no FT.* command issued)
```

Index contents after rebuild:

```
sqlite> select doctype, count(*) from search_fts group by doctype;
Communication|5380
HD Article|184
sqlite> select reference_ticket, count(*) from search_fts
        where doctype='HD Article' group by reference_ticket;
-1|37     <- internal
0|147     <- public
```
184 indexed == 184 Published articles in the DB.

End-to-end, real data:

```
public article   / as customer  found_target=True   matches=6
public article   / as agent     found_target=True   matches=8
INTERNAL article / as customer  found_target=False  matches=11
INTERNAL article / as agent     found_target=True   matches=14
filter options doctypes -> {'HD Article': 184}
```

Internal-article leak audit, real customer session (no mocking), 40 query terms
drawn from the internal articles' own titles:

```
agent-hb-test-5c633551@backup.internal
  is_agent=False  article hits=323  INTERNAL LEAKED=0
  filter option counts: {'HD Article': 147}
Administrator
  is_agent=True   article hits=416  INTERNAL LEAKED=34
  filter option counts: {'HD Article': 184}
```

Tests: `helpdesk/tests/test_search_index.py`, 23 cases covering the four
`is_redisearch_available()` branches, both guarded entry points, the
still-runs-when-available case, sentinel assignment, sentinel/ticket collision,
internal visibility per role, and the empty-accessible-ticket regression.

`TestInternalArticleVisibility` deliberately does NOT mock `is_agent`. The
property that matters is not "the non-agent branch filters correctly" but "a real
customer session takes that branch" -- a mocked gate proves the former and would
keep passing if the gate stopped being consulted at all. It drives
`helpdesk.api.search.search`, the whitelisted endpoint, because the UI routes the
search page only for agents but the endpoint is reachable by any logged-in user,
so UI routing is not the control. It also asserts the filter-option counts, which
are a side channel: a customer must not learn how many internal articles exist.

```
bench --site dev.localhost run-tests --module helpdesk.tests.test_search_index   -> 23 OK
bench --site dev.localhost run-tests --module helpdesk.tests.test_article_product_tagging -> 6 OK
bench --site dev.localhost run-tests --module helpdesk.tests.test_article_product_tags    -> 10 OK
```

## Review pass

- **Blocker**: none.
- **Major**: none.
- **Minor**: `get_filter_options()`'s early return on an empty accessible-ticket
  list is now unreachable, since the list always carries the public sentinel. Left
  in place — harmless, and the method is upstream-owned.
- **Minor**: `is_agent()` now runs once per search inside `get_search_filters()`.
  It is roles-cached with at most one `db.exists`, and the same method already runs
  a full `frappe.get_list("HD Ticket")`, so this is not a new order of cost.
- **Nit**: mocking on `HelpdeskSearch` needs a plain function, not a MagicMock —
  `get_scoring_pipeline()` sweeps in any attribute answering
  `hasattr(attr, "_is_scoring_function")`, and a mock answers True to every
  `hasattr`. Noted in a comment in the test.

## Out of scope / still open

- `helpdesk/api/article.py::search` **still calls the RediSearch path**, so the
  article-suggestion widget (`SearchArticles.vue`, used by `SearchPopover.vue:33`
  and `TicketNew.vue:72`) and the Ticket Search Analysis report remain broken on a
  Redis without the query engine. Indexing articles is the prerequisite; the
  repoint is the follow-up. It needs care: that endpoint returns a
  `[{title, items}]` shape with highlighting and does NLTK/textblob noun-phrase
  query expansion. **`SearchArticles.vue` renders under `v-if="isCustomerPortal"`
  (`TicketNew.vue:73`), so that endpoint is a customer-facing surface** -- the
  repoint MUST carry the internal-article gate across, or customers would be
  offered internal articles on the new-ticket form. `api/article.py` currently
  applies that gate on read (`search.py:381`); the SQLite path applies it through
  `_get_accessible_tickets()` instead.
- Frappe Cloud has not been checked. If prod's Redis also lacks the query engine,
  article suggestions are broken there too, silently. Check with
  `frappe.cache().execute_command("MODULE", "LIST")` or grep the FC error log for
  `FT.CREATE`.

---

# Follow-up: article search moved off RediSearch

Date: 2026-08-25
Branch: `fix/article-search-off-redisearch`

## Why

The "out of scope" item above became the work. `helpdesk/api/article.py::search`
still called the RediSearch path, so on any host without the module it raised
`unknown command 'FT.SEARCH'` and `SearchArticles.vue` rendered **nothing at
all** -- its "No answers found" state only appears on an empty *successful*
response, so a hard failure was indistinguishable from no matches. That widget
renders under `v-if="isCustomerPortal"` (`TicketNew.vue:73`), making it a
customer-facing surface.

Evidence on prod was suggestive rather than conclusive: the user reported the
widget doing nothing, but a JS error in the page (`Autocomplete.vue` TypeError)
could produce the identical blank, since `articles.data` is `undefined` whether
the request failed or was never made. The XHR status was requested to settle it.
The repoint is correct either way -- it removes the dependency rather than
diagnosing around it.

## Decisions taken (user-chosen)

1. **Whole-article results.** The RediSearch index stored one document per
   heading section (`name` = `<article>#<heading>`), so suggestions deep-linked
   into an article. SQLite stores whole articles. Chose whole-article links over
   reproducing section indexing.
2. **No query-expansion cascade.** The old endpoint retried through textblob/NLTK
   noun phrases and nouns, AND then OR -- compensation for RediSearch's strict
   AND matching, which also put an NLTK corpus download on a customer-facing
   path. One query now replaces up to five.

## Changes

- `helpdesk/api/article.py` -- rewritten onto `HelpdeskSearch`, filtered to
  `doctype: HD Article`, capped at `NUM_RESULTS`. Keeps `score` in the response:
  the Ticket Search Analysis report sums it, and dropping the key would have been
  a `KeyError` in a report nobody runs often enough to notice quickly.
- `helpdesk/search_sqlite.py` -- `HelpdeskSearch(articles_only=True)` skips the
  `frappe.get_list("HD Ticket")` permission lookup. The widget fires one search
  per debounced keystroke and can never match a ticket, so on a busy site that
  list was the most expensive part of an articles-only request.
- `desk/src/components/SearchArticles.vue` -- route param built from `name`
  directly; the old `name.split('#')[1]` would have produced `hash: "#undefined"`.
  Heading suffix rendered only when present.

## Bug found by the new tests

`INDEXABLE_DOCTYPES[...]["filters"]` is honoured **only by the bulk build**.
Frappe's `update_doc_index` doc_event calls `index_doc` directly and never
consults config filters, so every HD Article was indexed on save -- Draft and
Archived included. The earlier commit claiming "Draft and Archived articles never
enter the index" was wrong for the on-save path.

Worse was the transition: publishing indexed an article, and unpublishing left
the row from when it *was* published, because the base `index_doc` simply does
nothing when `prepare_document` declines. A withdrawn article stayed searchable.

Fixed at the one funnel both paths share: `_passes_index_filters()` applied in
`prepare_document`, plus an `index_doc` override that **evicts** a document that
no longer qualifies. The check is generic over the declared filters, so it also
closes the same hole for `Communication` (`reference_doctype: HD Ticket`).

Latent rather than realised on this site: the live index held 184 Published rows
and no drafts, only because it had just been rebuilt. It would have fired the
first time anyone edited one of the 2 Draft or 5 Archived articles.

## Verification

`helpdesk/tests/test_article_search_api.py`, 14 cases -- RediSearch is not
touched, customers get results, internal articles are withheld from customers
(including on a query that matches both fixtures) and shown to agents, drafts are
never offered, unpublishing evicts, response shape matches what the widget reads,
`score` survives for the report, `name` carries no anchor, results are capped, and
punctuation-only input returns `[]` rather than raising.

```
test_search_index          -> 23 OK
test_article_search_api    -> 14 OK
test_article_product_tagging -> 6 OK
test_article_product_tags  -> 10 OK
```

Against the 184 real articles, 8 queries, both roles:

```
as customer   backup 3 hits | restore 3 | printer 5 | tremol 5 | etims 5 ...
              top for "restore" = "Backup & <mark>Restore</mark>"
              INTERNAL LEAKED: 0
as agent      backup 5 hits | restore 4 | ...
              top for "restore" = "<mark>Restore</mark> DB"  (internal)
              INTERNAL LEAKED: 12  (correctly)
```

Highlighting survives the move. `bench build --app helpdesk` clean.

## Review pass

- **Blocker**: none.
- **Major**: none remaining -- the draft-indexing hole was found and fixed here.
- **Minor**: fixture naming matters in this codebase. The index tokenizer is
  `unicode61 ... tokenchars '-_'`, so it treats hyphens and underscores as WORD
  characters while `sanitize_query` strips them. Fixtures named `_test-foo-bar`
  can never be found, which reads as a broken endpoint rather than a broken
  fixture. Noted in a comment at the top of the test module.
- **Minor**: recall could differ from the old cascade on multi-word queries. A
  direct comparison was impossible -- the legacy backend cannot run on this bench
  at all -- so the new path was validated against real articles instead of
  diffed against the old one.
- **Nit**: `helpdesk/search.py` is now reachable only from `hd_ticket.py`'s unused
  import. Deleting the module is a bigger cleanup and stays upstream's call.

---

# Follow-up 2: UI defects from live use

Date: 2026-08-25
Branch: `fix/portal-context-autocomplete-theme-and-snippets`

Four defects reported from screenshots, plus one found while fixing them.

## 1. Sidebar collapsed on /helpdesk/backup (regression, mine)

`router/index.ts` sets `isCustomerPortal.value = to.meta.public || false`, and the
BackupPortal route was given `public: true` so customers could reach it. `public`
does double duty -- access gate AND portal-mode switch -- so an agent clicking
Imara Backup was flipped into customer mode and lost Dashboard, Notifications,
Tasks, Customers, Contacts and every WhatsApp line until they navigated away.

Missed during the original verification: the screenshot showed the REDUCED
sidebar and was read as "sidebar intact".

Fix: a `sharedPortal` meta flag for routes both audiences use. Portal context
then follows the USER (`!authStore.hasDeskAccess`) instead of the route. The
assignment moved after `authStore.init()`, which is what populates
`hasDeskAccess`.

Note for future reports: the reduced sidebar on `/helpdesk/my-tickets/new` is
CORRECT. That is a customer route, and `SearchArticles` only renders under
`v-if="isCustomerPortal"`.

## 2. Support Status not dark

`hd_product` is a Link (renders `Link.vue`, already themed); `support_status` is
a Select, which `UniInput` renders with `Autocomplete.vue` -- a component still
on raw Tailwind (`bg-white`, `bg-gray-100`, `text-gray-800`, `border-gray-300`).
Raw grays do not flip with `data-theme`. Converted to semantic tokens
(`bg-surface-*`, `text-ink-gray-*`, `border-outline-gray-*`), which fixes every
Select field in the app, not just this one.

## 3. Search results showed markup and raw markdown

Two separate causes:

- **Literal `<mark>` in titles.** SQLite's `highlight()` marks the title too, and
  the widget interpolates the title as TEXT. Titles were never highlighted before
  the backend move, so the tags are stripped server-side rather than starting to
  render untrusted markup somewhere new. The description keeps its highlighting;
  it was already `v-html`.
- **Raw markdown in snippets.** Article bodies are HTML wrapping markdown source
  (the Outline sync drops markdown into a `<pre>`). The base indexer strips HTML,
  leaving `## `, `![](...)`, `| Brand | IPs |`, `:::warning`, `**bold**` in the
  indexed text. Added `strip_markdown()`, applied by overriding `_process_content`
  so it runs at INDEX time -- the search page benefits too, and the punctuation
  stays out of the FTS vocabulary.

Two ordering subtleties worth keeping:
  - Literal `\n` / `\r` / `\t` sequences (not real whitespace) survive the base
    indexer's whitespace collapse and read as word characters to every pattern,
    which is how `excel\n#### MYSQL` kept its heading marker. They are stripped
    FIRST. 50 of 184 articles here contained them.
  - The base indexer rewrites bare URLs to `[link]` BEFORE this runs, eating an
    image's closing paren and leaving `![]([link]`. Matched separately.

## 4. `UniInput.vue` TypeError (from the user's console)

`@change="... $event.target?.value ..."` -- optional-chained after `.target` but
not before it. Autocomplete emits `change` with `null` when a selection is
CLEARED, so reading `.target` off null threw
`can't access property "target", i is null`. Every hop is now optional-chained.

## 5. Found while fixing: a full index rebuild could not complete

`filters: {"status": "Published"}` was declared without adding `status` to
`fields`. `get_documents_paginated` SELECTs only declared fields, so during a
BULK build `status` read as None, every article failed `_passes_index_filters`,
nothing was indexed, the progress cursor never advanced, and the build spun
forever -- 50MB of repeated progress output, and the index left dropped.

The existing tests could not catch it: they index through `index_doc`, which goes
via `frappe.get_doc` and has every field. So the suite stayed green while a full
rebuild was broken.

Fixes: declare `status`; make `_passes_index_filters` fall back to a DB lookup
when a filtered field was not selected, so the failure is a slow build rather
than a silently empty index; and add `TestIndexConfig`, which asserts every field
named in `filters` is also in `fields`. That class is deliberately NOT gated on
the index existing -- the bug makes the index impossible to build, so a
skip-when-missing class would skip exactly when it matters.

## Verification

```
test_search_index            -> 24 OK
test_article_search_api      -> 26 OK
test_article_product_tagging ->  6 OK
test_article_product_tags    -> 10 OK
test_contact_verification    -> 40 OK
```

Index rebuilt: 184 HD Article + 5,422 Communication rows. Residual markdown in
indexed content, out of 184 articles: images 0, table rules 0, task boxes 0,
literal escapes 50 -> ~3, one unbalanced `**` and one odd `####` from malformed
source. Cosmetic residue inside a line-clamped snippet.

In a browser at `/helpdesk/my-tickets/new`, dark mode, subject "tremol":
titles render clean, snippets read as prose ("Software Reset Fpcmdke-service
tab-software reset Password is F142HZ Network settings will get reset" where the
screenshot had "## Software Reset ... :::warning"), the table row lost its pipes,
`<mark>` highlighting still renders in the snippet, and both selects are dark.

At `/helpdesk/backup` the full agent sidebar is present: Dashboard, Notifications,
all WhatsApp lines, Analytics, Home, Tickets, Tasks, Knowledge Base, Customers,
Contacts, WhatsApp Business.

## Review pass

- **Blocker**: none.
- **Major**: none remaining.
- **Minor**: a snippet can still show `<[link]` where the source had a bare URL
  inside angle brackets. The `[link]` substitution is the base class's.
- **Minor**: `strip_markdown` is regex-based, not a parser. It is deliberately
  conservative -- the hyphenation test exists because an over-eager table-rule
  pattern would eat `tab-software` and `FT-100MX`.
- **Nit**: verifying a CSS or index change in the browser needs a browse-daemon
  restart; `portal.css` and the debounced widget both served stale state once
  each during this work.

## Not fixed here

The WhatsApp bot's PIN request. `hooks.py` dispatches only
`bot.handle_wa_message` on `WA Message`, so the WA Line (Evolution) path never
calls `wa_verification.handle_incoming` -- the prompt is never sent and a PIN
reply is never recorded. The WABA path does both. The PIN extractor itself was
tested and is correct. Wiring WA Line up is not a one-liner: `send_prompt()` calls
`_send_fw_reply`, which is WABA-only, and would need to route through
`send_wa_reply` per channel. Awaiting a decision.

---

# Follow-up 3 (reverted): WA Line PIN verification

Date: 2026-08-25

Wiring unknown-contact verification into the WA Line (Evolution) path was
implemented, merged, pushed -- and then reverted, because the requirement does
not exist. **WA Line conversations are not asked for a KRA PIN. Verification is
WABA-only by design**, that being the channel with the customer/ticket linkage
the claim is meant to resolve against.

The investigation behind it was sound and the finding was real: `wa_verification`
has exactly one dispatch site, on the WABA path, so a PIN volunteered on a WA
Line is not recorded. What was wrong was the conclusion that this is a defect. It
is the intended scope.

Reverted with `git revert -m 1`, so the prior state is restored in history rather
than rewritten. What was kept instead, because the same wrong conclusion is easy
to reach again -- the two channels are handled symmetrically nearly everywhere
else, so the missing `WA Message` entry reads like an oversight:

- The `wa_verification` module docstring now states the scope and says plainly
  that it was once "fixed" this way.
- `hooks.py` carries a note at the `WA Message` dispatch point.
- `TestScopeIsWabaOnly` asserts the ABSENCE: no handler on `WA Message` may
  mention `wa_verification`. It also asserts the bot still runs there and that
  the WABA prompt still goes out, so the revert cannot have overshot.

Cost of the mistake: one commit written, merged and pushed, then reverted. The
lesson worth keeping is that "channel A does X and channel B does not" is a
question for the owner, not a bug to close -- the asymmetry was load-bearing.

## Still unexplained

The incident that started this: a customer replied with a PIN and it was ignored.
If verification is WABA-only and that conversation was on WABA, then a real
defect remains and has not been found. The next thing to read is that contact's
`hd_verification_status` on production:

- `Unverified` -> the prompt never went out; the question is why, not why the
  reply was dropped.
- `Asked` or `Claimed` -> the prompt went out and the claim path is the suspect.

Also worth checking there: `verification_enabled`, which is **0** on dev, and
whether that contact already resolves to a customer via `get_customer()`, which
makes `_handle` return before asking anything.


---

# Follow-up 4: a volunteered PIN was silently discarded

Date: 2026-08-26
Branch: `fix/record-unprompted-pin`

## Reported

Two contacts on production, unlinked to any customer, who shared their KRA PIN.
Nothing was recorded.

## Cause

`_handle` read the claim only inside `if status in (ASKED, CLAIMED)`. A PIN
arriving in any other state was dropped -- no error, no log, nothing for an agent
to see. Reproduced locally in two ordinary situations:

**A. The PIN is in the customer's first message.** The contact is still
Unverified, so the PIN was discarded and the prompt sent -- the bot asking for
exactly what it had just been given.

```
"Hi, my PIN is P051234567X, printer broken"
  prompts sent: 1 | hd_claimed_tax_id: None | status: Asked
```

**B. The prompt send failed.** A failed send leaves the contact Unverified on
purpose so the ask retries. That also meant every later PIN was dropped and the
prompt re-sent, indefinitely.

```
prompt raises ("outside 24h window")   -> status Unverified
"P051234567X"                          -> hd_claimed_tax_id: None, prompt re-sent
```

Both end exactly where the report did. Neither needed production access to
confirm.

Note this is channel-independent, which retires the WA Line theory from
follow-up 3 twice over: wrong channel, and the real defect had nothing to do with
channels.

## Change

Read the claim from ANY non-terminal inbound message. `TERMINAL` is still checked
first, so a Verified or Rejected contact is untouched and an agent's decision
stays final. Recording without an ask is safe because a claim is not
authorisation -- it is evidence an agent approves.

Accepted trade-off, chosen by the owner: any PIN-shaped token from an unlinked
contact now becomes a claim, so a device serial could land there. Nothing
auto-links, the claim is visible, and a later PIN overwrites it.

After:

```
A: prompts sent: 0 | status Claimed | hd_claimed_tax_id P051234567X
B: prompts sent: 0 | status Claimed | hd_claimed_tax_id P051234567X
```

## Verification

`test_contact_verification.py` 43 -> 49. `TestUnpromptedPin` covers the PIN in a
first message, no redundant prompt when the PIN is already present, recording
after a failed prompt, the ordinary no-PIN path still asking, and both terminal
states still ignoring a volunteered PIN.

```
test_contact_verification -> 49 OK
test_bot                  -> 24 OK
test_bot_product_scoping  ->  9 OK
test_entitlement_api      ->  8 OK
```

## Review pass

- **Blocker**: none.
- **Major**: none.
- **Minor**: unprompted claims will be noisier than prompted ones. If that shows
  up in practice, the narrower option is a stricter pattern for the unprompted
  case only (`^[A-Z]\d{9}[A-Z]$`), at the cost of missing the nine malformed PINs
  known to exist in the ERPNext data.
- **Minor**: a failed prompt send is still only visible in the error log. Worth
  surfacing, but out of scope here.
- **Nit**: `verification_enabled` is **0** on dev. Whether it is on in production
  is still unconfirmed, and if it is off none of this runs at all.

## Not verified against production

No route to it from this bench: the only helpdesk site here is `dev.localhost`
(`host_name = https://dev.cecypo.tech`), production is
`cce-support.jh.frappe.cloud`, and there are no stored credentials, no
`~/.frappe-cloud`, no `.netrc`, no `fc` CLI. Neither reported number
(+254746127115, +254746771116) exists on this bench, which holds 0 HD Tickets.
The fix is confirmed by local reproduction of the exact reported state, not by
reading the live records.

---

# Follow-up 5: the claims had nothing to resolve against

Date: 2026-08-27
Branch: `feat/full-customer-resync`

## Reported

"PIN being asked for now, but no update being set after that", and separately a
"pin / no company issue".

## What production actually showed

Queried directly (read-only) with the API token at `~/.cecypo-support-api`.

The capture was never broken. `verification_enabled = 1`, prompts going out,
**13 claims recorded** — including one from minutes before the report:

```
victor sum    254710249736  P051199670Z  Claimed
Annie Muthoni 254746127115  P000591377X  Claimed
Nikunj Patel  254746771116  P051187945J  Claimed
```

What was broken sat one step later. `match_claim()` resolves a claimed PIN
against `HD Customer.tax_id`, and production had **249 HD Customers with 5
tax_ids**. Every claim resolved to zero candidates: no company, nothing for an
agent to approve, no update. Checked all 13 — not one matched.

## Why the customers were missing

The sync looked healthy, which is what made this hard to see: `enabled=1`,
`erp.cecypo.com`, `last_customer_sync` **11:59 that same day**, entitlements
12:02.

But `sync_customers()` is incremental — it asks only for rows modified since the
watermark. The initial backfill never happened, so it had been faithfully
mirroring the handful of recently-changed customers ever since, reporting success
each time. The docstring already warned about exactly this:

> "the first time this ran against production it did nothing at all, because a
> stale watermark left by the test suite made it ask for records newer than any
> that existed."

And **"Sync Now" could not fix it**: `enqueue_customer_sync()` called
`sync_customers` with no `full=True`. The `full` escape hatch had no caller
anywhere outside a test — no button, no whitelisted argument — so on Frappe
Cloud, with no console, it was unreachable.

## A — production unblocked

Cleared `last_customer_sync` (baseline recorded first:
`2026-08-27 11:59:16.867223`, 249 customers, 5 tax_ids) and triggered the sync.
With no watermark, `since` is empty and the incremental path reads everything.

```
before   customers=249    with_tax_id=5
+40s     customers=804    with_tax_id=587
final    customers=3033   with_tax_id=2803
```

2,803 — exactly the figure the `verification.py` comment cites. All **14** claims
now resolve, and the names corroborate the PIN matching rather than merely
agreeing with it:

```
254746127115  P000591377X -> Slater & Whittaker Ltd        (contact: "Annie(Slater)")
254746771116  P051187945J -> Jalaram merchandise Ltd       (contact: "Nikunj Patel (Jalaram Merchandise)")
254710249736  P051199670Z -> PAVAN AUTO HARDWARE LIMITED
```

## B — so it never needs a console again

- `enqueue_customer_sync(full=False)` now takes and forwards the flag, through
  `cint` so the string a button sends ("1"/"0") behaves.
- A separate `job_id` for full runs: `deduplicate()` matches on job_id alone, so
  sharing one would let an in-flight incremental job swallow the resync.
- A **Full Resync** button on the settings form, behind a confirm, since it
  re-reads every customer rather than the changed handful.

## Verification

```
test_erpnext_customer_sync -> 21 OK
```

`TestFullResyncIsReachable` covers: default stays incremental, `full` is passed
through, the string form a button sends is handled (so "0" does not read as
True), full runs get their own job_id, and the settings form actually exposes the
button — a whitelisted argument nobody can reach is the same bug again.

## Review pass

- **Blocker**: none.
- **Major**: none.
- **Minor**: the 14 claims are resolvable but still unapproved — 0 Verified, 0
  Rejected. Whether agents have a surface that shows them a pending-claims queue
  is unconfirmed and is the next thing to look at.
- **Minor**: nothing detects "mirror is implausibly small". The sync reports
  success by advancing a watermark, not by reconciling counts, so this class of
  failure stays silent until someone notices downstream.
- **Nit**: production write was one field on a Single, with the prior value
  recorded above; everything else this session was read-only.

---

# Follow-up 6: a queue for pending claims

Date: 2026-08-28
Branch: `feat/pending-claims-queue`

## Why

Approve/Dismiss live on a ticket's Contact tab, so a claim was only ever
discoverable by opening the one ticket it arrived on. Thirteen claims sat unread
on production for two days. Nothing anywhere said a number out loud.

## Shape (user-chosen)

- **The queue lists; the ticket approves.** A row opens its ticket, where the
  existing panel does the linking. Keeps the decision next to the conversation it
  rests on, and leaves exactly one code path able to link a contact to a customer.
- **Sidebar entry with a count badge, hidden when empty.** An entry that is empty
  most days trains agents to skip it; the count is the point.

## Changes

- `api/verification.py`: `get_pending_claims()` returns each claimed contact with
  its PIN, matches and the newest ticket to open. `get_pending_claim_count()` is
  separate and deliberately cheap — the badge is fetched on every page and must
  not pay for `match_claim`, which reads every HD Customer with a tax_id once per
  claim. Both `@agent_only`; both return empty on an unmigrated site.
- `pages/desk/verification/PendingClaims.vue`: the list. States "No customer
  matches that PIN" explicitly — that is a data problem, not something to approve.
- `stores/verification.ts`: the count, shared by badge and page.
- `Sidebar.vue`: filters the entry out at zero, badges it otherwise, through the
  `linkBadge()` helper that already existed.

## Three bugs found by looking at it

1. **The badge never appeared.** `createResource({auto: true})` defers its first
   fetch to `onMounted`, and a Pinia store has no component instance — the
   request never went out, the count stayed 0, and the entry hid itself forever.
   Confirmed by the absence of any `get_pending_claim_count` row in the network
   log, not by reading code.
2. **Then it 403'd**, because fetching at store-creation races the session. The
   sidebar now calls `refresh()` from its own `onMounted`.
3. **`dayjs(...)` instead of `dayjs.tz(...)`** — the codebase convention. Caught
   because a timestamp stamped seconds earlier rendered as "in 2 hours".

## The timezone finding is a dev misconfiguration, not a bug

Chasing (3) turned up something worth keeping:

```
dev  System Settings time_zone = Asia/Kolkata     <- wrong
prod System Settings time_zone = Africa/Nairobi   <- correct
```

`window.timezone` on dev is `{system: Asia/Kolkata, user: Africa/Nairobi}`. Server
datetimes are stamped in SYSTEM tz and rendered in USER tz, so on dev every
server-derived relative timestamp in the whole app is 2.5 hours out. On production
the two match and it is correct. No code change; the dev site setting is wrong.

## Verification

`test_contact_verification.py` 49 -> 55. `TestPendingClaimsQueue` covers: a
claimed contact appears, the row carries a ticket to open (without it the row is a
dead end), an `Asked` contact does NOT appear (that is waiting on the customer,
not on an agent), `Verified`/`Rejected` leave the queue, the count matches the
list (badge and page are separate endpoints and must not drift), and both are
agent-gated.

Exercised in a browser against three seeded claims: the page rendered all three
with their matched customers (`Slater & Whittaker Ltd`, `PAVAN AUTO HARDWARE
LIMITED`, `NAYOSA ENTERPRISES`) and ticket links, and the sidebar rendered
`Verifications` with a badge of `3`. Fixtures removed afterwards; count back to 0.

## Review pass

- **Blocker**: none.
- **Major**: none.
- **Minor**: the count is fetched once per page load, not polled. A claim arriving
  mid-session does not bump the badge until the agent navigates. Polling every
  agent's session costs more than it tells anyone; realtime would be the better
  upgrade if it matters.
- **Minor**: `get_pending_claims` calls `match_claim` per row, and that reads every
  HD Customer with a tax_id each time — 2,803 rows on production. Fine at 14
  claims, not at 1,000. The count endpoint is separate precisely so the badge
  never pays it.
- **Nit**: I misread a screenshot mid-build and thought the sidebar had lost its
  entries; they were below the fold in a scrollable list. The DOM text was the
  thing that settled it, as with the `<mark>` bug earlier.
