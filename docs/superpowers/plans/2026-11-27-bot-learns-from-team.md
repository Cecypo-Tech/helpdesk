# Bot Learns From Team Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans.

**Goal:** Turn closed tickets into notes a person approves on Raven, and give only approved notes to the bot.

**Architecture:**
- New DocType `HD Learned Note` and new module `helpdesk/integrations/learn.py`, with an hourly `run()` that drafts,
  posts polls and reads votes.
- Approved notes become `HD Ticket Embedding` rows, which the bot already searches.

**Spec:** `docs/superpowers/specs/2026-11-27-bot-learns-from-team-design.md`

## Global Constraints
- `learn_from_tickets` ships 0.
- Limits per run: `DRAFT_BATCH = 10`, `POLL_BATCH = 10`.
- Drafts only cover closes with `closed_on` in the last 7 days. Merged tickets and payment-reminder tickets are
  excluded.
- Bad LLM JSON or an LLM error means no row is written, so the ticket is retried next hour.
- Raven and the LLM are mocked in tests. Tests run on test.localhost only.

## Review Focus
1. An edit made after the poll is the text that gets embedded. Test: `test_approve_uses_edited_note`.
2. A tie or no votes does nothing. Test: `test_tie_waits`.
3. Setting an Approved note to Skipped deletes its embedding. Test: `test_unapprove_removes_embedding`.
4. A Not useful verdict is never asked again, and nothing is posted for it. Test: `test_not_useful_recorded_no_poll`.
5. The subject-only embed path is gone. Test: `test_embed_skips_empty_resolution`.

### Task 1: Notes, polls, votes and embedding
**Files:**
- Create the `hd_learned_note` DocType, with `on_update` to unlearn.
- Create `learn.py` and the patch `remove_subject_only_embeddings`.
- Modify `helpdesk_bot_settings.json` to add `learn_from_tickets`, `learn_raven_channel` and `learn_raven_user`.
- Modify `raven_bridge.py`: `create_poll`, `votes` and `close_poll` take `channel=None, user=None`.
- Modify `embeddings.embed_resolved_tickets` to skip tickets with empty `resolution_details`.
- Modify `weekly_digest.build` to add the "N notes waiting" line.
- Modify `hooks.py` to add `learn.run` to `hourly_long`.

**Interfaces:**
- `parse_note(text) -> dict | None`: returns `{"useful": bool, "problem", "fix"}`, or None for bad JSON. When useful
  is true, `problem` and `fix` must not be empty.
- `draft(now) -> int`
- `post_polls() -> int`
- `check_votes() -> int`
- `approve(note_name)`, which embeds, upserts the embedding row and invalidates the cache.
- `run(now=None)`, which returns unless learning is enabled and a channel is set.

Tests: the five above, plus:
- `test_parse_note`
- `test_draft_skips_reminder_merged_old`
- `test_poll_question_has_links`
- `test_approve_writes_embedding`
- `test_skip_closes_poll_no_embedding`
- `test_disabled_does_nothing`
- `test_digest_mentions_waiting_notes`
- `test_draft_batch_cap`

- [ ] Write the tests and watch them fail. Implement. Watch them pass. Commit.

### Task 2: Land
- [ ] Run the suites touched by this change (learn, weekly_digest, payment_reminders, the embeddings and bot
  tests), then the full suite, and compare against the known failures.
- [ ] Do a fresh review, one fix pass, then merge and push.
