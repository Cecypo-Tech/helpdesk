# WABA outbound hold — finish · 2026-08-29

Batches an agent's chunked WhatsApp Business replies into a single billable send.

## Why

Meta bills every business message sent inside the 24-hour service window from
**1 Oct 2026** (Kenya utility/service rate **$0.0040**, no volume discount).
Free-form agent replies, previously free, become chargeable.

Measured on production (`support.cecypo.tech`, 30 days to 2026-08-29):

| | |
|---|---|
| Outgoing WABA messages | 2,107 (20 failed → **2,087 billable**) |
| Incoming (never billed) | 2,251 |
| Free-form / template split | 2,102 Manual · 5 Template |
| Unique numbers · tickets | 104 · 201 (10.5 outgoing per ticket) |
| **Cost at $0.0040** | **$8.35/mo ≈ KES 1,077/mo (~$100/yr)** |

Chunking is real but the payoff is small. Reconstructing uninterrupted outgoing
runs: 695 messages (33%) are "extra" beyond the first in their run, but only
**399 (19%)** follow the previous message within 60s — the rest are genuine
follow-ups after real work. Gap distribution:

```
  0-10s  : 157 (22.6%)      60-120s :  62 ( 8.9%)
 10-30s  : 148 (21.3%)     120s+    : 234 (33.7%)
 30-60s  :  94 (13.5%)
```

**Realistic saving: ~$1.60/month.** This was built at the user's explicit
direction after that estimate was presented.

Growth is the reason it may matter later: outgoing went 903 (Mar) → 1,644 (May)
→ 2,134 (Jul). The larger risk is category drift — marketing messages are
$0.0225, 5.6x utility.

## Design

- **Frontend buffer, not backend.** `send_wa_reply` still receives one message
  and behaves identically to before. A Redis buffer would have meant the message
  did not exist as a row until flush, breaking the thread render and the
  realtime echo, and multiplying failure semantics.
- **WABA only.** The Evolution (WA Line) path is not billed by Meta; the
  endpoint deliberately omits the field there so the composer's default of 0
  applies and no latency is added to a free channel.
- **Fixed window.** Opens on the first message, closes `holdMs` later without
  extending, so worst-case latency is bounded rather than stretchable.
- **A visible held state was mandatory.** There is no optimistic bubble in the
  thread (it renders only after the send response), so without the strip the
  agent's own message would simply be missing for the hold's duration.

## Files

| File | Change |
|---|---|
| `desk/src/utils/outboundHold.ts` | **new** — framework-free buffer/timer |
| `desk/src/utils/__tests__/outboundHold.spec.ts` | **new** — 15 unit tests |
| `desk/src/components/whatsapp/WhatsAppReplyBox.vue` | held strip, flush triggers, `defineExpose({ flush })` |
| `desk/src/components/whatsapp/WhatsAppChatTab.vue` | pass the setting; flush on incoming message |
| `helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/…json` | `outbound_hold_seconds` (Int, default 5) |
| `helpdesk/integrations/wa.py` | `_outbound_hold_seconds()`; returned from `get_whatsapp_ticket_info()` |
| `helpdesk/tests/test_wa_outbound_hold.py` | **new** — 6 backend tests |

Flush triggers: timer expiry · *Send now* · attachment send · template picker
opening · customer reply arriving · ticket switch · unmount · `beforeunload`.

## Bugs found and fixed during the build

1. **Held text could be sent to the wrong customer.** `WhatsAppChatTab` is
   mounted with `:ticketId` but no `:key`, so Vue can reuse the component across
   a ticket switch and swap the prop. Reading the ticket at flush time would
   deliver a held message to whichever conversation the agent moved to. Fixed by
   capturing the ticket on `append()` and carrying it in the payload; a ticket
   switch now closes the open window instead of merging across it. Regression
   test: "sends held text to the ticket it was typed against".
2. **Held text could race the media upload that flushed it.** Issuing the text
   send before starting the upload is not enough — both are in flight together
   and could be stored in either order. The media path now awaits the held send.
3. **`truncate` + `whitespace-pre-wrap`** were conflicting classes on the strip
   (`truncate` sets `white-space: nowrap`). Now `line-clamp-2`.
4. **Test could have disabled the feature site-wide.** `frappe.db.get_single_value`
   returns `0` for a missing Int, so capturing "the original value" through it
   cannot distinguish unset from an explicit 0 — restoring would have written the
   kill-switch value. The test reads and restores the raw `tabSingles` row.

## Verification

- `npx vitest run` → **33 passed** (15 new)
- `bench run-tests` on `test_wa_outbound_hold`, `test_wa_templates`,
  `test_wa_read_receipt`, `test_wa_unread_state` → **37 passed**, no regressions
- `bench build --app helpdesk` clean; bundle contains the new code
- Setting semantics confirmed against the site: unset → 5, explicit 0 → 0
  (kill switch), explicit 12 → 12, negative → clamped to 0

**Not verified: the rendered UI.** Reaching the SPA needed either the
Administrator password on a site also served as `dev.cecypo.tech`, or importing
browser cookies unavailable under WSL. The held strip, countdown and *Send now* /
undo buttons have not been seen in a browser — they need a human pass.

Local dev state used for the attempt was restored (message `400c3ccb95` creation
put back; the 300s test hold removed).

## Rollout

Default is **5 seconds**. Set `outbound_hold_seconds = 0` in WhatsApp Helpdesk
Settings to disable without a deploy — no code change, no restart beyond the
settings cache.

Worth telling agents that their message now pauses briefly before sending.

## Declined

A monthly tripwire on outgoing WABA volume (alert above ~10k/month ≈ $40) was
recommended and **declined on 2026-08-29**. Not built, not pending.

If the bill is ever revisited, the numbers to re-measure are outgoing
`WhatsApp Message` rows per month and the Manual/Template split — the queries
are in this document's first section.
