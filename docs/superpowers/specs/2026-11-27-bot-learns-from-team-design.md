# Bot learns from team replies — design

## Why
The helpdesk bot looks up past tickets when it answers (`embeddings.search_resolved_tickets`), but it learns only from
`resolution_details`, which agents never fill in. On support.cecypo.tech, none of the 1,104 closed tickets has one, so
its 232 ticket embeddings are subject lines only. The team's actual fixes sit in the conversations and the bot never
sees them. The owner wants the bot to learn from those fixes, but with a person approving every note, because notes can
carry one customer's details to another customer and many tickets aren't worth learning from.

## Goals
1. After a ticket closes, the AI reads the conversation and decides whether it holds a reusable fix. It skips:
   - nonsense, greetings, spam and wrong numbers;
   - one-off account matters (balances, a specific invoice, passwords);
   - tickets with no clear fix.
2. For a worthwhile ticket, it drafts a short note in two parts, **Problem** (one or two lines) and **Fix** (the
   steps the team gave). It contains no customer names, phone numbers, amounts, invoice numbers, passwords,
   IP addresses or site URLs.
3. Each draft goes to Raven **#actions** as a poll with **Approve / Skip**, an **Edit** link to the note, and a link
   to the ticket. Any channel member may vote. Approve feeds the note to the bot; Skip discards it.
4. The bot draws only on approved notes.

## Non-goals
- Public knowledge-base articles. The notes are bot context only and never appear on the portal.
- Backfilling the 1,104 old tickets. That could be offered later as a one-off run once the owner trusts the notes.
- Learning from email-only conversations differently. The transcript covers email and WhatsApp alike.

## Anti-goals
- A note reaching the bot without a person's Approve.
- Customer details in a note. The prompt strips them; the approver is the second check.
- Flooding #actions: at most 10 new polls an hour.

## Constraints
- Tickets: the `closed_on` stamp from step 3 marks when a ticket closed, including WhatsApp closes made through
  `db.set_value`.
- Transcript: `weekly_digest._transcript(ticket)` (WhatsApp, WA Line, email).
- Raven: `raven_bridge.create_poll`, `votes`, `close_poll` and `post` need a channel and a user from the caller, the
  same change step 3 made to `post`.
- Bot side: `embeddings.search_resolved_tickets` reads `HD Ticket Embedding` (subject, resolution_details,
  embedding). `embed()` needs the Bot Settings to be enabled.

## Approaches
1. **A small "HD Learned Note" DocType, plus approved notes written into HD Ticket Embedding (chosen).** It gives the
   Edit link a real form, keeps a record of what was approved or skipped, and lets the bot read notes through the
   search it already uses.
2. **Fields on HD Ticket.** No new DocType, but editing would happen on the ticket form, and there's no list of
   pending or approved notes. Rejected.
3. **Draft HD Articles, as the KB gap flow does.** Articles can be published to the portal, so a slip would make a
   customer's details public. Rejected.

## Decided shape
- **HD Learned Note**, named by hash. Fields:
  - `ticket` (Link HD Ticket, unique)
  - `subject`
  - `problem` (Small Text), `fix` (Text)
  - `status`: Pending / Approved / Skipped / Not useful (the AI skipped it)
  - `poll_id`, `poll_message`, `decided_by`
  Permissions: Agent can read and write, Agent Manager has full access.
- **Hourly job** `learn.run()`:
  1. Draft: closed tickets with `closed_on` in the last 7 days and no note, excluding merged and payment-reminder
     tickets, at most 10 a run. The LLM returns `{"useful": bool, "problem": str, "fix": str}`. Bad JSON is retried
     next hour; useful=false creates a Not useful row, so the ticket isn't asked about again.
  2. Poll: for each new Pending note, post the poll. The question carries the note text and the two links
     (`/app/hd-learned-note/<name>` and `/helpdesk/tickets/<n>`).
  3. Votes: for each Pending note with a poll, the first option with more votes than the other wins. A tie or no
     votes waits. Approve re-reads the note, which may have been edited since, writes or replaces the ticket's
     `HD Ticket Embedding` row (subject, then "Problem: … Fix: …", embedded), closes the poll and sets `decided_by`.
     Skip closes the poll.
- **Unlearning.** Changing an Approved note to Skipped on the form deletes its embedding row. That's the undo.
- **Settings.** Add `learn_from_tickets` (ships off), `learn_raven_channel` and `learn_raven_user` to Helpdesk Bot
  Settings. #actions is `erp-support-actions`.
- **Stop subject-only embeddings.** `embed_resolved_tickets` skips tickets with empty `resolution_details`. Subject
  lines alone match the wrong questions and crowd out real notes. The existing subject-only rows are deleted by a
  patch.
- **Digest.** The Monday digest gains a line, "N notes waiting for approval", linking to the filtered list, when
  N > 0.

## Calls made (Claude's, for review)
1. Any #actions member can approve, not only the owner.
2. Polls don't expire; the digest line keeps pending notes visible.
3. The subject-only embeddings are removed. The bot loses nothing useful and stops matching on subjects.
4. A reopened and re-closed ticket keeps its first note; there is no second draft.
5. Builder check done by Claude itself, not a fresh reviewer, to save cost. The plan's review step covers it.

## Left to the builder
- The prompt wording, and how "Problem: … Fix: …" is laid out in the embedding text.
- Whether `raven_bridge` grows `channel`/`user` on every function, or one helper.
