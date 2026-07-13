# Plan: WhatsApp read-receipt error spam, task digest gating, KB dark mode

Date: 2026-07-13
Branch: develop (direct, per explicit user consent)

## Context

Investigated a flood of `Error Log` entries with method "WhatsApp API Error",
error body `"None\n{}"`, triggered via `helpdesk.integrations.wa.mark_wa_messages_read`.
Root cause: a message whose Meta read-receipt call fails is never marked
`"marked as read"`, so it gets retried (and re-logged) on every ticket open /
incoming-message event, and the frappe_whatsapp exception handler logs a
reconstructed error that's usually empty. While investigating, user also
flagged the daily manager task digest not gating by inactive users/agents,
and a dark-mode contrast bug in the KB article title textarea.

## Tasks

### A. Stop the read-receipt retry storm
File: `helpdesk/integrations/wa.py`, `mark_wa_messages_read()` (frappe_whatsapp
branch, ~line 1613-1634).
- Add a `frappe.cache()` backoff key per `WhatsApp Message` name
  (`wa_read_receipt_retry:{name}`, ~15 min TTL).
- Skip calling `send_read_receipt()` again while the key is set.
- After calling it, if `msg_doc.status != "marked as read"`, set the key.
- Verify: force a failing message twice via bench console; confirm only one
  Error Log entry and the second call is skipped.

### B. Debounce the frontend markAsRead() trigger
File: `desk/src/components/whatsapp/WhatsAppChatTab.vue`.
- `markAsRead()` fires on `onMounted` (line 359) and on every
  `helpdesk:whatsapp-message` event (line 311).
- Wrap the call in a small debounce (~2-3s) so a burst of incoming messages
  collapses into a single API call.
- Verify: seed rapid fake incoming messages, confirm only one
  `mark_wa_messages_read` network call in devtools instead of one per message.

### C. Fix useless error logging in frappe_whatsapp
File: `apps/frappe_whatsapp/frappe_whatsapp/frappe_whatsapp/doctype/whatsapp_message/whatsapp_message.py`,
`send_read_receipt()` (~line 400-415).
- Vendored dependency, remote is `upstream` only (no fork of our own) —
  editing it means a future `bench update` on this app must reconcile with
  our local diff; git will stop on conflict rather than silently discard it.
- Capture the actual failing response's status code/body directly (e.g. via
  `e.response` if it's a `requests.HTTPError`) instead of trusting
  `frappe.flags.integration_request`, which can hold a stale response and
  produces the meaningless `"None\n{}"`.
- Verify: force a bad token/invalid message_id, confirm Error Log shows a
  real status/body.

### D. Gate the daily task digest by active users
File: `helpdesk/helpdesk/doctype/hd_task/hd_task.py`,
`send_manager_task_digest()` recipient loop (~line 268-285).
- Recipient loop never checks `HD Agent.is_active` or the linked
  `User.enabled` — a deactivated agent left in `digest_recipients` keeps
  getting pinged forever.
- Skip a recipient when `recipient.agent` resolves to an inactive HD Agent or
  a disabled User.
- Verify: deactivate a test HD Agent in `digest_recipients`, run
  `send_manager_task_digest()` manually, confirm no WA message/notification
  sent to them.

### E. KB article title — dark mode border
Files: `desk/src/pages/knowledge-base/Article.vue:77`,
`desk/src/pages/knowledge-base/NewArticle.vue:39`.
- Title `<textarea>` hardcodes `border-gray-200` / `focus:border-gray-200`,
  no dark-theme override.
- Swap to semantic `border-outline-gray-2` (already used elsewhere, e.g.
  `CommentBox.vue`, `TicketAgentActivities.vue`).
- Verify: `bench build --app helpdesk`, open an article in edit mode with
  dark mode on, confirm the underline is visible.

## Verification summary
- A/D: bench console checks against dev.localhost data.
- B: browser devtools network tab during seeded WhatsApp message burst.
- C: forced API failure, inspect resulting Error Log.
- E: visual check after build, dark mode toggled on.
