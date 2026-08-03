# Plan: WhatsApp read-receipt error spam (round 2) + download_corpus filelock noise

Date: 2026-08-03
Branch: develop
Site affected: cce-support.jh.frappe.cloud (support.cecypo.tech)

## Context

Two recurring `Error Log` families reported by the user.

### 1. `WhatsApp API Error` / body `"None\n{}"`

Triggered via `helpdesk.integrations.wa.mark_wa_messages_read` on ticket open.
This is a **repeat of the 2026-07-13 investigation**
(`2026-07-13-whatsapp-digest-darkmode-fixes-plan.md`, tasks A/B/C).

What shipped and what did not:

| Task | Where | Shipped? |
|------|-------|----------|
| A. `wa_read_receipt_retry:{name}` cache backoff | `helpdesk/integrations/wa.py` | YES (commit `bfeb3a5ac`, on `upstream/develop`) |
| B. frontend debounce | `WhatsAppChatTab.vue` | assumed yes, not re-verified |
| C. real error capture in `send_read_receipt()` | vendored `frappe_whatsapp` | **NO — undeployable** |

Task C is commit `0e7ec57` in `apps/frappe_whatsapp`, on branch `master`, whose
only remote is `upstream → github.com/shridarpatil/frappe_whatsapp`. It is
unpushed and unpushable. Frappe Cloud installs that app from the public repo, so
production runs the pre-patch handler.

Confirmation: the production log title is `WhatsApp API Error`. Our patched
version logs `WhatsApp API Error (read receipt)` (`whatsapp_message.py:423`).
The old handler reconstructs the error from `frappe.flags.integration_request`,
which is stale/empty here, producing the meaningless `"None\n{}"`. So the
underlying Meta rejection is still undiagnosed — most plausibly `message_id`
too old, which would be a permanent failure, not a transient one.

Second-order damage nobody flagged: a message that never reaches
`status == "marked as read"` also stays in the WABA unread count
(`get_ticket_wa_unread_count`, `wa.py:2408-2416`, filter
`status != "marked as read"`). Permanently-failing receipts inflate the tab
badge forever, not just the Error Log.

Decision (user, this session): **own the call in `wa.py`** rather than fork
`frappe_whatsapp`. Get off the vendored code path so the fix deploys with our
own app.

### 2. `Filelock: Failed to aquire .../helpdesk_corpus_download.lock`

`helpdesk/search.py:413`:

```python
@filelock("helpdesk_corpus_download", timeout=1, is_global=True)
def download_corpus():
    try:
        data.find("taggers/averaged_perceptron_tagger_eng.zip")
        ...
```

The lock is acquired **before** the existence check, and `is_global=True` makes
it bench-wide. The job is registered on the `all` scheduler event
(`hooks.py:37-40`), so on a multi-site Frappe Cloud bench every site fires it on
the same tick; one wins, the rest time out after 1s.

Critical detail: `frappe/utils/synchronization.py:44` calls `frappe.log_error`
**itself**, inside the context manager, before raising `LockTimeoutError`.
Therefore catching the exception does NOT suppress the Error Log. The only way
to stop the noise is to **not enter the lock** when there is nothing to
download. The existence check must move outside.

This is upstream `frappe/helpdesk` code, but our fork deploys, so we fix it here
and offer it upstream.

## Acceptance criteria

- No `WhatsApp API Error` entry whose body is empty/`"None\n{}"`; any receipt
  failure logs a real HTTP status and Meta response body.
- A message whose read receipt fails permanently is logged **once**, then stops
  being retried and stops inflating the ticket's unread badge.
- `download_corpus` produces zero Error Logs on a bench where the corpus is
  already present, regardless of how many sites run it concurrently.
- Existing read-receipt behaviour on the happy path is unchanged (message still
  reaches `status == "marked as read"`, customer still sees blue ticks).

## Tasks

### A. Own the WABA read-receipt call in `wa.py`

File: `helpdesk/integrations/wa.py`, `mark_wa_messages_read()` (~2352-2382).

1. New private helper `_post_wa_read_receipt(message_id, account) -> tuple[bool, str]`:
   - resolve `WhatsApp Account` (from `WhatsApp Message.whatsapp_account`;
     fall back to the `is_default_incoming` account)
   - honour `account.allow_auto_read_receipt` — if unset, skip entirely
     (today this flag gates only the desk form JS, not the API; our path
     currently ignores the documented kill switch)
   - POST `{url}/{version}/{phone_id}/messages` with
     `{"messaging_product": "whatsapp", "status": "read", "message_id": ...}`
   - return `(True, "")` on success, `(False, "<status> <body>")` on failure,
     capturing `e.response.status_code` and `e.response.text` directly
2. Widen the `frappe.get_all` in `mark_wa_messages_read` to also fetch
   `creation` and `whatsapp_account`.
3. Age cutoff: for messages older than 24 h, skip the HTTP call entirely and
   set `status = "marked as read"` locally. Meta's customer-service window is
   the natural boundary and these are the likely permanent failures.
4. Attempt counter (replacing the boolean backoff key): after 3 failed
   attempts, give up permanently — set `status = "marked as read"` locally and
   emit exactly one `frappe.log_error` carrying the real Meta body.
5. Use `frappe.db.set_value` for the status write, not `doc.save()`, to avoid
   re-running `WhatsAppMessage.validate`/`on_update` (`set_whatsapp_account`,
   `update_profile_name`) as a side effect of a read receipt.

Rationale for writing `"marked as read"` on give-up: the agent *did* read the
message; only the courtesy receipt to Meta failed. It is already a shared field
across agents, so this matches existing WABA semantics, and it resolves both the
retry loop and the stuck badge.

### B. `download_corpus` — check before locking

File: `helpdesk/search.py:413-425`.

1. Split into `download_corpus()` (public, unlocked) and
   `_download_corpus_locked()` (carries `@filelock`).
2. `download_corpus()` does the three `data.find()` calls first; if all present,
   return immediately — never touches the lock.
3. Only on `LookupError` call `_download_corpus_locked()`, wrapped in
   `try/except LockTimeoutError: return` so the loser of a genuine race exits
   quietly (this catch is for the real-download case only; it does not and
   cannot suppress frappe's internal log line).
4. Re-check existence inside the lock before downloading, so the loser of a race
   that then acquires the lock does not re-download.

### C. Upstream the fixes (non-blocking)

- PR `0e7ec57` to `shridarpatil/frappe_whatsapp` (still worth landing even
  though task A routes around it).
- PR the `search.py` change to `frappe/helpdesk`.

## Verification

Task A:
- `bench --site dev.localhost console`: pick an incoming `WhatsApp Message`,
  corrupt its `message_id`, call `mark_wa_messages_read(ticket=<n>)` four times.
  Expect: attempts 1-3 hit the API, attempt 4 gives up; **exactly one** Error
  Log, containing a real HTTP status and Meta body; final `status ==
  "marked as read"`.
- Seed an incoming message with `creation` older than 24 h → confirm zero HTTP
  calls and an immediate local mark.
- Happy path: a fresh valid message still reaches `"marked as read"` via a real
  Meta 200.
- `get_ticket_wa_unread_count(ticket)` returns 0 after give-up.
- Add a regression test asserting the give-up path logs once and marks read.

Task B:
- `bench --site dev.localhost console`: call `download_corpus()` with the corpus
  already present → returns immediately, no Error Log, lock file untouched
  (check mtime on `config/helpdesk_corpus_download.lock`).
- Two concurrent consoles calling it with the corpus removed → one downloads,
  the other returns quietly.
- `frappe.get_all("Error Log", filters={"method": ["like", "Filelock%"]})` count
  unchanged after the above.

## Risks

- Writing `"marked as read"` locally means the customer never gets blue ticks
  for those messages. They already do not, since the call fails — no regression,
  but it is now silent by design rather than by accident. The single Error Log
  per message preserves visibility.
- Task A duplicates ~20 lines of vendored logic. Accepted deliberately: the
  vendored copy is on a code path we cannot deploy to.
- `search.py` is upstream code; our edit becomes a merge point on the next
  `git merge frappe/develop`. Keep the diff minimal and clearly scoped.
