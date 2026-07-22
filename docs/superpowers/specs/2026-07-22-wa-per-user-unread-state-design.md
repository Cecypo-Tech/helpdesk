# WA Line per-agent unread state

## Problem

The WhatsApp (Baileys/WA Line) unread badge — shown in the conversation list, the
sidebar per-line count, and the per-ticket WA tab — is driven entirely by
`WA Message.is_read`, a single boolean stored on the message row itself. There is
no concept of "read by whom."

`mark_wa_messages_read()` and `mark_all_wa_messages_read()` flip that boolean to
`1` for every message in a JID (or line) the moment *any* agent opens the
conversation. Because every other agent's unread count is computed from the same
`is_read = 0` filter, the badge clears for all agents simultaneously, not just the
one who opened the chat. Confirmed by reading `helpdesk/integrations/wa.py`:
`get_wa_conversations` (~L1821-1834), `get_wa_lines` (~L1736-1739),
`get_ticket_wa_unread_count` (~L1953), and the two mark-read functions
(~L1886-1902, ~L1969-1979) all reference the same unscoped field.

The WABA (`WhatsApp Message.status`) side has the identical structural problem,
but `status` also drives the actual outbound "read receipt" sent to the customer
over WhatsApp — a fact that's legitimately global, not per-agent. **WABA is
explicitly out of scope for this change.**

## Goal

Each agent's WA Line unread badge (conversation list, sidebar, ticket tab)
reflects only what *that agent* has opened. Opening a conversation must not
affect any other agent's badge for the same conversation.

## Design

### Data model

New doctype `WA Conversation Read State`:

| field | type | notes |
|---|---|---|
| `user` | Link → User | the agent |
| `jid` | Data | matches `WA Message.jid` |
| `last_read` | Datetime | agent has read everything in this JID up to this time |

One row per (agent, JID) pair that agent has ever opened — bounded by
agents × active conversations, not by message volume. Indexed on `(jid, user)`
(count queries) and `(user, jid)` (upserts, enforced unique).

No row for a (agent, JID) pair means "never opened" — treated as unread from the
start of the conversation, same as today's default for a brand-new chat.

### Query changes (`helpdesk/integrations/wa.py`)

Replace `WHERE direction = 'Incoming' AND is_read = 0` with a join/subquery
against `WA Conversation Read State` scoped to `frappe.session.user`:
`WHERE direction = 'Incoming' AND (last_read IS NULL OR message.creation > last_read)`.

Applies to:
- `get_wa_conversations` — per-JID `unread_count` in the conversation list payload
- `get_wa_lines` — sidebar per-line badge (aggregate across a line's JIDs)
- `get_ticket_wa_unread_count` — ticket tab badge, WA Line branch only

### Mutation changes

- `mark_wa_messages_read(jid, ...)` and `mark_all_wa_messages_read(line)` stop
  bulk-updating message rows. Instead they upsert one
  `WA Conversation Read State` row (or one per JID under the line) for
  `frappe.session.user` with `last_read = now()`.
- Return value stays an integer (count of messages that were unread under the
  previous cursor) — response shape is unchanged, so no frontend changes are
  needed at the call sites (`BaileysChat.vue`, `BaileysConversationList.vue`).

A shared helper, e.g. `_mark_conversation_read_for_user(jid, user=None)`,
backs both mutation endpoints and the backfill case below.

### Historical backfill sync (`_run_sync_old_messages_job`)

This background job imports old messages per chat and currently marks them
`is_read = 1` at import time (L3758) so historical import doesn't generate
false unread badges. Under the cursor model there's no per-message flag to set,
so after importing a chat's history the job must instead advance **every
agent's** cursor for that JID to cover the imported batch — otherwise freshly
imported old messages would appear unread for everyone the next time counts are
computed, since no agent has a cursor yet.

Add a helper `_mark_conversation_read_for_all_agents(jid, upto)` (agents drawn
from `HD Agent.user`) and call it once per chat at the end of that chat's
import, with `upto` = the newest imported message's creation time.

### Cleanup

`WA Message.is_read` becomes fully dead once the four read sites and two write
sites above are the only things touching it. Remove the field from
`wa_message.json` and delete the six now-pointless writes
(~L1071, ~L1110, ~L1429, ~L1530, ~L3758, plus the two bulk-update loops being
replaced in `mark_wa_messages_read` / `mark_all_wa_messages_read`).

### Migration

New patch, e.g. `helpdesk.patches.add_wa_conversation_read_state`:

1. Create the new doctype (via `frappe.reload_doc` / migrate).
2. For every `HD Agent.user` × every distinct `jid` currently present in
   `WA Message`, insert a `WA Conversation Read State` row with
   `last_read = max(creation)` of that JID's existing messages.

This guarantees every agent starts exactly caught up at rollout — no surprise
backlog — per the earlier decision. Conversations/agents that don't exist yet
at migration time naturally fall into the "no row → unread from the start"
case once they do exist, which is correct.

## Testing

Regression test in `helpdesk/tests/test_baileys.py`: two agents (A, B), one JID,
a few incoming messages. Agent A calls `mark_wa_messages_read`. Assert:
- Agent A's view (`get_wa_conversations` / `get_ticket_wa_unread_count` as
  agent A) now shows `0` for that JID.
- Agent B's view of the same JID is unchanged from before A's action.

This is the exact scenario from the bug report and is the primary correctness
guarantee for this change.

## Out of scope

- WABA (`WhatsApp Message.status`) unread badge — same structural issue, not
  addressed here; the status field also drives the outbound customer read
  receipt and needs separate design.
- Any frontend changes — API response shapes for all touched endpoints are
  unchanged.
