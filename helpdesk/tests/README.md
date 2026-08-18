# Running the helpdesk test suite

## The suite runs in THREE blocks, not one

`bench --site <site> run-tests --app helpdesk` prints three separate summaries:

```
Running  79 integration tests for helpdesk
Running 152 old-frappe-test-class-category tests for helpdesk
Running 273 unspecified-category tests for helpdesk
```

**`tail` on that command shows only the last block.** Most tests written for this
fork land in the third block, so tailing gives a green summary while the first
two blocks are failing. Always grep for all three:

```bash
bench --site dev.localhost run-tests --app helpdesk 2>&1 \
  | grep -E "^Running|Ran [0-9]+ tests|^(OK|FAILED)"
```

## Two environment problems that look like flaky tests

Both produce failures that move around between runs, which makes them read as
flakiness in the code under test. Neither is.

### 1. `ValidationError: Throttled`

Frappe caps User creation at `throttle_user_limit` (default **60**) per rolling
**60 minutes** — `frappe/core/doctype/user/user.py:throttle_user_creation`. The
suite creates users, so running it a handful of times in an hour exhausts the
budget and every user-creating test afterwards fails.

It gets *worse the more you run the suite*, which is exactly backwards from how
a real regression behaves, and it is why the same commit can look fine in the
morning and broken in the afternoon.

**Fix — on the dev/test site only:**

```json
// sites/<site>/site_config.json
{ "throttle_user_limit": 5000 }
```

Do NOT raise this on production; there it is a real abuse control.

### 2. `QueryDeadlockError` / `(1020, "Record has changed since last read")`

`frappe.enqueue` puts jobs on the **real** queue even under test:
`background_jobs.py` computes `call_directly = now or (not is_async and not
frappe.in_test)`, so with `in_test` true that second clause is always false.

On CI nothing consumes those jobs and they are harmless. On a dev bench with
`frappe worker` running, the worker executes them **while the tests are still
running**, mutating the same rows the tests assert on. That produces deadlocks,
`1020` optimistic-lock errors, and a stream of Error Log entries.

Measured on this bench: a run that only executed tests still modified 12
Contacts and generated 85 Error Log rows — all worker activity.

**Fix — stop the worker while running the suite:**

```bash
# in the tmux/pane running it
#   bench worker --queue short,default,long
# stop it, run the suite, start it again
```

The scheduler is a separate concern and is already disabled on `dev.localhost`
(`bench --site dev.localhost scheduler status`).

## Writing tests that do not add to the problem

- **Patch `frappe.enqueue`** whenever the code under test enqueues. Most WhatsApp
  tests already do (`with patch("frappe.enqueue"):`); follow that.
- **Reuse users** rather than creating one per test where you reasonably can.
- **Snapshot and restore singletons.** Several modules write to
  `Helpdesk Bot Settings`; `helpdesk/tests/settings_guard.py` exists to put it
  back, including the live API key in `__Auth`. It updates rows in place rather
  than deleting and re-inserting them, because the delete-then-insert pattern
  takes range locks that deadlock against the worker.

## A stale runner cache also aborts the whole run

```
FileNotFoundError: 'dev.localhost/.test_records.jsonl'
ValueError: Global test record '...' had been deleted resulting in
            inconsistent global state.
```

Delete the cache and re-run; it is rebuilt automatically:

```bash
rm -f sites/dev.localhost/.test_records.jsonl
```
