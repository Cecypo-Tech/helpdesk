# ERPNext connection and customer mirror (sub-projects A + B)

## Problem

Helpdesk has a product catalogue and an entitlement model (shipped 2026-08-17,
`2026-08-17-product-entitlement-design.md`) but **nothing populates it**.
`helpdesk/entitlement.py` only ever reads `HD Customer Product`; no code writes
it. Today the single row on `dev.localhost` — `Test Co → POS` — was typed by
hand, and that is the only mechanism that exists.

Consequences:

1. **Entitlements are hand-maintained**, so they drift the moment a customer
   renews or lapses, and the `support_status` stamped on a ticket is only as
   good as somebody's memory.
2. **Customers and contacts are duplicated by hand.** ERPNext knows the
   customer, their `tax_id` (KRA PIN) and their territory; Helpdesk re-types it.
3. **Account standing is invisible.** Overdue balances live in ERPNext and no
   agent can see them while answering a ticket.

Two Server Scripts already exist and are verified against real data
(`docs/erpnext-server-scripts/`): `helpdesk_customer_sync` and
`helpdesk_customer_standing`. They are the ERPNext half of the wire. **Nothing
in Helpdesk calls them.**

Helpdesk and ERPNext run on **separate benches**, so this is an HTTP problem,
not an app-dependency problem. Upstream's `helpdesk/integrations/erpnext/`
module is same-bench only (`ERPNext HD Settings.enabled = 0` here) and is not
used by any of this.

## Goal

Populate `HD Customer`, `Contact` and `HD Customer Product` from ERPNext on a
schedule, and show live account standing to agents — without Helpdesk ever
depending on ERPNext being installed, reachable, or correct.

### Standing invariant: WhatsApp support works with no ERPNext

Inherited from the entitlement spec and **binding here too**. Production
helpdesk has no `erpnext` app, and every call in this sub-project crosses a
network. `helpdesk/integrations/tests/test_no_erpnext.py` must keep passing.

Every ERPNext call **fails open**: an unreachable or erroring ERPNext leaves the
last mirrored state in place and support proceeds. Nothing here may block ticket
creation, a reply, or a bot answer. This is the same flag-never-block rule
settled for account standing.

## Design

### Architecture: thin Server Scripts, fat Helpdesk client

ERPNext exposes near-raw data. Helpdesk owns mapping, reconciliation and
upserts.

The reason is operational. Server Scripts are **hand-pasted into a production
textarea, with no tests, no version control and no review**. Every rule that
will need tuning — what confers coverage, when a renewal counts as unpaid,
which Item means which product — belongs in the app, where it is tested and
merges cleanly. Putting coverage policy in `safe_exec` would put the most
change-prone logic in the least safe place we own.

Rejected alternatives:

- **Fat scripts, thin client** — less data over the wire, but moves business
  rules into the untestable half.
- **Push via ERPNext webhooks** — near-real-time, at the cost of DocType Event
  scripts, cross-bench retry, and a public Helpdesk endpoint. Eventual
  consistency is sufficient here; the failure surface is not worth it.

**Pull, on a schedule, plus a manual "Sync now"** — the shape
`WA API Settings` + `enqueue_wa_sync` already uses, which the team knows.

### Connection

New singleton `ERPNext Sync Settings`:

| field | type | notes |
|---|---|---|
| `enabled` | Check | master switch; off = no calls at all |
| `server_url` | Data | ERPNext base URL |
| `api_key` | Data | |
| `api_secret` | Password | masked, and **never** exported in fixtures |
| `sync_interval_hours` | Int | default 6 |
| `last_customer_sync` | Datetime | drives incremental `modified_after` |
| `last_entitlement_sync` | Datetime | |

Auth is `Authorization: token <api_key>:<api_secret>`. A "Test connection"
button calls the customer endpoint with `limit=1`.

**Security note carried from the Server Script work:** inside `safe_exec`,
`frappe.get_all` is forced to `ignore_permissions=True` and `frappe.db.sql`
bypasses permissions entirely. The API key therefore **is** the access control.
It must belong to a dedicated ERPNext user, be rotated like a secret, and never
be shared with a human login. Rate limiting stays enabled on all three scripts.

### Identity and linking

`HD Customer.erpnext_customer` already exists as a **`Data`** field (hidden,
read-only, currently unused). Data — not Link — is exactly right across benches,
since the `Customer` doctype does not exist locally. It becomes the join key.

Match order for an incoming ERPNext customer, strict at every step:

1. existing `HD Customer.erpnext_customer` == ERPNext customer name
2. `tax_id` exact match
3. `customer_name` exact match

Never fuzzy. No match creates a new `HD Customer` with `erpnext_customer` set.

Contacts get the same treatment: a new `Contact.erpnext_contact` Data custom
field marks ERPNext-owned rows. Match order for an incoming ERPNext contact,
also strict:

1. existing `Contact.erpnext_contact` == ERPNext contact name
2. exact `email_id` match **against a contact already linked to the same
   customer** — never a global email match, which would hijack an unrelated
   contact who happens to share an address
3. no match creates a new Contact, linked to the customer via `Dynamic Link`

Phone is deliberately **not** a match key. WhatsApp already creates contacts by
phone (`match_phone_to_contact`), and letting two systems claim the same row on
the same key is how silent merges happen.

**A contact with no `erpnext_contact` value is Helpdesk-owned and is never
touched by a sync** — that is what protects the
sales-staff contacts deliberately kept out of ERPNext, and the contacts created
automatically from WhatsApp.

New fields on `HD Customer`: `tax_id` (Data), `territory` (Data),
`customer_group` (Data). Data rather than Link: these mirror ERPNext values and
must not require local doctypes.

All new fields on existing doctypes are **Custom Fields via fixtures**, never
edits to upstream JSON — the rule inherited from the entitlement work.

### What is mirrored, and what is not

| Source | Destination | Frequency |
|---|---|---|
| Customer + `tax_id`, `territory`, `customer_group` | `HD Customer` | scheduled, incremental |
| Contacts (name, email, phone, mobile) | `Contact` + `Dynamic Link` | scheduled |
| Recurring Sales Orders → items + expiry | `HD Customer Product` | scheduled |
| Overdue / outstanding totals | **not stored** | live, per request |

**Standing is deliberately not mirrored.** It changes daily, it is advisory, and
a stale copy is worse than a live fetch that degrades visibly. It is read
through `helpdesk_customer_standing` on demand, cached briefly (see Failure).

### Entitlement derivation

This is the part that stops entitlements being hand-typed, and the part with the
most business nuance.

**How renewals actually work here** (confirmed with the operator, 2026-08-18):
recurring support is sold as a **Sales Order with an `Auto Repeat`**, not via
ERPNext's `Subscription` doctype — there are zero Subscription records. The Auto
Repeat re-issues the Sales Order roughly a month before expiry as a renewal
reminder. Billing is **not** automated: the customer confirms manually, and the
reminder email states *"Payment is required before expiry to avoid interruption
of service."* `Sales Order.delivery_date` is the renewal/expiry date — the
operator's own email template labels it "Renewal / expiry".

The join is direct: **`Sales Order.auto_repeat` is a Link field**, so a recurring
SO is identifiable without a reverse lookup.

A third Server Script, `helpdesk_customer_entitlements`, returns for each
customer every submitted Sales Order with `auto_repeat` set, carrying
`delivery_date`, `status`, and its item codes.

Helpdesk then derives, per `(customer, product)`:

```
support_expiry = MAX(delivery_date) over that product's mapped SOs
renewal_unpaid = winning SO status in (To Bill, To Deliver and Bill, To Pay)
                 -- PROVISIONAL: confirm against real renewal SOs in the probe
source         = "ERPNext"
```

**Why `MAX(delivery_date)` needs the unpaid flag.** The Auto Repeat has
`submit_on_creation: 1`, so the renewal SO is created *and submitted*
automatically a month before expiry, with a `delivery_date` a year out. Taking
the latest date alone would mean **the reminder itself silently grants another
year of coverage**, whether or not the customer ever renews or pays — marking
lapsed customers `Covered`, which is backwards from the renewal conversations
this feature exists to support.

Rather than resolve that by joining through to payments (more query surface, and
a rule that would live in a Server Script), coverage stays on the latest SO and
the ambiguity is made **visible**: the agent sees `Covered · renewal unpaid`.
The badge tells the truth instead of hiding it.

New fields on `HD Customer Product`: `renewal_unpaid` (Check) and
`source_document` (Data — the SO name), so an agent asking *why does it say
that* has an answer.

### Item mapping

ERPNext Item codes (`F052`, `F063`) will never equal HD Product names (`eTIMS`,
`POS`). `HD Product` gains an `erpnext_items[]` child table listing the Item
codes that mean this product — many-to-one, so "eTIMS Annual" and "eTIMS
Monthly" both resolve to `eTIMS`.

**Unmapped items are ignored, never auto-created.** Auto-creating an HD Product
per subscribed Item would fill a deliberately flat 5–15 row catalogue with
billing SKUs and variants. The sync logs a count of unmapped item codes so gaps
are discoverable rather than silent.

Note this also means the POS product can never be sourced from ERPNext — the
point-of-sale system is not a Frappe application. POS entitlements stay
`source = Manual` or arrive from POS licensing in a later sub-project. That is
precisely why `source` exists.

### Ownership rules

The rule the entitlement model was built around, now load-bearing:

> A sync may **create** rows, and may **update** rows whose `source` matches its
> own. It must **never** overwrite a row whose `source` is `Manual`.

Same shape for contacts, keyed on `erpnext_contact`. A human decision outranks a
mirror, always.

The composite unique index on `HD Customer Product (customer, product)` is what
makes the upsert idempotent and concurrency-safe. It is created by a patch and,
since `install_app` marks patches as run without executing them, also invoked
from `after_install`.

### Failure behaviour and degradation

- **Sync unreachable** → last mirrored state stands; the job logs and exits.
  No user-visible failure.
- **Standing lookup unreachable** → the badge shows "standing unavailable", not
  an error, and never blocks the ticket view.
- **Circuit breaker** on the live standing call: after **3 consecutive
  failures**, stop calling for **5 minutes** rather than adding a timeout's
  latency to every ticket open. Counter and cool-off live in `frappe.cache()`,
  the same mechanism the WA sync cooldown uses.
- **Partial data** → a customer that fails to map is skipped and counted, not
  fatal to the run.
- Every entry point wraps its ERPNext call; nothing propagates.

### Scheduling

A scheduler job every `sync_interval_hours`, plus a manual "Sync now" in
settings that enqueues the same job — mirroring `enqueue_wa_sync`. Incremental
by `modified_after` for customers (the endpoint already supports it, ordered
`modified asc` for safe paging); entitlements re-derive in full, since they are
small and derived rather than accumulated.

### Implementation phasing

This is one coherent subsystem but a large one, and it should not land as a
single change. The natural order, each independently useful:

1. **Connection** — settings doctype, HTTP client, test-connection button.
   Nothing writes yet.
2. **Customer mirror** — customers, `tax_id`, `territory`. Verifiable on its own.
3. **Live standing** — read-only, cached, circuit-broken; the agent badge gains
   an overdue signal.
4. **Contacts** — the ownership rules get their first real exercise.
5. **Entitlements** — the third Server Script plus item mapping. Last, because
   it is the only piece blocked on a production probe.

Steps 1-4 are unblocked today. Step 5 is not.

## Testing

- Mapping rules with a mocked transport: customer match order (link → tax_id →
  name), never fuzzy.
- Ownership: a `Manual` `HD Customer Product` row survives a sync that would
  otherwise update it; a Helpdesk-owned `Contact` (no `erpnext_contact`) is
  untouched.
- Entitlement derivation: `MAX(delivery_date)` picks the latest SO;
  `renewal_unpaid` set for each unsettled status and clear for `Completed`;
  unmapped item codes are ignored and counted.
- Idempotency: running the sync twice produces no changes on the second run.
- Fail-open: an unreachable ERPNext leaves existing rows intact and raises
  nothing; a standing timeout degrades the badge only.
- `test_no_erpnext.py` still passes — the app works with no ERPNext at all.

## Verified vs assumed

**Verified against real data (2026-08-17):** `helpdesk_customer_sync` returns
customers with populated `tax_id` and `territory` plus their contacts;
`helpdesk_customer_standing` returns correct overdue totals, counts, oldest due
date and day count, and resolves a customer by `tax_id`. Both reject
unauthenticated callers (403) and are rate limited.

**NOT verified — production only:** the entitlement payload. `dev.localhost` has
**zero `Auto Repeat` records** and zero `Subscription` records, so the Sales
Order + Auto Repeat chain has never been observed populated. `custom_reference`,
referenced in the operator's renewal email template, does not exist on the dev
instance either.

**A probe against production must run before `helpdesk_customer_entitlements`
is written.** The design above is sound on schema, but the shape and volume of
real recurring Sales Orders is unconfirmed, and the `status` values actually
observed on renewal SOs will decide the exact `renewal_unpaid` predicate.

## Out of scope

- The unknown-contact guard and self-registration form (sub-project D).
- POS licensing as an entitlement source — a separate system, not Frappe.
- Writing anything back to ERPNext. This sync is strictly one-way, read-only.
- Real-time push. Eventual consistency via scheduled pull is sufficient.
- Mirroring standing. It is fetched live by decision, not stored.
- Auto-creating `HD Product` rows from Item codes.
