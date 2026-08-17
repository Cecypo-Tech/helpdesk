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
| `article_categories` | Table → HD Product Article Category | KB categories this product covers |

**`HD Product Article Category`** — child table, single `category` Link →
`HD Article Category`. Structurally identical to the existing
`HD Bot Allowed Category` (`istable: 1`, one Link field), which is the pattern to
copy.

**`HD Customer Product`** — one row per entitlement. A standalone doctype, not a
child table on `HD Customer`.

| field | type | notes |
|---|---|---|
| `customer` | Link → HD Customer | |
| `product` | Link → HD Product | |
| `version` | Data | e.g. "eTIMS v2", "POS Pro" |
| `support_expiry` | Date | **blank = no expiry = permanently covered** |
| `source` | Select | `Manual` / `ERPNext` / `POS` — default `Manual` |
| `notes` | Small Text | |

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

The global allowlist remains the ceiling — a product can never widen the bot's
reach beyond what `Helpdesk Bot Settings.allowed_categories` permits.

```
categories = global_allowlist ∩ categories_of(resolved_product)
if not categories:
    categories = global_allowlist        # fallback — never search an empty set
```

Product resolution order:

1. the ticket's `hd_product`
2. else, if the customer holds **exactly one** entitlement, that product
3. else, unscoped

Rule 2 earns its place: a customer who owns only eTIMS gets correctly scoped
answers before any agent touches the ticket.

The empty-intersection fallback is not optional. Without it the bot goes silent for
precisely the customers whose data is incomplete — a worse failure than being
slightly off-topic, and one that would present as "the bot is broken".

`_combined_kb_search()` gains an optional product argument. `bot.py:114` is the
only site that resolves categories today, so the change stays contained.

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
- Bot scoping: correct intersection; empty-intersection falls back to the global
  allowlist; single-entitlement inference; global allowlist is never widened.
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
- Automatic routing on expiry. `default_team` is a hint agents can act on; nothing
  routes automatically, because an outage at an out-of-contract customer must not
  be diverted to a sales desk.
