# Move WhatsApp Ingestion Off the Webhook Request — Implementation Plan

**Goal:** Return `200` to Meta quickly enough that it stops retrying, by doing ticket,
contact, notification and bot work in a background job instead of inline.

**Architecture:** Our doc-event hooks stop doing work and start enqueueing it.
`frappe_whatsapp` is not modified. A scheduled sweeper picks up anything a failed job
left unlinked.

**Tech Stack:** Frappe v16, RQ (`frappe.enqueue`), MariaDB, Python 3.14.

## Background

On 2026-08-12, ten Notification Log rows arrived in three minutes carrying the **same**
`wamid` (`…QjZFNUQxQ0ZBQjQxQTUwOEI5AA==`) and the same two status timestamps. Meta does not
send a status ten times; those are retries of a delivery it did not get a timely `200` for.

The rows persisted, so the request did not error — a 500 rolls the Notification Log insert
back with the transaction. Completed but slow is the only reading left: Meta timed out
waiting and re-sent.

That mechanism explains the rest of the history. Repeated timeouts get an endpoint
deprioritised, which is how inbound messages go missing while statuses trickle through,
and why a three-day backlog drained at once on 08-11.

## Global Constraints

- Never modify `apps/frappe_whatsapp` — clean checkout of `shridarpatil/frappe_whatsapp`,
  no fork remote. Everything happens in helpdesk's hooks.
- The duplicate guard stays **inline**. It is one indexed lookup, and it has to be
  synchronous or the row it exists to prevent is already committed.
- No behaviour change visible to an agent beyond latency: same tickets, same contacts,
  same bot replies, same realtime events.
- The job must be safe to run twice. RQ retries, and the sweeper may race it.

## File Structure

- `helpdesk/integrations/wa_ingest.py` — new. The job body and the sweeper. Keeps the
  async plumbing out of `wa.py`, which is ~4700 lines already.
- `helpdesk/integrations/wa.py` — hooks become thin enqueues; the existing bodies move.
- `helpdesk/integrations/bot.py` — `handle_whatsapp_message` stops being an `after_insert`
  hook and is called from the job, after the ticket link exists.
- `helpdesk/hooks.py` — hook wiring, plus the sweeper on the scheduler.
- `helpdesk/integrations/tests/test_wa_ingest.py` — new.

---

### Task 1: Measure before changing anything

**Files:** none — this task produces numbers, not code.

- [ ] Frappe Cloud → site → Analytics / Request Log, filter
      `/api/method/frappe_whatsapp.utils.webhook.webhook`. Record p50 and p95 duration, and
      the status codes.
- [ ] `/app/recorder`: start, replay a status payload and an inbound message payload, stop,
      read the slowest queries for each.
- [ ] Record the split between the two payload shapes. The retries observed were on
      **statuses**, which do not run `on_whatsapp_message_insert` at all — they run
      `frappe_whatsapp`'s `_handle_status` (a `get_value` + `get_doc` + `save`) and our
      `on_whatsapp_message_update`. If statuses are the slow path, Task 3 matters more than
      Task 2, and this plan's emphasis is wrong.
- [ ] Write the numbers into this file before proceeding.

**Gate:** do not implement Tasks 2–4 until this shows which path is slow. Designing against
a guess is how we spent 08-11.

**Outcome (2026-08-12): the gate was waived, deliberately.** Before the measurement was
taken, the retry storm stopped on its own — removing a stray `object: "user"` webhook
subscription pointing at the same endpoint ended it, and the next status batch arrived once
each instead of ten times. So the latency evidence this plan was written against no longer
holds, and Tasks 2–5 were implemented on the narrower argument that holding a webhook open
through contact and ticket creation is fragile regardless. Treat the change as robustness,
not as a fix for the missing inbound messages, which are a Meta-side fault (sender's client
shows two ticks; no `messages` webhook ever raised). The measurement in this task is still
worth taking after deploy.

### Task 2: Inbound message work moves to a job

**Files:** `helpdesk/integrations/wa_ingest.py`, `helpdesk/integrations/wa.py`,
`helpdesk/hooks.py`, `helpdesk/integrations/tests/test_wa_ingest.py`

**Interfaces produced:**
- `process_incoming_message(message_name: str) -> None`

- [ ] Tests first: the hook enqueues rather than linking; running the job links the ticket;
      running it twice links one ticket; a message already carrying a reference is a no-op.
- [ ] Move the body of `on_whatsapp_message_insert`'s inbound branch into
      `process_incoming_message`, keyed by message name so the job re-reads the row rather
      than carrying a stale doc.
- [ ] `on_whatsapp_message_insert` keeps only: the doctype check, the duplicate drop, the
      settings check, and the outgoing branch — then `frappe.enqueue(...,
      enqueue_after_commit=True, job_id=f"wa_ingest_{doc.name}")`. `enqueue_after_commit`
      matters: the job must not start before the row it reads is committed.
- [ ] Make the job idempotent — re-read `reference_doctype`; if a ticket is already linked,
      return.

### Task 3: Status updates stop committing mid-request

**Files:** `helpdesk/integrations/wa.py`

- [ ] `on_whatsapp_message_update` (`wa.py:3834`) and `_publish_fw_message` (`wa.py:426`)
      both call `frappe.db.commit()` inside a doc event. Frappe v16 disallows this and warns;
      it also forces a durability barrier in the middle of the webhook request.
- [ ] Replace with `frappe.publish_realtime(..., after_commit=True)`, which defers the emit
      to the real commit rather than forcing one.
- [ ] Verify the agent still sees a live status change — this is the path that draws the
      delivery ticks.

### Task 4: The bot moves behind the ticket link

**Files:** `helpdesk/hooks.py`, `helpdesk/integrations/bot.py`,
`helpdesk/integrations/wa_ingest.py`

- [ ] `bot.handle_whatsapp_message` currently runs as the second `after_insert` hook and
      returns early unless `reference_doctype == "HD Ticket"`. Once linking is asynchronous
      that guard is always false, and **the bot would silently stop replying**.
- [ ] Remove it from `after_insert` and call it at the end of `process_incoming_message`,
      once the link exists.
- [ ] Test: an inbound message still produces exactly one enqueued bot job, and none when
      the ticket link fails.

### Task 5: Nothing is lost when a job fails

**Files:** `helpdesk/integrations/wa_ingest.py`, `helpdesk/hooks.py`

- [ ] A failed job leaves a stored message with no ticket — invisible to agents, and
      today's inline code cannot produce that state. This is the main risk the change adds.
- [ ] `sweep_unlinked_messages()`: every 15 minutes, re-enqueue `Incoming` rows with no
      `reference_name`, older than 5 minutes, newer than 7 days.
- [ ] Register on `scheduler_events`.
- [ ] Test: a message left unlinked is picked up and linked by the sweeper; a linked one is
      not touched; one older than the window is left alone.

### Task 6: Verify and ship

- [ ] `bench --site dev.localhost run-tests --app helpdesk`
- [ ] `bench build --app helpdesk`
- [ ] Manual: inbound message creates a ticket, appears in the thread, bot replies, status
      ticks update.
- [ ] Commit on `perf/wa-async-ingestion`, merge to `develop`, push.
- [ ] **After deploy:** re-run the Task 1 measurement and confirm p95 dropped, then watch
      the Notification Log for a day and confirm repeated identical `wamid` statuses stop.

## Verification

The success criterion is not "the code is asynchronous" — it is **Meta stops retrying**.
Task 1's numbers before, the same query after, and no duplicate-`wamid` status bursts.

## Known limits

- Agents see an inbound message roughly a second later than today. Acceptable; invisible in
  practice next to the WhatsApp round-trip.
- If the RQ `short` queue backs up, ingestion lags behind the webhook. Worth watching
  queue depth after this lands, since it becomes a new failure mode.
- This does not shorten `frappe_whatsapp`'s own inline work — the Notification Log insert
  and, for statuses, `_handle_status`. If Task 1 shows the time is there, the honest fix is
  overriding the webhook endpoint via `override_whitelisted_methods`, which is a larger
  change and should be its own plan.
