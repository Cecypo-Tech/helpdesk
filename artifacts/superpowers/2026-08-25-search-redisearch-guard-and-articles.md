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

Tests: `helpdesk/tests/test_search_index.py`, 17 cases covering the four
`is_redisearch_available()` branches, both guarded entry points, the
still-runs-when-available case, sentinel assignment, sentinel/ticket collision,
internal visibility per role, and the empty-accessible-ticket regression.

```
bench --site dev.localhost run-tests --module helpdesk.tests.test_search_index   -> 17 OK
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
  query expansion.
- Frappe Cloud has not been checked. If prod's Redis also lacks the query engine,
  article suggestions are broken there too, silently. Check with
  `frappe.cache().execute_command("MODULE", "LIST")` or grep the FC error log for
  `FT.CREATE`.
