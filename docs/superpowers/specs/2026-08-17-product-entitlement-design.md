# Product catalogue and customer entitlement

## Problem

Helpdesk has no idea what a customer bought. Three consequences:

1. **Agents guess.** Nothing on a ticket says whether this customer runs the POS,
   eTIMS, TIMS, or a Frappe-based product, so the agent works it out from the
   conversation every time.
2. **The bot answers from the whole knowledge base.** `_get_allowed_categories()`
   (`helpdesk/integrations/bot.py:38`) reads a single global allowlist from
   `Helpdesk Bot Settings.allowed_categories` and applies it identically to every
   conversation. `_combined_kb_search(query, limit)` (`bot.py:108`) takes no
   customer context at all, so a POS-only customer can be answered from eTIMS
   articles.
3. **Support entitlement is invisible.** There is no record of what a customer is
   entitled to, or until when, so out-of-contract support is absorbed silently and
   renewal conversations have no data behind them.

`HD Ticket.product` exists in upstream as a `Select` with the literal options
`Product A / Product B / Product C`. It is referenced by **zero** lines of Python
or Vue in this repo — a stub nobody filled in.

## Goal

Model what each customer owns, surface it to agents on the ticket, scope the bot's
knowledge base to it, and record whether support was in contract at the moment a
ticket was raised.

Explicitly **advisory**: nothing here refuses service. This follows the same
principle settled for account standing — flag and route, never block.

### Standing invariant: WhatsApp support works with no ERPNext

Helpdesk and ERPNext run on **separate benches**. Production helpdesk has no
`erpnext` app, and that must never change. This applies to every sub-project, not
just E.

The risk is not hypothetical. `hooks.py:125-136` wires doc_events for
`User Permission` and `DocShare` — core Frappe doctypes present on every site —
to handlers in `helpdesk.integrations.erpnext`, and `ALLOWED_DOCTYPES` includes
`HD Customer`, which also exists without ERPNext. Those hooks fire on a
helpdesk-only site and enter the mirror path.

They are correctly guarded today: every path funnels through `should_sync()`
(`integrations/erpnext/utils.py:16`), which returns `False` when `erpnext` is
absent from the installed-app list. `HD Customer.erpnext_customer` is a plain
`Data` field, not a Link, so there is no foreign key to a missing doctype.

Verified by `helpdesk/integrations/tests/test_no_erpnext.py`. Because this bench
*has* erpnext installed, the guarantee is proved in two halves: that the guard
detects the missing app, and that with sync inactive the full WhatsApp ingestion
path — ticket creation, contact creation, idempotency — still works. Patching
`frappe.get_installed_apps` globally is not a viable simulation; Frappe's own hook
resolution calls it and raises `AppNotInstalledError` for an app the site
genuinely has.

**Every sub-project must keep that test passing**, and any new ERPNext-touching
code must sit behind the same guard.

## Design

### Data model

Three new doctypes.

**`HD Product`** — the catalogue. Expected size is 5–15 rows, flat, no hierarchy.

| field | type | notes |
|---|---|---|
| `product_name` | Data | `autoname: field:product_name` |
| `description` | Small Text | |
| `disabled` | Check | retire a product without deleting history |
| `default_team` | Link → HD Team | routing hint, not enforced |

**`HD Article Product`** — child table on `HD Article`, single `product` Link →
`HD Product`. This is the **precise** signal, and it exists because
`HD Article.category` is a single Link: an article belongs to exactly one
category.

That constraint makes category-only mapping degrade badly. An article relevant to
POS *and* eTIMS but not TIMS must live in a category mapped to exactly those two,
so as the catalogue grows the taxonomy stops describing subjects and starts
describing audience intersections ("POS+eTIMS"). And a genuinely generic article
can only live in one place, so it needs a "General" category mapped to every
product — which fails silently the first time someone adds a product and forgets
to include it.

**Eligibility rule:**

```
article has product tags  → eligible only for those products
article has NO tags       → eligible for every product (generic)
```

Untagged-means-generic is the load-bearing half. It makes the safe state the
default: a newly written article is visible everywhere until somebody narrows it,
rather than invisible until somebody remembers to tag it. Same fail-open instinct
as the empty-intersection fallback below.

**`HD Customer Product`** — one row per entitlement. A standalone doctype, not a
child table on `HD Customer`.

| field | type | notes |
|---|---|---|
| `customer` | Link → HD Customer | |
| `product` | Link → HD Product | |
| `support_expiry` | Date | **blank = no expiry = permanently covered** |
| `source` | Select | `Manual` / `ERPNext` / `POS` — default `Manual` |
| `notes` | Small Text | |

There is deliberately **no `version` field**. The catalogue is flat by decision
and support answers do not currently differ by version; adding one "just in case"
would invite version-scoped articles and a materially more complex KB design for
no present benefit. If versions start to matter, this doctype is where they go.

Naming is hash-based, with a **composite unique index on `(customer, product)`**
added in a patch (`frappe.db.add_index` with `unique=True`).

`autoname: format:{customer}-{product}` was considered and rejected. It would give
idempotent upsert free via the primary key, but `HD Customer` names are
user-editable labels; a rename would either break links or force a `rename_doc`
cascade across every entitlement. The index provides the same upsert guarantee
without coupling row identity to a mutable label.

### Why standalone rather than a child table

Sub-project B will sync entitlements from ERPNext and possibly POS licensing.
Against a standalone doctype with a unique key, that sync is an idempotent
`(customer, product)` upsert. Against child rows it is a reconciliation problem —
`idx` ordering, orphan deletion, and no stable row identity between runs.

A standalone doctype also makes "whose eTIMS support expires within 30 days" an
ordinary report rather than SQL against a child table, and gives `source` somewhere
to live.

**Ownership rule, which B inherits:** a sync may create rows and may update rows it
owns (matching `source`). It must **never** overwrite a row whose `source` is
`Manual`. A human decision outranks a mirror.

### Ticket field

`HD Ticket.hd_product` → Link `HD Product`, added as a **Custom Field via
fixtures**, matching `baileys_jid` / `baileys_line` in this fork. Editing
`hd_ticket.json` directly would conflict on every `git merge upstream/develop`.

This inherits the known consequence documented in `CLAUDE.md` for `baileys_jid`:
on a site that has not run `bench migrate` since the fixtures landed, filtering
`HD Ticket` by `hd_product` raises `OperationalError`. **Every query filtering on
`hd_product` must be wrapped in `try/except`**, exactly as the existing
`baileys_jid` queries are.

Selection is constrained to the customer's entitlements but remains
**overridable** — pre-sales questions, evaluations, and stale mirror data are all
legitimate reasons to pick a product the customer does not own.

Concretely, the constraint is a **UI default, never a server-side validation**: the
product dropdown lists the customer's entitled products first and offers "show all
products" to reach the rest. The server accepts any active `HD Product`. Choosing
an unentitled product is what produces `support_status = Not Entitled` — that
combination is a recorded outcome, not an error to reject.

### Support status

`HD Ticket.support_status` → Select, also a fixture custom field:

| value | meaning |
|---|---|
| `Covered` | entitlement exists, `support_expiry` blank or in the future |
| `Expired` | entitlement exists, `support_expiry` in the past |
| `Not Entitled` | no `HD Customer Product` row for this customer + product |
| `Unknown` | no product on the ticket, or no customer linked |

**Stamped once, when the product first becomes known; never recomputed after
that.** Concretely:

- at ticket creation, if `hd_product` is already set (portal/email flows where the
  requester picks a product); otherwise
- the first time `hd_product` transitions from empty to set.

Stamping *only* at creation would be wrong here. WhatsApp tickets are created
automatically on the first inbound message, long before any agent tags a product,
so a creation-only rule would leave `support_status = Unknown` permanently on
precisely the channel this work started from. Once stamped, the value is frozen.

The freeze is deliberate: the field records what the status *was when the customer
asked*, which is the figure that matters for renewal conversations and for
measuring absorbed out-of-contract support. A live-computed value would silently
rewrite history the moment someone renews.

A Select field is used rather than Frappe's native tagging. `_user_tags` is a
free-text comma-blob — awkward to aggregate and trivially broken by a typo —
whereas a Select is queryable and constrained.

The **live badge** in the agent sidebar (`TicketContactTab.vue`, already modified
in this fork) computes from current data and answers a different question: is this
customer covered *right now*. Both exist; neither replaces the other.

### Bot knowledge-base scoping (`helpdesk/integrations/bot.py`)

One mechanism: the product tag filter. The existing global allowlist in
`Helpdesk Bot Settings.allowed_categories` is untouched and still applies exactly
as it does today — it remains the ceiling, and product scoping only ever narrows
within it.

A category→product mapping layer was designed and then deliberately dropped. The
five categories in use (`Customers`, `Internal Docs`, `Public Access`, `General`,
`Licenses`) are audience- and topic-shaped, so such a mapping would have shipped
empty and stayed empty — dead configuration plus a second failure mode. If Outline
collections are ever renamed after products, a mapping layer is a small,
self-contained addition at that point.

Filtering applies the eligibility rule above: an article tagged for other products
is dropped; an untagged article is generic and always survives.

It is a **hard filter, not a ranking boost.** Boosting tolerates mistagging but
can still hand a POS customer eTIMS instructions, which is the precise confusion
this feature exists to prevent. The untagged-is-generic escape hatch already
covers the mistagging risk, so the extra tolerance buys little.

Filtering must run **before** results are truncated to `top_k`, not after.
Afterwards, a semantic search could return three articles, drop two on product and
answer from one — silently degrading answers in a way that looks like a weak
knowledge base rather than a filtering artefact. `search_articles` already
overfetches `top_k * 4` for exactly this reason; the LIKE fallback needs its
`LIMIT` widened to match.

Product resolution order:

1. the ticket's `hd_product`
2. else, if the customer holds **exactly one** entitlement, that product
3. else, unscoped

Rule 2 earns its place: a customer who owns only eTIMS gets correctly scoped
answers before any agent touches the ticket.

Rollout is inert by construction. Until articles are tagged, every article is
generic, so the filter removes nothing and the bot answers exactly as it does
today. Accuracy improves in proportion to tagging.

`_combined_kb_search()` gains an optional product argument, and the filter applies
at three points — each already filters by category and so has the article rows to
hand: `embeddings.py:251` (semantic search, which re-fetches article data at query
time), `_search_kb`'s LIKE fallback, and `_filter_outline_by_category`.


### Outline-synced articles

`docs.cecypo.tech` syncs into `HD Article` hourly via `sync_outline_docs()`, and
Outline carries no product information. Two facts make this safe.

**Product tags survive the sync.** The update path is
`frappe.db.set_value("HD Article", existing, {...})` with an explicit field dict —
`title`, `content`, `category`, `source_url`, `internal`, `status`
(`integrations/outline.py:168-186`). It never loads or saves the document, so it
cannot touch child tables. Tags applied in Helpdesk persist across every re-sync,
and archiving is likewise a single `status` write, so tags survive a document
disappearing from Outline and returning.

**Helpdesk owns routing metadata; Outline owns content.** That split is the
design, not a workaround. Tagging happens once, in Helpdesk, and is never
overwritten.

Current state (2026-08-17): 189 synced articles across five categories —
`Customers`, `Internal Docs`, `Public Access`, `General`, `Licenses`. These are
audience- and topic-shaped rather than product-shaped, so **no name link exists
today** and scoping will come from tags. Decision taken: tag in Helpdesk, leave
Outline's structure alone. The name-matching rule above then costs nothing now
and starts working automatically if a product-named collection is ever created.

Note that `Public Access` / `Internal Docs` / `Customers` encode *who may read*,
an axis the sync already handles separately through the `internal` flag. Product
scoping is orthogonal to it and must not be conflated with it.

### Knowledge-base gap tracking

`HD Bot Missing KB Query` already records `suggested_category` when the bot
cannot answer. Add `product`, populated from the same resolution used for
scoping. This turns "the bot could not answer this" into "we have no eTIMS
article about X", which is directly actionable for whoever writes articles — and
once articles carry product tags, article-count-per-product becomes an ordinary
report that shows where the bot is weakest before customers discover it.

### Upstream `HD Ticket.product`

Left untouched. It is dead, but it is upstream's field; repurposing it would
conflict on every upstream merge for no benefit.

## Testing

- Catalogue CRUD; `disabled` hides a product from selection without breaking
  existing tickets that reference it.
- Entitlement uniqueness: a duplicate `(customer, product)` insert is rejected by
  the index, including under concurrent insert.
- `source` ownership: an `ERPNext`-sourced write does not overwrite a `Manual` row.
- Expiry boundaries: blank expiry, expiry today, expiry yesterday.
- `support_status` stamped correctly for all four states.
- `support_status` stamps on the empty→set transition of `hd_product`, covering the
  WhatsApp path where the ticket exists before any product is known.
- `support_status` is **not** recomputed when an entitlement is later renewed, nor
  when `hd_product` is changed from one product to another after the first stamp.
- An unentitled product is accepted by the server and yields `Not Entitled` rather
  than a validation error.
- Bot scoping: a tagged article is excluded for a non-matching product; an
  untagged article is returned for every product; filtering happens before
  `top_k` truncation, so a product filter cannot quietly shrink the result set.
- The global allowlist is unchanged — product scoping narrows within it and can
  never widen it.
- Single-entitlement inference resolves the product before an agent tags a ticket.
- With no article tagged anywhere, search results are identical to today.
- A product tag applied to an Outline-synced article survives `sync_outline_docs()`.
- A KB gap records the resolved product.
- Regression: `hd_product` queries survive an unmigrated site (`OperationalError`
  path).

## Out of scope

- Any call to ERPNext. E is entirely local; the transport is sub-project A and the
  mirror is B.
- Account standing / overdue invoices (sub-project C).
- The unknown-contact guard and verification workflow (sub-project D).
- Per-branch or per-site product instances. The catalogue is flat by decision; if
  multi-site deployments later matter, `HD Customer Product` is the natural place
  to hang them.
- Product versions, and therefore version-scoped articles. Support answers do not
  currently differ by version.
- Ranking or boosting by product. Filtering is hard.
- A category→product mapping layer. Designed, then dropped: the categories in
  use are audience- and topic-shaped, so it would have shipped empty. It is a
  small, self-contained addition later if Outline collections are ever renamed
  after products.
- Automatic routing on expiry. `default_team` is a hint agents can act on; nothing
  routes automatically, because an outage at an out-of-contract customer must not
  be diverted to a sales desk.
