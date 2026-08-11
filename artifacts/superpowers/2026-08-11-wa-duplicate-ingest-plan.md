# WABA Duplicate Ingestion — Implementation Plan

**Goal:** Stop the same inbound WhatsApp message being stored twice, and clean up the
rows already duplicated.

**Architecture:** A `before_insert` hook on `WhatsApp Message` marks a row whose
`message_id` is already stored; `on_whatsapp_message_insert` drops the marked row before
any ticket, contact or bot work happens. An index on `message_id` makes the lookup keyed.
A separate reporting/cleanup module handles the historical rows.

**Tech Stack:** Frappe v16, MariaDB, Python 3.14.

## Background

Meta fans one inbound event out to **every app subscribed to the WABA**, and retries any
delivery that does not return 200 promptly. `frappe_whatsapp` checks neither case:
`message_id` carries no unique constraint and no index
(`whatsapp_message.json`), and `post()` inserts unconditionally
(`frappe_whatsapp/utils/webhook.py`). Observed on production 2026-08-11: five inbound
messages, each stored twice, pairs 0.15–2.5 s apart, identical `wamid`.

Removing the second subscriber fixes today's cause. It does not make ingestion
idempotent — a single slow request still earns a retry and a duplicate.

## Global Constraints

- Never modify `apps/frappe_whatsapp` — it is a clean checkout of
  `shridarpatil/frappe_whatsapp` with no fork remote. All changes live in `helpdesk`.
- Never raise from the ingestion path. Raising returns 500 to Meta, which buys a retry of
  the very delivery being rejected and, repeated, gets the app's webhook backed off.
- The guard applies to `type == "Incoming"` only. Outgoing rows receive `message_id` from
  Meta's send response and must not be touched.
- No unique constraint on `message_id`. Precedent: `drop_wa_message_id_unique_index`
  removed one from `WA Message` because forwarded messages legitimately share an ID.

## File Structure

- `helpdesk/integrations/wa.py` — the guard: duplicate detection, the flag, the drop.
- `helpdesk/hooks.py` — `before_insert` becomes a list so the guard runs first.
- `helpdesk/patches/add_whatsapp_message_id_index.py` — Property Setter + index.
- `helpdesk/patches.txt` — register it.
- `helpdesk/fixtures/whatsapp_message_id_index.json` — carry the Property Setter to
  sites where patches are marked pre-run (fresh installs).
- `helpdesk/integrations/wa_duplicate_cleanup.py` — report and merge for historical rows.
- `helpdesk/integrations/tests/test_wa_duplicate_ingest.py` — guard tests.
- `helpdesk/integrations/tests/test_wa_duplicate_cleanup.py` — cleanup tests.

---

### Task 1: Duplicate guard

**Files:** `helpdesk/integrations/wa.py`, `helpdesk/hooks.py`,
`helpdesk/integrations/tests/test_wa_duplicate_ingest.py`

**Interfaces produced:**
- `flag_duplicate_whatsapp_message(doc, method=None) -> None`
- `_wa_message_id_seen(message_id: str) -> bool`
- `_WA_DUPLICATE_FLAG: str`

- [ ] Write failing tests: same `wamid` inserted twice leaves one row; distinct `wamid`s
      both survive; blank `message_id` never dedupes; an `Outgoing` row sharing an
      `Incoming` row's `message_id` survives; the dropped row creates no second ticket.
- [ ] Run them, confirm they fail.
- [ ] Implement `_wa_message_id_seen` using `SELECT ... FOR UPDATE`. A plain `exists()`
      runs under REPEATABLE READ and cannot see a row a concurrent delivery has inserted
      but not committed — exactly the fan-out case. A locking read is a current read and
      takes a key lock, so the second request blocks until the first commits, then sees it.
- [ ] Implement `flag_duplicate_whatsapp_message`, setting `doc.flags[_WA_DUPLICATE_FLAG]`.
- [ ] Drop the row at the top of `on_whatsapp_message_insert` via `frappe.db.delete`, and
      return. Raw delete because the row has no children and no links, and logically never
      existed. Returning before `reference_doctype` is set also makes
      `bot.handle_whatsapp_message` a no-op via its own guard — no double bot reply.
- [ ] Make `hooks.py` `before_insert` a list, guard first, then
      `set_wa_message_normalized_phone`.
- [ ] Run the tests, confirm they pass.

### Task 2: Index on `message_id`

**Files:** `helpdesk/patches/add_whatsapp_message_id_index.py`, `helpdesk/patches.txt`,
`helpdesk/fixtures/whatsapp_message_id_index.json`, `helpdesk/hooks.py`

- [ ] Patch creates a `Property Setter` (`search_index = 1` on
      `WhatsApp Message.message_id`) and the index itself, both guarded by existence
      checks. The Property Setter is what makes it durable: a hand-rolled index on another
      app's doctype gets dropped by schema sync, whereas `search_index` makes frappe
      re-create it whenever the table is rebuilt.
- [ ] Ship the same Property Setter as a fixture — fresh installs mark patches as
      already-run without executing them.
- [ ] Verify with `SHOW INDEX FROM \`tabWhatsApp Message\``.

### Task 3: Historical cleanup

**Files:** `helpdesk/integrations/wa_duplicate_cleanup.py`,
`helpdesk/integrations/tests/test_wa_duplicate_cleanup.py`

**Interfaces produced:**
- `report_duplicate_messages(verbose=0) -> dict`
- `merge_duplicate_messages(dry_run=1, limit=0) -> dict`

- [ ] Tests first: report counts groups without writing; dry run writes nothing; the
      earliest row per `message_id` is the survivor; rows carrying a ticket link are never
      dropped in favour of one without.
- [ ] Report lists `message_id` groups with counts and the tickets they touched.
- [ ] Merge keeps the earliest row per `message_id`, preferring a row that carries
      `reference_name`, and deletes the rest. `dry_run=1` by default;
      `frappe.only_for("System Manager")`; `methods=["POST"]` on the mutating call.
- [ ] Run the tests, confirm they pass.

### Task 4: Verify and ship

- [ ] `bench --site dev.localhost run-tests --app helpdesk`
- [ ] `bench build --app helpdesk`
- [ ] `bench --site dev.localhost migrate` to prove the patch runs and is idempotent.
- [ ] Commit on `fix/wa-duplicate-ingest`, merge to `develop`, push.

## Verification

- Suite green.
- Migrate twice; the second run is a no-op and the index survives.
- On production after deploy: re-run the duplicate report and confirm no new groups
  appear with a `creation` after the deploy.

## Known limits

- The guard is per-site. It cannot stop dev2 from also storing the message; only
  unsubscribing app 888 from the WABA does that.
- `SELECT ... FOR UPDATE` takes a key lock on the ingestion path. With the index this is a
  single-key lock; without it, it would be a table scan holding locks, which is why Task 2
  is not optional.
