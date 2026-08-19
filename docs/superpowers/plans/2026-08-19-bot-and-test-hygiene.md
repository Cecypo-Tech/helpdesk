# Bot identity hand-off and WhatsApp test hygiene

**Goal:** Stop the bot inventing and linking customers, close two security/side-effect
defects in it, and repair the two red WhatsApp test modules.

**Spec context:** `docs/superpowers/specs/2026-08-19-unknown-contact-verification-design.md`
— identity is a claim an agent approves, never something automation decides.

## Part A — Test hygiene (test-only changes)

Neither module is failing because of product code. Two distinct causes, both confirmed.

### A1. Purge the leaked rows on dev

Verified present: 3 stale `WA Message` rows on `222unreadtest@s.whatsapp.net`, one leaked
`Jane %` Contact, a stale `WA Line` named `wa-unread-test-line`. These alone account for
`6 != 3`, `4 != 2`, and `'Jane 6a4821' is not None`.

### A2. `test_wa_unread_state` — stop reusing fixed JIDs

The module hardcodes `111unreadtest@…` / `222unreadtest@…`, so any run that dies before
cleanup leaves rows that the next run counts. `test_wa_contact_link` already solved this
for itself — it derives identifiers per run via `frappe.generate_hash` and documents why.
Adopt the same approach, and additionally purge that run's JIDs in `setUp` so a dirty site
cannot poison a fresh run.

### A3. `test_wa_contact_link` — the architecture moved, the tests did not

Three failures (`reference_name` is `None`) are all one cause: the tests insert a
`WhatsApp Message` and read `reference_name` immediately, but `on_whatsapp_message_insert`
now only *enqueues* `wa_ingest.process_incoming_message` with `enqueue_after_commit=True`.
The linking that these assertions expect happens in a job, after the assertion has run —
or in a real worker, after cleanup, which is what leaks the Contacts.

`helpdesk/integrations/tests/test_no_erpnext.py` already establishes the correct pattern:

```python
with patch("frappe.enqueue"):
    doc = self._insert(...)
wa_ingest.process_incoming_message(doc.name)
```

Apply it to the three affected tests. This is not weakening them — it makes them test the
ingestion path deterministically instead of racing a worker.

**Verification:** run both modules twice back to back. Passing once proves the fix;
passing twice proves nothing accumulates.

## Part B — The bot stops handling identity

**Decision (user, 2026-08-19): route it through verification.** The bot no longer asks for
a company name, no longer creates `HD Customer`, and no longer writes `HD Ticket.customer`.

### Why

`bot.py:571-580` currently does:

```python
existing = frappe.db.get_value("HD Customer", {"customer_name": company_name}, "name")
if existing:
    cust_name = existing
else:
    cust = frappe.get_doc({"doctype": "HD Customer", ...}).insert(...)
frappe.db.set_value("HD Ticket", ticket_name, "customer", cust_name)
```

That links an unverified party to a customer record on a typed name alone — weaker than the
PIN flow, and reachable by anyone. With 2,834 mirrored ERPNext customers, typing a real
customer's name links a stranger to their record, from which the coverage panel and account
standing then resolve. On the miss path it fills the customer list with LLM-extracted
strings. It also means an unknown contact is asked two different identity questions, one
from the bot and one from verification.

### Changes

- Remove the company-name prompt, the retry branch, the cache keys
  (`wa_bot_company:*`, `wa_bot_company_retry:*`), the `HD Customer` creation and the
  `HD Ticket.customer` write from `process_message`.
- Remove `_extract_company_name` and its LLM call — it exists only for this flow.
- A ticket with no customer now proceeds straight to normal bot handling. The bot answers
  the question; verification handles identity separately.

### Tests

- the bot never creates an `HD Customer`, whatever the customer types
- the bot never sets `HD Ticket.customer`
- a ticket with no customer still receives a normal bot reply (the behaviour the removed
  branch used to short-circuit)

## Part C — Two defects in the bot

### C1. `suggest_agent_reply` has no permission guard

`bot.py:729` is `@frappe.whitelist()` with nothing else. Any authenticated user — including
a portal Customer — can pass any ticket name and get an LLM-drafted reply built from that
ticket's conversation history. Two harms: disclosure of another customer's WhatsApp thread,
and an unmetered billable LLM call per request.

Add `@agent_only` beneath `@frappe.whitelist()`, matching `helpdesk/api/entitlement.py`,
plus a test that a non-agent is refused.

### C2. Bot replies claim and move the ticket

`_BotState.send_reply` → `send_wa_reply(ticket=...)` → `_send_fw_reply(system=False)`, so
every bot reply assigns the ticket to whatever user the job runs as and moves it into
`agent_reply_status`.

Pass `system=True`. **Stated assumption:** a bot reply is not an agent reply, so the ticket
should stay in the agents' queue until a human touches it. This is a deliberate behaviour
change and is trivially reversible if the queue is meant to reflect bot activity.

The assignment half is unambiguous — assigning tickets to a background job's session user
is wrong in every reading.

## Not fixed here, deliberately

Recorded so they are not lost, but out of scope for this pass:

- **Prompt injection via prior conversations.** `_get_prior_customer_context` injects earlier
  customer messages into the system prompt, so a customer can plant instructions that are
  replayed later as trusted context.
- **The `OVERRIDE — FOLLOW-UP RULE` block** tells the model in plain words that it "overrides
  the KB-only restriction" — the guardrail the rest of the prompt establishes, disabled by a
  rule a user triggers by asking to reformat.

Both are prompt-behaviour changes with no test coverage to protect them, so changing them
blind risks regressing answer quality. They deserve their own pass with evals.

## Verification

```
bench --site dev.localhost run-tests --module helpdesk.tests.test_wa_contact_link
bench --site dev.localhost run-tests --module helpdesk.tests.test_wa_unread_state
bench --site dev.localhost run-tests --module helpdesk.tests.test_bot
bench --site dev.localhost run-tests --module helpdesk.tests.test_bot_product_scoping
bench --site dev.localhost run-tests --module helpdesk.tests.test_contact_verification
bench --site dev.localhost run-tests --module helpdesk.integrations.tests.test_wa_ingest
```

The two repaired modules must be run twice consecutively.
