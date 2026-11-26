# Ticket Customer Auto-link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an agent sets the customer on a ticket, link the ticket's contact to that customer. The red border on an empty Customer field is a separate task.

**Architecture:** Pull the link-writing part of `approve_contact_link` out into a plain helper with no commit and no permission decorator. Call that helper from both the existing whitelisted method and a new `HD Ticket.on_update` hook. The frontend change is one conditional class in `TicketDetailsTab.vue`.

**Tech Stack:** Frappe v16 (Python), Vue 3 + frappe-ui + Tailwind semantic tokens.

**Spec:** `docs/superpowers/specs/2026-11-26-ticket-customer-autolink-design.md`

## Global Constraints
- Only link a contact that currently has **zero** HD Customer links. Never add a second customer link, and never replace one.
- Fill blanks only. Never overwrite another ticket's existing customer. This is the existing `_backfill_ticket_customer` behaviour.
- Do not commit inside a doc-event hook. The request commits.
- Use semantic Tailwind tokens (AGENTS.md), e.g. `outline-red-3` / `border-red-3`, not `border-red-500`.
- Work on branch `feat/ticket-customer-autolink` off `develop`.
- Tests run only on `test.localhost`.

## Review Focus
1. A ticket with no contact (an email ticket that hasn't been matched yet) must not error when its customer is set. Test: `test_no_contact_is_a_noop`.
2. A customer cleared to blank must not unlink anything. Test: `test_clearing_customer_does_nothing`.
3. A contact already linked to customer A, with a ticket set to customer B, must keep only A. Test: `test_existing_link_is_left_alone`.
4. A new ticket inserted with a customer already set should also link its contact, because `on_update` runs after insert too. Test: `test_insert_with_customer_links`.
5. The verification screen's Approve button must still work after the refactor. The existing `test_contact_verification.py` must stay green.

---

### Task 1: Link the contact when the ticket's customer is set

**Files:**
- Modify: `helpdesk/api/verification.py:72-121`. Extract the helper; `approve_contact_link` calls it.
- Create: `helpdesk/overrides/ticket_customer.py`
- Modify: `helpdesk/hooks.py:124-126`. `HD Ticket` gets `"on_update": "helpdesk.overrides.ticket_customer.link_contact_on_customer_set"`.
- Test: `helpdesk/tests/test_ticket_customer_autolink.py`

**Interfaces:**
- Produces: `helpdesk.api.verification.link_contact_to_customer(contact: str, customer: str) -> bool`. It appends the HD Customer link if it is missing (saving with `ignore_permissions=True`), then calls `_backfill_ticket_customer`. It returns True if it added a link. It does not commit and does not set `hd_verification_status`; `approve_contact_link` keeps those two steps.
- Produces: `helpdesk.overrides.ticket_customer.link_contact_on_customer_set(doc, method=None) -> None`. It acts only when `doc.customer` is set, `doc.contact` is set, `doc.has_value_changed("customer")` is true (true on insert too), and `helpdesk.utils.get_customer(doc.contact)` is empty.

- [ ] **Step 1: Write the failing tests.** Set up an HD Customer `_AL Cust A` and `_AL Cust B`, plus a Contact `_al-contact` with no links. Create tickets with `frappe.get_doc({"doctype": "HD Ticket", "subject": "t", "raised_by": ..., "contact": ...}).insert(ignore_permissions=True)`. Clean up in tearDown. Tests:
  - `test_setting_customer_links_contact`: ticket inserted blank, then `ticket.customer = A; ticket.save()`, then `get_customer(contact) == [A]`.
  - `test_next_ticket_fills_itself`: after the above, a new ticket for the same contact has `customer == A` after insert.
  - `test_existing_link_is_left_alone`: the contact is pre-linked to A, a ticket's customer is set to B, and `get_customer(contact) == [A]`.
  - `test_clearing_customer_does_nothing`: A is linked through a ticket, then `ticket.customer = ""; save()`, and the contact is still linked to A.
  - `test_no_contact_is_a_noop`: a ticket with `contact=None` and its customer set saves without error.
  - `test_insert_with_customer_links`: a ticket inserted with `customer=A` leaves the contact linked to A.
- [ ] **Step 2: Run the tests and confirm they fail.** `bench --site test.localhost run-tests --module helpdesk.tests.test_ticket_customer_autolink`. Expected: FAIL (the contact is not linked).
- [ ] **Step 3: Implement** the helper, the refactor of `approve_contact_link` and the hook, as described under Interfaces.
- [ ] **Step 4: Run the new module and the verification module.** Run `--module helpdesk.tests.test_ticket_customer_autolink`, then `--module helpdesk.tests.test_contact_verification`. Expected: both PASS. (First check that no other run-tests is active on test.localhost.)
- [ ] **Step 5: Commit** with the message `feat(ticket): link the contact when an agent sets the ticket's customer`.

### Task 2: Red border on an empty Customer field

**Files:**
- Modify: `desk/src/components/ticket-agent/TicketDetailsTab.vue:17-33`

- [ ] **Step 1:** Add a conditional class on the `<Link>`: when `field.fieldname === 'customer' && !field.value`, apply a red outline using a semantic token, so it is visible on the Link's trigger. Prefer a wrapper `rounded ring-1 ring-outline-red-3`, or an equivalent that actually renders. Pick whichever shows on screen, in light mode and in dark mode.
- [ ] **Step 2:** Run `cd ~/frappe-bench && bench build --app helpdesk`. Expected: build succeeds.
- [ ] **Step 3: Browser check on dev.** Open a ticket with no customer at `http://localhost:8000/helpdesk/tickets/<name>` using a `qa.*@example.com` agent user (`scripts/qa_user.py`). The Customer field shows a red border. Set a customer and the border disappears with no reload. Reload, and the contact's next ticket is pre-filled. Take screenshots, then delete the QA user.
- [ ] **Step 4: Commit** with the message `feat(ticket): flag a missing customer with a red border`.

### Task 3: (i) overdue dialog next to Customer

**Files:**
- Modify: `helpdesk/api/entitlement.py`. Add `get_ticket_customer_snapshot(ticket: str | int) -> dict`, `@frappe.whitelist() @agent_only`.
- Modify: `helpdesk/integrations/erpnext_standing.py`. Add `fetch_snapshot(erp_customer: str) -> dict`, which uses the same cache, breaker and never-raise rules as `fetch`, with cache key `erpnext:snapshot:<customer>`.
- Create: `desk/src/components/ticket-agent/CustomerSnapshotDialog.vue`
- Modify: `desk/src/components/ticket-agent/TicketDetailsTab.vue`. Add the (i) button beside the Customer field.
- Test: `helpdesk/tests/test_customer_snapshot.py`

**Interfaces:**
- `fetch_snapshot` calls `erpnext_remote.call("cecypo_powerpack.api.get_customer_snapshot", {"customer": erp_customer})` on erp.cecypo.com. It returns `{"status": "ok"|"unknown"|"unavailable", "snapshot": <powerpack dict or None>}`. The powerpack dict has `invoices` (name, posting_date, due_date, grand_total, paid, outstanding_amount, days_overdue, currency) plus totals. Read the totals' exact keys from `cecypo_powerpack/customer_snapshot.py:131-150`.
- The endpoint resolves the ERP customer the same way `get_ticket_standing` does: ticket.customer → `HD Customer.erpnext_customer`.

- [ ] **Step 0: Check access first.** Call the powerpack method with SUPPORT's ERPNext Sync credentials. If you get a PermissionError, stop and ask the user before changing any role on erp.cecypo.com.
- [ ] **Step 1: Write the failing tests**, patching `erpnext_remote.call`:
  - `test_no_erp_customer_is_unknown`
  - `test_ok_returns_snapshot`
  - `test_remote_failure_is_unavailable_not_raise`
  - `test_second_call_is_cached`, which asserts `call` was invoked once.
- [ ] **Step 2: Run** `--module helpdesk.tests.test_customer_snapshot`. Expected: FAIL.
- [ ] **Step 3: Implement** the backend. Then the frontend: an (i) icon beside Customer, using `text-ink-red-3` when any invoice has `days_overdue > 0`, `text-ink-gray-4` otherwise, and hidden when the status is not `ok`. Clicking it opens a frappe-ui `Dialog` with an invoice table (Invoice, Date, Due, Days overdue, Total, Paid, Outstanding) and the totals, following the layout of powerpack's dialog. Each invoice links to `https://erp.cecypo.com/app/sales-invoice/<name>` and opens in a new tab.
- [ ] **Step 4: Run** the tests, then `bench build --app helpdesk`. Browser check on dev with a customer that has overdue invoices (red icon, dialog matches ERPNext) and one with none (grey icon).
- [ ] **Step 5: Commit** with the message `feat(ticket): customer overdue dialog from ERPNext`.

### Task 4: Land it
- [ ] Code review (superpowers:requesting-code-review), then merge into `develop` and push to `origin/develop` (Cecypo-Tech/helpdesk). Then deploy to support.cecypo.tech the usual way and recheck the red border there.
