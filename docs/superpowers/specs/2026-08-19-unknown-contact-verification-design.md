# Unknown-contact verification and customer linkage (sub-project D)

WABA only. WA Line, email and portal are explicitly out of scope for this pass.

## Problem

A WhatsApp Business message arrives from a number nobody has seen before.
Helpdesk creates a Contact and a ticket, but the Contact has no link to an
`HD Customer`, so:

- `get_customer()` returns nothing, `HD Ticket.customer` stays empty
- no entitlement resolves, so `hd_product` and `support_status` stay Unknown
- account standing cannot be looked up
- the bot cannot scope KB articles to the product the customer actually runs

Today the only fix is an agent recognising the number and linking the Contact by
hand. That does not scale past a handful of new numbers a week, and it is
invisible work — nothing surfaces that the linkage is missing.

## Goal

Let an unknown WABA sender identify themselves, and let one agent click turn
that claim into a permanent link on the Contact. Once linked, everything
downstream — product, entitlement, standing, bot scoping — resolves for every
future ticket from that number, with no further prompting.

Explicitly **not** a gate. This never refuses, delays, or degrades support. An
unverified contact receives exactly the service they receive today.

## Decisions

### 1. A KRA PIN is a claim, not a credential

This is the decision the rest of the design hangs off, so it is worth stating
why rather than asserting it:

- The PIN is printed on every invoice and every ETR receipt. It is not secret,
  and anyone who has ever been handed a receipt can quote one.
- 30 customers in the production ERPNext share a `tax_id` with another customer
  (found during the sub-project B mirror). It is not even unique at source.
- A WhatsApp number can be a shared office handset. Linking a number to a
  customer is a claim about a device, not about a person.

Therefore a matching PIN **never** links anyone automatically, not even on a
single exact match. It routes to an agent, who approves. The cost is one click
per new number, once, forever. The thing it buys is that a stranger cannot read
another company's support history and account standing by quoting a number off
a receipt.

### 2. Match by normalised comparison, do not validate

The obvious implementation is a KRA PIN regex. Real data says no. Of the 2,803
mirrored `tax_id` values:

| Shape | Count | |
|---|---|---|
| `A999999999A` | 2,794 | canonical |
| `A9999999999` | 5 | trailing digit where a letter belongs |
| `A99999999A` | 3 | one digit short |
| `A` | 1 | literally just `"P"` |

A strict `^[A-Z]\d{9}[A-Z]$` would refuse to match nine real customers, and
those customers would be told their own PIN is invalid.

The nine, for correction at the ERPNext end:

| `tax_id` | Customer | Fault |
|---|---|---|
| `P05122111X` | BLUE LAKE LTD | one digit short |
| `P05110212C` | SAHAJANAND ENTERPRISES LTD. | one digit short |
| `A00268781E` | SHIVLING SUPERMARKET | one digit short |
| `P0511478994` | MOIL KENYA LIMITED | ends in a digit, not a letter |
| `A0011327804` | MULCHAND RANMAL SHAH | ends in a digit, not a letter |
| `P0153879270` | VANDAN GENERAL STORES | ends in a digit, not a letter |
| `P0511194661` | VISTA WINDOWS LIMITED | ends in a digit, not a letter |
| `P0512110582` | YAKO SUPERMARKET (K) LTD | ends in a digit, not a letter |
| `P` | TEITA ESTATE LTD. | truncated to a single character |

Permissive matching does not rescue these. If BLUE LAKE quote their PIN
correctly it still will not match the truncated value we hold, because the
stored record is wrong. They fall into the "no match" path and an agent links
them by hand — which is what that path exists for. Fixing them in ERPNext is
the actual remedy.

So:

- **Extraction** from the message body is permissive: `[A-Za-z]\d{7,10}[A-Za-z]?`
- **Matching** normalises both sides (uppercase, strip everything non-alphanumeric)
  and compares against stored `tax_id`
- **Nothing is validated.** A claim that matches nothing is shown to the agent as
  "no match", which is information, not an error.

### 3. Verification state lives on the Contact, not a new DocType

A claim is about a number, not about a ticket — once verified it must apply to
every future conversation. `Contact` is exactly that grain, and the fork already
adds Custom Fields to Contact (`erpnext_contact`, `phone_suffix`) via fixtures.
A separate DocType would add a join and a lifecycle for no gain.

### 4. Off by default

Every new setting defaults to off. Deploying this changes nothing observable
until somebody ticks a box.

## Design

### Data model

Three Custom Fields on `Contact`, added via the consolidated `custom_field`
fixture entry in `hooks.py` (one entry, all specs — see the fixture-clobbering
note in CLAUDE.md):

| Field | Type | Purpose |
|---|---|---|
| `hd_verification_status` | Select | `Unverified` (default) / `Asked` / `Claimed` / `Verified` / `Rejected` |
| `hd_claimed_company` | Data | what they typed, verbatim, never trusted |
| `hd_claimed_tax_id` | Data | what they typed, verbatim, never trusted |
| `hd_verification_asked_on` | Datetime | rate-limits re-asking |

`Verified` means an agent approved and the Dynamic Link exists. It is deliberately
redundant with the link's existence, because `Rejected` and `Asked` are states the
link alone cannot express.

Three settings on `WhatsApp Helpdesk Settings`:

| Field | Type | Default |
|---|---|---|
| `verification_enabled` | Check | `0` |
| `verification_prompt` | Small Text | see copy below |
| `verification_reask_days` | Int | `7` |

Default prompt copy — deliberately free of any account detail, because it is sent
to a number we have not identified:

> Hi! Please reply with your company KRA PIN so we can link this WhatsApp number
> to your account. We only need this once.

The company name is deliberately not requested. `match_claim` compares `tax_id`
only, and the company's identity comes from the mirrored ERPNext record once the
PIN resolves — so asking for a name gave the customer a second thing to get
wrong for no matching benefit. `hd_claimed_company` still captures whatever
context they typed around the PIN, and is shown to the agent when non-empty.

### Flow

```
inbound WABA message
  └─ existing ingestion: Contact + HD Ticket created   (unchanged)
       └─ new: after_insert on WhatsApp Message
            ├─ verification disabled?            → return
            ├─ contact already has HD Customer?  → return
            ├─ status Verified or Rejected?      → return
            ├─ status Unverified                 → send prompt, status=Asked, stamp asked_on
            ├─ status Asked, asked_on older than reask_days → send prompt again, restamp
            └─ status Asked                      → try to parse a claim from this message
                 ├─ PIN-shaped token found → store claim, status=Claimed
                 └─ nothing found          → leave as-is, do NOT re-prompt
```

The prompt is a free-form WABA message. That is safe without a template: their
inbound message just opened the 24-hour window, so `_fw_reply_window_open()` is
true by construction at the moment we reply.

Re-prompting is capped at once per `verification_reask_days`. Somebody who
ignores the question gets asked again next week, not on every message.

Parsing only runs while status is `Asked`. A PIN appearing in an unrelated
message from an already-`Verified` contact is ignored — a verified link is never
overwritten by a later claim.

### Matching

`match_claim(tax_id) -> list[str]` normalises the claim and compares against
`HD Customer.tax_id`, returning matching customer names.

Three outcomes the agent sees:

- **one match** — the common case; today `tax_id` is 1:1 across mirrored HD
  Customers (verified: 0 duplicates), because the 30 ERPNext duplicates folded
  into namesakes during the mirror
- **no match** — they are genuinely new, or mistyped, or gave a supplier's PIN
- **several matches** — cannot happen on current data, but is specced and built
  because the mirror's name-folding is what makes it 1:1, and that is a property
  of the current data rather than a guarantee

### Agent approval

Backend, in `helpdesk/api/verification.py`, both `@agent_only` — the same guard
`get_ticket_entitlement` needed, because these read and write customer linkage:

```python
get_contact_claim(ticket)   -> {status, claimed_company, claimed_tax_id, matches: [...]}
approve_contact_link(contact, customer) -> {ok, customer}
reject_contact_claim(contact)           -> {ok}
```

`approve_contact_link` appends the Dynamic Link row on the Contact
(`link_doctype="HD Customer"`, `parentfield="links"`) — the exact shape
`helpdesk/utils.py:91` queries — and sets status to `Verified`. From the next
save onward `HD Ticket.customer` populates through the existing path at
`hd_ticket.py:309`, and product, entitlement and standing follow with no
further work.

Frontend: extend the existing coverage panel in
`desk/src/components/ticket-agent/TicketContactTab.vue`. It already renders
products and standing for a known customer; for an unverified one it renders the
claim, the match list, and Approve / Reject. No new component.

There is also a manual fallback that needs no UI at all: an agent can add the
Dynamic Link in `/app/contact` and everything resolves identically. That path
works today and keeps working.

### Fail-open discipline

Consistent with the rest of the ERPNext work — this is advisory, so nothing it
does may break a ticket:

- prompt send fails → log, continue; the ticket is already created
- Custom Fields absent (site not migrated) → every read wrapped in `try/except`,
  the feature no-ops, exactly as `baileys_jid` queries are wrapped
- matching raises → agent sees "unavailable", the ticket is untouched
- `verification_enabled` off → the hook returns before doing anything

### Privacy

The prompt carries no account data. Nothing in this feature causes the bot or
any automated reply to quote standing, balances, or entitlements into a
WhatsApp thread — the shared-handset case makes that a real disclosure, and it
remains prohibited. Match results are shown to agents in the desk UI only.

## Testing

Backend, `helpdesk/tests/test_contact_verification.py`:

- extraction accepts the canonical PIN, the three malformed real shapes, and a
  PIN embedded in a sentence; rejects a bare phone number like `254712345678`
- matching is case- and punctuation-insensitive (`p051234567x` matches `P051234567X`)
- the prompt is sent once; a second message inside the reask window does not resend
- a message after the reask window does resend
- a claim is parsed and stored, and status moves `Asked` → `Claimed`
- zero / one / many match outcomes each return the right shape
- **a single exact match does not auto-link** — the load-bearing test for decision 1
- approve creates the Dynamic Link and `get_customer()` then resolves it
- a `Verified` contact is never re-asked and a later PIN never re-links it
- a `Rejected` contact is never re-asked
- with `verification_enabled = 0` nothing is sent, stored, or changed
- a failing prompt send leaves the ticket intact
- both API endpoints refuse a non-agent

## Verified vs assumed

**Verified against code or live data:**

- `get_customer()` resolves through a Dynamic Link on Contact with
  `link_doctype="HD Customer"`, `parentfield="links"` — `helpdesk/utils.py:91`
- `HD Ticket.customer` is set from `get_customer(self.contact)` —
  `hd_ticket.py:309`; the field is an editable Link, so the manual path works
- `HD Customer.tax_id` exists as a Custom Field and is populated by the mirror
- `unknown_contact_action` options are `Skip Ticket Creation` (default) /
  `Create Contact and Ticket` / `Create Ticket Only`
- `support_status` gates nothing — every reader is a writer or a display
- tax_id shapes and the 0-duplicate finding, from the 2,803 mirrored values
- the 24-hour window is open at prompt time, since the inbound message opens it

**Assumed, and what breaks if wrong:**

- **Customers will answer the question.** If they mostly ignore it, this
  degrades to the manual path — no worse than today, but the build was wasted.
  Worth measuring before extending it to other channels.
- **The company name is no longer requested** (changed 2026-08-19). It was never
  matched on, so asking for it only widened the surface for a customer to get the
  reply wrong. `hd_claimed_company` still records whatever surrounds the PIN and
  is displayed when non-empty, so an agent keeps any context volunteered.
- **`Create Contact and Ticket` is the intended prod setting.** On the default
  `Skip Ticket Creation`, an unknown number produces no ticket, and this feature
  never runs at all.

## Out of scope

- WA Line (Evolution API), email, and portal — WABA only for now
- auto-approval on a single match, even with a corroborating company name
- treating the PIN as a credential in any form
- customer-facing self-service linking
- resolving the 30 duplicate `tax_id` records in ERPNext — a data-quality issue
  for the ERPNext side, surfaced but not fixed here
