# Finish: WhatsApp read-receipt error spam + download_corpus filelock noise

Date: 2026-08-03
Branch: `fix/wa-read-receipt-and-corpus-lock` (forked from `develop`)
Plan: `2026-08-03-wa-read-receipt-and-corpus-lock-plan.md`

## What landed

### A. Own the WABA read-receipt call — `helpdesk/integrations/wa.py`

`mark_wa_messages_read()` no longer delegates to frappe_whatsapp's
`WhatsAppMessage.send_read_receipt()`. New helpers:

- `_post_wa_read_receipt()` — POSTs the receipt to Meta directly and returns
  `(ok, detail)` where `detail` carries the real HTTP status and response body
  (previously reconstructed from stale request flags and logged as `"None\n{}"`).
- `_wa_account_for_message()` — resolves the owning `WhatsApp Account`, falling
  back to the default incoming one, returning `None` rather than raising on a
  since-deleted link.
- `_settle_wa_read_receipt()` — marks a message read locally via
  `frappe.db.set_value` (not `doc.save()`, which would re-run
  `validate`/`on_update`).

Behaviour changes:
- Messages older than 24 h are settled locally with **no** API call.
- `allow_auto_read_receipt` on the account is now honoured; it previously gated
  only the desk form's button while this path sent receipts regardless.
- Failures are counted (`wa_read_receipt_attempts:{name}`, 24 h TTL) behind the
  existing 15-minute backoff key. After 3 attempts the receipt is abandoned:
  **one** Error Log with the real Meta response, then settled locally.

Second-order fix: because `get_ticket_wa_unread_count()` filters on
`status != "marked as read"`, permanently-failing receipts had been inflating
the WhatsApp tab's unread badge forever. Settling clears that too.

### B. `download_corpus` — check before locking — `helpdesk/search.py`

Split into `_corpus_exists()`, `_download_corpus_locked()` (carries `@filelock`)
and a public `download_corpus()` that returns early when the corpus is present.

The existence check must sit **outside** the lock:
`frappe/utils/synchronization.py:44` calls `frappe.log_error` itself before
raising, so catching `LockTimeoutError` cannot suppress the log. Not entering
the lock is the only quiet path. `LockTimeoutError` is still caught for the
genuine-race case, and existence is re-checked inside the lock.

Both `hooks.py` registrations (`after_migrate`, `all`) still point at the public
`download_corpus`; no hook changes needed.

### C. Regression cover

New `helpdesk/tests/test_wa_read_receipt.py` — 9 tests over the give-up path,
backoff, age cutoff, happy path, kill switch, account resolution, and error
formatting. Meta is simulated at the requests boundary; nothing hits the network.

### D. Unblocked the site's test suite (dev-site data fix, not a code change)

`bench run-tests` previously died during frappe's *global test-record bootstrap*
before any test executed. Cause: `Email Account "_Test Comm Account 1"` on this
site had drifted to `enable_incoming=0` while keeping
`enable_automatic_linking=1` — a combination `check_automatic_linking_email_account`
rejects. The `Email Domain "example.com"` test record's `on_update` cascades a
save to every account on that domain, so the invalid record aborted the run.

Fixed by clearing `enable_automatic_linking` on that account. Chose that over
restoring frappe's intended `enable_incoming=1`, which would have started real
mail-pull jobs against the fake `pop.test.example.com`. Verified neither test
account is `default_incoming`/`default_outgoing`, so no real mail routing is
affected.

Note: an earlier console repro of this blocker produced a *different*
(SMTP/DNS) error, because `frappe.in_test` was False outside the test runner and
SMTP validation therefore ran. The automatic-linking defect above is the actual
cause under the runner.

## Verification

**Full app suite, before vs after — identical failure profile.**
`bench --site dev.localhost run-tests --app helpdesk` runs in three category
phases:

| Phase | develop | this branch |
|-------|---------|-------------|
| 1 | 79 tests, 2 failures / 11 errors | 79 tests, 2 failures / 11 errors |
| 2 | 74 tests, 19 errors | **83** tests, 19 errors |
| 3 | 113 tests, 3 failures / 6 errors | 113 tests, 3 failures / 6 errors |

+9 tests (this branch's), no new failures. The ~37 pre-existing failures are
present on `develop` too — mostly tests making live calls to
`hd-whatsapp-api.cecypo.tech` and doctype-schema drift (e.g. `WA API Settings`
missing `default_team`). Out of scope here.

One test, `test_incoming_message_on_lid_is_rerouted_to_pn`, appeared in only one
of the two app-wide runs. Run in isolation, `test_wa_webhook` gives an identical
2 failures / 6 errors on both branches — so that is inter-test pollution in the
app-wide run, not a regression.

The new module on its own: **9/9 passing**
(`run-tests --module helpdesk.tests.test_wa_read_receipt`).

Additionally verified by driving the real functions directly against
`dev.localhost` (`/tmp/verify_wa_read_receipt.py`, `/tmp/verify_corpus.py`),
which is how the work was checked before the suite was unblocked —
**26 checks, all passing:**

- gives up after exactly 3 API calls; exactly 1 Error Log; contains
  `HTTP 400: bad id`; status `marked as read`; unread badge 0
- backoff blocks a second attempt inside the window (1 call, status untouched)
- 30 h-old message settled with 0 API calls and 0 Error Logs
- happy path: 1 call, marked read, no log
- kill switch: 0 calls, status untouched
- `HTTP 400` + `Invalid message id` both present in the error detail
- `ConnectTimeout` reported by type
- unknown account link → `None`, no raise; empty link → default account
- corpus present → locked path not entered, **lock file mtime unchanged**,
  0 new Error Logs
- corpus missing + lock held → `LockTimeoutError` swallowed
- corpus missing + lock free → 3 `nltk.download` calls; re-check under the lock
  skips a download another process just finished

Also: `ruff check` on all three files reports only the 2 errors that already
exist at HEAD (`F841` unused `settings` at wa.py:4254, `F821` undefined `json`
at wa.py:4424) — neither in touched code. Byte-compile clean. No test data left
on the dev site.

## Review pass

- **Blocker**: none.
- **Major**: none.
- **Minor** (found in self-review, fixed before finishing): `_wa_account_for_message`
  originally called `get_cached_doc` unguarded, so a message pointing at a
  deleted account would raise out of a whitelisted endpoint — where the old code
  had a per-message `try/except`. Now returns `None`. Covered by two tests.
- **Minor** (pre-existing, out of scope): `wa.py:4424` uses `json.dumps` with no
  `json` import in scope — a latent `NameError` in the WABA template path.
  Untouched by this branch; worth its own fix.
- **Nit**: the returned `count` now includes messages settled locally without a
  Meta call. Intentional — they did transition to read — and documented.

## Not done / follow-ups

- **frappe_whatsapp `0e7ec57` is still undeployable.** It remains committed
  locally on a branch tracking only `shridarpatil/frappe_whatsapp`. Task A routes
  around it, so nothing is blocked, but the PR upstream (plan task C) is unsent.
- **`search.py` PR to `frappe/helpdesk`** — unsent.
- **~37 pre-existing test failures remain** on both branches — largely tests that
  make live HTTP calls to `hd-whatsapp-api.cecypo.tech` and doctype-schema drift
  (`WA API Settings` missing `default_team`). Untouched here; worth their own pass.
- The underlying reason Meta rejects these receipts is still unknown — that is
  precisely what the new logging exists to reveal. Check Error Log after deploy.
