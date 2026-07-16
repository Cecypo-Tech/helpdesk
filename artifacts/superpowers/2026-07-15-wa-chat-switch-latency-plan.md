# WA Line chat-switch latency — investigation + plan

Date: 2026-07-15
Branch: develop
Status: **APPROVED 2026-07-15 — full scope, Stages 1-3 (incl. thumbnails)**

## Symptom

On the WA Line chat page (`/helpdesk/whatsapp/:lineName`), clicking a different
conversation updates the header name instantly, but the **previous** contact's
messages stay on screen for several seconds before being replaced. Visible on a
customer's Frappe Cloud prod site; not visible locally (loopback hides it).

## Root cause investigation (evidence)

Measured against local `dev.localhost` (14,587 `WA Message` rows).

### 1. Unbounded payload per chat switch — dominant on prod

`helpdesk/integrations/wa.py::get_whatsapp_messages()` — the `jid` (WA Line) path
runs `.where(BM.jid == jid).orderby(BM.creation).run(as_dict=True)` with **no
LIMIT**. The `limit`/`before` params in the signature are wired only into the
WABA `phone` path.

Measured (`get_whatsapp_messages(jid=...)`, 3 runs, warm):

| jid | rows | server time | JSON payload |
|---|---|---|---|
| `254733839393-1399525466@g.us` | 1239 | 17 ms | **783 KB** |
| `120363409622996901@g.us` | 954 | 14 ms | 540 KB |
| `254722293339-1465117472@g.us` | 925 | 14 ms | 541 KB |

The UI renders only the newest 40 (`PAGE_SIZE = 40`, client-side
`slice(-visibleCount)`). So ~783 KB crosses the wire to display ~3% of it.
Server time is trivial; **transfer time is the cost**, which is exactly why it
only shows up on prod (TLS + RTT + real bandwidth) and not on loopback.

### 2. Stale data renders the old chat — why it looks like a hang

`BaileysChat.vue:252`:
```
v-if="messages.loading && !messages.data"
```
On chat switch, frappe-ui's `createResource` sets `loading = true` but does
**not** clear `.data` (verified in `node_modules/frappe-ui/src/resources/resources.js`
lines 65-66; `.data` is only reassigned on success at line 105). Nothing calls
`reset()` in the `jid` watcher (`BaileysChat.vue:566-576`).

So during the fetch: `loading = true`, `data` = **old chat's messages** →
spinner condition is false → the `v-else` branch renders the previous
conversation until the new payload lands. The header is a prop, so it updates
instantly. That mismatch is the reported symptom.

### 3. Eager full-resolution images — the "load issue" suspected

`WhatsAppBubble.vue:157` renders `<img :src="mediaSrc">` with **no
`loading="lazy"`** and no width/height. Media is stored as URLs (`/files/wa_media_*.jpg`),
not inline base64, so it does not bloat the JSON — but every image in the
rendered window fetches immediately.

Image files on disk are unresized originals: **1878 files, 312.7 MB total,
median 147 KB, p90 283 KB, max 2.0 MB**.

Worst case measured — images among each chat's newest 40 messages:

| jid | images in last 40 |
|---|---|
| `254736763255-1621937418@g.us` | **39 / 40** |
| `243413026869419@lid` | 24 / 40 |
| `120363426386881676@g.us` | 21 / 40 |

Opening that first chat eagerly fetches ~39 × 147 KB ≈ **5.7 MB** of images at
once. Answer to the user's question: yes, images are a real and separate cost.

### 4. Missing index on `tabWA Message.jid` — minor now, scales badly

`SHOW INDEX` gives indexes on `name`, `sender_jid`, `line`, `creation` — **none
on `jid`**, which is the column every chat query filters on.

```
EXPLAIN ... WHERE bm.jid = '...' ORDER BY bm.creation
→ type: index   possible_keys: NULL   key: creation
```

Full index scan per switch. Only ~5-17 ms at 14.5k rows, so it is not today's
bottleneck, but it is O(table size) and degrades as history grows.

## Decisions (locked 2026-07-15)

| # | Decision | Choice |
|---|---|---|
| 1 | Scope of surfaces | **Fix both** `BaileysChat.vue` (WA Line page) **and** `BaileysGroupChatTab.vue` (ticket tab) — verified to share the same unbounded fetch and the same `loading && !data` stale-render bug (line 213). |
| 2 | Image thumbnails | Store explicit **`WA Message.thumbnail_url`** field. Missing thumb → fall back to `media_url`. Clicking always opens the original. |
| 3 | Thumb dimensions | **480 px max, quality 75** (2× the 240 px `max-h-60` display, crisp on retina). ~25 KB vs 147 KB median original. |
| 4 | Image backfill (1878 files) | **Patch enqueues a background job.** Never block `bench migrate` on Frappe Cloud. Resumable, skips done/corrupt, logs failures. Uses Pillow via `frappe.utils.image.optimize_image` — no ffmpeg. |
| 5 | Video eager-loading | Add **`preload="none"`** to the `<video>` element. This is the byte win (340 videos / 2.7 GB; one chat renders 26 inline players). |
| 6 | Video posters — new | Capture the provider's **`jpegThumbnail`** in `_save_base64_media()` *before* `_strip_media_thumbnails()` discards it. Free poster frame, no ffmpeg, no re-encode. |
| 7 | Video posters — existing 340 | **Skip entirely.** Their `jpegThumbnail` is already stripped (only 6 rows retain one) and regenerating needs ffmpeg. **No ffmpeg / imageio-ffmpeg dependency is taken.** Old videos show a plain player until clicked — acceptable, since `preload="none"` already delivers the full byte win. |
| 8 | Index shape | Composite **`(jid, creation)`** via `frappe.db.add_index` in a patch — serves both the `jid` filter and the `creation` sort, so the paginated query is a pure index range scan. (Decided, not asked: `search_index: 1` in the DocType JSON only yields a single-column index and leaves a filesort.) |
| 9 | Customer site data volume | Not needed. Measurements are local; pagination bounds the payload to ~40 messages regardless of history size. Larger history only increases the benefit. |

## Constraints / risks for the fix

Pagination on the `jid` path is **not** a drop-in `LIMIT 40`:

- **Reactions consume row slots.** 19% of rows are `content_type='reaction'`
  (2770/14587). The client filters reactions out (`allNonReactions`) *then*
  slices, so a raw `LIMIT 40` yields only ~32 real bubbles.
- **Reply previews need their target.** `messageByMsgId` is built from *all*
  loaded messages; `replyToMessage` resolves against it. If a reply's target is
  older than the page, the preview silently breaks.
- **Dedup across WA Lines.** The `message_id` dedup currently runs over the full
  set (same group stored once per line). Paging makes dedup per-page, so a
  duplicate could slip through at a page boundary.
- `get_whatsapp_messages` is shared with `BaileysGroupChatTab.vue` (ticket tab),
  so the bare-list return shape must stay backward compatible when no `limit`
  is passed.

## Acceptance criteria

1. Switching chats shows a loading state immediately — the previous chat's
   messages never remain on screen.
2. Payload for a 1200-message chat drops from ~783 KB to < 50 KB.
3. "Load older messages" still walks back through full history.
4. Reply previews, reactions, and cross-line dedup still behave as today.
5. Ticket-tab WA chat (`BaileysGroupChatTab.vue`) is unaffected.

## Plan (staged — ship Stage 1 first)

### Stage 1 — low risk, immediate perceived fix

1. **Clear stale data on switch.** Call `messages.reset()` before `loadMessages()`
   in the `jid` watcher of **both** `BaileysChat.vue` (line 566) and
   `BaileysGroupChatTab.vue` (same `loading && !data` bug at line 213), so the
   spinner shows instead of the previous conversation.
   *Verify:* switch between two chats; old messages vanish on click, spinner shows.
2. **Lazy-load media.** Add `loading="lazy"` + `decoding="async"` to the image
   `<img>` in `WhatsAppBubble.vue` (line 157) and the reply-preview thumb (line
   136 — currently pulls a full 147 KB original to render a 56 px `h-14 w-14` box).
   Add `preload="none"` to the `<video>` (line 221).
   *Verify:* DevTools Network on `254736763255-1621937418@g.us` (39 images in last
   40) and `120363388748443278@g.us` (26 videos in last 40) — offscreen media is
   not requested until scrolled to; video bytes are not fetched until play.
3. **Add composite index `(jid, creation)`** via `frappe.db.add_index` in a patch.
   *Verify:* `EXPLAIN` shows `key: jid_creation`, `type: ref`, no filesort.

### Stage 2 — the real payload fix

4. **Server-side pagination for the `jid` path**, mirroring the existing
   `phone`-path contract: when `limit` is passed return
   `{"messages": [...oldest→newest...], "has_more": bool}`; `before` = oldest
   loaded `creation`. Handle the constraints above:
   - Page on **non-reaction** messages, then attach their reactions regardless of
     where the reaction rows fall.
   - Fetch reply targets referenced by the page even when older than the page.
   - Keep dedup correct across page boundaries.
5. **Client switches to server pagination.** `BaileysChat.vue` requests
   `limit: 40`, and `loadMore()` fetches the next older page via `before` instead
   of client-side `slice`. Apply to `BaileysGroupChatTab.vue` as well (decision 1).
   *Verify:* payload < 50 KB for the 1239-message chat; "Load older" walks back
   correctly; replies/reactions/dedup intact; ticket tab still renders.

### Stage 3 — thumbnails (approved; addresses the 312 MB at source)

Lazy loading (Stage 1) defers image cost but does not shrink it — a scrolled
image-heavy chat still pulls full-res originals. Stage 3 shrinks the bytes.

Available tooling (confirmed): `frappe.utils.image.optimize_image(content,
content_type, max_width, max_height, optimize, quality)` and Pillow 12.2.0 in
the bench env. Single ingest choke point: `wa.py::_save_base64_media()` (line
315) — every inbound media file is decoded and saved as a Frappe File there.

6. **Add `WA Message.thumbnail_url` field** (decision 2). Missing value is a
   normal, expected state — always fall back to `media_url`.
7. **Generate thumbnails on ingest.** In `_save_base64_media()`:
   - `image/*` → Pillow downscale to **480 px max @ q75** (decision 3), saved as
     a second File `wa_media_<hash>_thumb.jpg`; store in `thumbnail_url`.
   - `video/*` → capture the provider's **`jpegThumbnail`** as the poster
     (decision 6). It must be read *before* `_strip_media_thumbnails()`
     (wa.py:339) discards it — that ordering is the whole trick. Keep stripping
     it from `raw_message`; only the extracted File is retained.
   - **No ffmpeg / imageio-ffmpeg dependency** (decision 7).
8. **Backfill existing images only.** Patch enqueues a background job (decision
   4) generating thumbs for the 1878 existing images. Resumable; skips
   already-done and corrupt files; logs failures instead of aborting. The 340
   existing videos are **not** backfilled (decision 7).
9. **Serve thumb inline, original in lightbox / on click.**
   `WhatsAppBubble.vue` uses `thumbnail_url` for the inline `<img>` and as the
   `<video poster>`; `openLightbox()` keeps using full `media_url`. Falls back to
   `media_url` whenever `thumbnail_url` is absent.

   *Verify:* on `254736763255-1621937418@g.us` (39 images in last 40), image
   bytes on open drop from ~5.7 MB to a small fraction; lightbox still shows full
   resolution; pre-backfill messages with no thumb still render via fallback; old
   videos render a plain player and fetch no bytes until played.

## Verification commands

```bash
# payload + timing (temp script under helpdesk/, removed after)
bench --site dev.localhost execute helpdesk.bench_wa_tmp.run

# index check
bench --site dev.localhost mariadb -e "SHOW INDEX FROM \`tabWA Message\`;"
bench --site dev.localhost mariadb -e "EXPLAIN SELECT ... WHERE jid='...';"

# build
bench build --app helpdesk
```

No automated frontend test infra exists (`desk/` has no vitest; only two
Playwright e2e specs, neither covering WA chat), so Stage 1-2 verification is
manual via DevTools Network + the measurement script above.
