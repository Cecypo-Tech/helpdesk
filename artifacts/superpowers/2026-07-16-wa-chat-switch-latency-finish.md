# WA Line chat-switch latency — implementation report

Date: 2026-07-16
Branch: develop (uncommitted)
Plan: `2026-07-15-wa-chat-switch-latency-plan.md` (all 9 decisions honoured, one overridden on evidence — see below)

## Outcome (measured, not asserted)

| chat | JSON payload | media fetched on open |
|---|---|---|
| 39-of-40 images | 213 KB → **26 KB** | 5.3 MB → **1.06 MB** |
| 26-of-40 videos | 21 KB → 21 KB | **77.6 MB → 0 MB** |
| 1239 messages | 816 KB → **30 KB** (96%) | 3.7 MB → 2.6 MB |

Browser-verified against a simulated 3-second API round-trip (patched `fetch`),
because local loopback hides the bug the customer sees:

- Old chat's 40 images gone **within ~101 ms**; spinner covers the full 3 s wait;
  new chat renders when data lands. `GHOST_FRAMES: 0`, `oldShownEver: false`,
  with the header confirmed switching at 101 ms — so the zero is meaningful, not vacuous.
- 26 `<video>` elements all `preload="none"`, **0 .mp4 bytes**, nothing buffered.
- "Load older": 48 → 95 bubbles in one 32 KB call; `scrollTop` 5054 → **5159**,
  exactly `scrollHeight_after - scrollHeight_before` — position preserved, no jump.

## Decision overridden by evidence

**Plan decision 8 said: composite `(jid, creation)` index via a patch. Rejected.**

`add_wa_message_composite_index` already existed and had already run
(Patch Log: 2026-06-02 22:20:44) — yet its index did not exist. The table was
created 2026-06-10 10:06:13, eight days *later*: a rebuild silently destroyed the
patch-made index, and patches never re-run. The indexes that survived
(`sender_jid`, `line`, `creation`) are all declared `search_index: 1` in the
DocType JSON, which Frappe recreates on every migrate.

So a patch-added index is precisely the trap this codebase already fell into.
Used `"search_index": 1` on `jid` instead — durable across rebuilds. Verified:
`EXPLAIN` went from `possible_keys: NULL` (full scan of 14,587 rows) to
`type: ref, key: jid_index`, scanning 1,241. The residual filesort sorts ≤1,241
rows (sub-ms), which the composite would have saved — not worth the fragility.

**The related pre-existing bug is now fixed too** — see "Composite index" below.

## Bugs my own verification caught (before shipping)

1. **Cursor tie lost messages.** A full-history walk recovered 762 of 763
   bubbles. Root cause: `creation` is not unique — two message pairs share a
   timestamp to the microsecond (bulk import, `.000000`), so `WHERE creation <
   before` silently dropped the tied sibling at a page boundary, forever.
   Fixed with a compound `(creation, name)` cursor + matching `ORDER BY`; walk
   now recovers 763/763, 0 duplicates. Regression test added.
2. **`reply_targets` wiped on refresh** (ticket tab). `onSuccess` fires for every
   refresh (new message / retry / edit), which re-fetches only the newest page —
   it replaced `replyTargets` wholesale, so after "Load older" an incoming
   message would blank the reply previews on older messages. Now merged+deduped,
   gated on `loadedOlder`, mirroring the `hasMore` handling.
3. **Test that proved nothing.** The first `reply_targets` test passed with
   `reply_targets=0` — people reply to recent messages, so targets resolve inside
   a 40-message page and the path never ran. Added an assert that fails when the
   path isn't exercised, then tuned page sizes to force the boundary.

## What changed

**Backend** (`helpdesk/integrations/wa.py`)
- `get_whatsapp_messages` jid path: opt-in pagination (`limit`/`before`/`before_name`)
  returning `{messages, has_more, reply_targets}`. No `limit` → bare list, so
  existing callers are untouched.
- Pages over **non-reaction** rows (reactions are 19% of the table and would eat
  page slots), then fetches this page's reactions by target, and any reply target
  older than the page — returned separately so they resolve previews without
  rendering as bubbles.
- Extracted `_dedupe_wa_rows` / `_finalize_wa_rows` so page and reply-targets get
  identical treatment.
- Thumbnails on ingest: images via Pillow (480 px, q75); video posters from the
  provider's `jpegThumbnail`, read **before** `_strip_media_thumbnails` discards
  it. Late-arriving media (`_retry_media_download`, `refetch_media_for_message`)
  also generates image thumbs.
- `_buffer_to_bytes` handles the real wire format — a numeric-keyed Buffer
  (`{"0":255,"1":216,…}`), not base64 as assumed. Verified against stored data.

**Schema** (`wa_message.json`) — `jid` gains `search_index`; new `thumbnail_url`.

**Frontend** — `BaileysChat.vue` + `BaileysGroupChatTab.vue`: reset on switch,
server pagination with accumulated pages, merge-on-refresh (incoming wins, so
statuses/edits update), scroll-preserving "Load older" (added to the ticket tab,
which had none). `WhatsAppBubble.vue`: `loading="lazy"`/`decoding="async"`,
thumbnail with fallback to original, `preload="none"` + poster on video.

**Backfill** — `wa_thumbnail_backfill.py` + patch that **enqueues** it (never
blocks `bench migrate`). Ran locally: **1943 generated, 29 skipped, 0 failed in
82 s** — which vindicates the enqueue decision. Resumable; rows that can't be
thumbnailed are pointed at their original so they stop being retried.

## Composite index for `get_wa_conversations` (the follow-up)

`(line, jid, creation)` was intended by `add_wa_message_composite_index` but did
not exist. Two hypotheses were tested and **both were wrong**:

1. *Ran before the table existed?* No — it sits in `[post_model_sync]`, after
   DocType sync.
2. *Frappe's schema sync dropped it?* No — added it by hand, ran `bench migrate`,
   it survived.

What's left is the `CREATE_TIME` evidence: the table was recreated 2026-06-10,
eight days after the patch ran 2026-06-02 (restore or doctype recreate), taking
the index with it. Patches never run twice, so it could never come back.

**It is worth having** (measured, ~14k rows, and this query runs on every page
load and after every incoming message):

| | rows scanned | time |
|---|---|---|
| with composite | 6,092 | **5.2 ms** |
| without | 15,221 | 17.3 ms |

**Fix:** appended a ` #2026-07-16` token to the patch line in `patches.txt`.
Frappe keys Patch Log on the exact string and splits on whitespace to import the
module (`patch_handler.execute_patch` line 167), so the token re-runs the patch
everywhere without touching the module path. Verified end-to-end by reproducing
the prod state locally — old patch logged, index dropped — then migrating: the
patch ran, the index came back (3 columns), both strings now in Patch Log. Also
verified idempotent (re-executing with the index present is a no-op).

**This does re-run on Frappe Cloud prod** on the next deploy's `bench migrate`,
because the tokenised string isn't in those sites' Patch Log. It's safe whether
or not their index survived: the patch checks `SHOW INDEX` first and no-ops.
Whether they actually *need* it is unknown from here — the loss depended on a
table recreation that may be local-only. See below for how to check.

Note this is durable against migrate but **not** against another table
recreation. A DocType `search_index` would self-heal, but only expresses
single-column indexes — composites have no declarative form in Frappe. If it
vanishes again, bump the token.

## Deliberate non-changes

- **Audio left alone.** Browsers default `<audio>` to `preload="metadata"`, which
  is what shows voice-note duration. `preload="none"` would hide it for ~124
  small files — a UX loss for no real gain.
- **GIFs not thumbnailed.** A static JPEG of frame 1 would silently kill the
  animation; they fall back to the original.
- **No ffmpeg / imageio-ffmpeg dependency**, per decision 7. The 340 existing
  videos get no poster (their preview frame is long stripped) — confirmed in the
  browser as `withPoster: 0`, fetching 0 bytes.
- **Reaction removal still leaves the old emoji.** Pre-existing (each reaction is
  its own row; the empty removal row is skipped by `reactionsMap`). Not
  introduced by the merge model, not fixed here.

## Tests (promoted from scratchpad — 29 tests, all passing)

The scratchpad scripts leaned on whatever the local DB happened to contain
(specific jids, 1239 messages), so they were rewritten to build their own
fixtures and now live in the repo:

- `helpdesk/integrations/tests/test_wa_messages_pagination.py` (12)
- `helpdesk/integrations/tests/test_wa_thumbnails.py` (17)

```bash
bench --site dev.localhost run-tests --module helpdesk.integrations.tests.test_wa_messages_pagination
bench --site dev.localhost run-tests --module helpdesk.integrations.tests.test_wa_thumbnails
```

The pagination fixture deliberately contains a **timestamp tie**, a reaction
outside the page window, a reply quoting an ancient message, and a cross-line
duplicate — so the awkward shapes are asserted on every run, not left to chance.
Fixtures are torn down (verified: 0 leftover rows/files, live data untouched).

**The tests were checked against broken code, not just green code.** Reverting
the cursor fix makes `test_tied_creation_timestamps_page_losslessly` fail with
`'_test-pg-05' not found in {...}: the tied sibling was skipped`, and the walk
fail at limit=1 and limit=5.

Three times the tests were wrong rather than the code, which is worth recording:

1. **A single page size proved nothing.** At `limit=3` both tied messages happen
   to fall in the same page, so the walk passed against buggy code. Now swept
   over sizes 1-6 so some boundary always splits the tie.
2. **The walk asserted a stronger contract than the design offers.** It demanded
   globally unique message_ids across pages; the cross-line duplicate legitimately
   straddles a boundary because a stateless cursor can't dedupe across requests —
   the client folds them by message_id on merge. Now asserts what's real: no
   duplicate *rows*, full coverage after message_id dedup, plus an explicit test
   documenting the straddle so the client-side assumption is visible.
3. **The "not worth thumbnailing" fixture was wrong.** An 80×60 q95 JPEG *does*
   shrink at q75. Measured the actual behaviour: Pillow's `optimize=True` rewrites
   Huffman tables, so images under ~100px always shrink (header dominates). The
   real skip case is an image already compressed below q75 at a size where pixel
   data dominates (200×150 @ q30 → ratio 1.03), which is what the backfill's 29
   real skips were.

## Verification commands

```bash
bench --site dev.localhost run-tests --module helpdesk.integrations.tests.test_wa_messages_pagination
bench --site dev.localhost run-tests --module helpdesk.integrations.tests.test_wa_thumbnails
bench --site dev.localhost mariadb -e "SHOW INDEX FROM \`tabWA Message\` WHERE Column_name='jid';"
bench build --app helpdesk
```

Frontend behaviour (ghost chat, load-older, lazy media) remains browser-verified
only — `desk/` has no vitest and the two Playwright specs don't cover WA chat.

## Review pass

- **Blocker** — none outstanding (the two found are fixed above).
- **Major** — resolved: the `get_wa_conversations` composite index is restored
  via a re-runnable patch (see below).
- **Minor** — pagination logic is now duplicated across the two chat components;
  they were already near-duplicates, but this widens it. A shared composable
  (`useWaMessages`) is the obvious cleanup.
- **Minor** — frontend behaviour has no automated coverage; the backend is now
  covered by 29 tests but the Vue components are browser-verified only.
- **Nit** — `thumbnail_url` is reused as "no separate thumbnail" when it equals
  `media_url` (backfill skip marker). Works and is self-describing, but it does
  overload the field's meaning.

## State

Uncommitted on `develop`. Also present: the earlier `TaskNew.vue` description fix
and an unrelated pre-existing `.claude/scheduled_tasks.lock` modification.
Local DB now has 1,972 image messages with thumbnails (real data written by the
backfill — intended and idempotent).
