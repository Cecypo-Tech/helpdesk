"""Backfill thumbnails for WA Message media that predates thumbnail generation.

Runs as a background job rather than inline in a patch: sites carry thousands of
images and generating previews for them all takes minutes, which would stall
`bench migrate` (and risk a deploy timeout on Frappe Cloud).

Only images are backfilled. Video posters come from the preview frame the
provider bundles with the webhook, which is stripped before `raw_message` is
stored, so historical videos have nothing left to recover — they fall back to no
poster, which `preload="none"` already makes cheap.
"""

import frappe

from helpdesk.integrations.wa import thumbnail_for_media_url

BATCH_SIZE = 200


def _pending_messages(limit: int) -> list[dict]:
	"""Images with media but no thumbnail yet."""
	return frappe.db.get_all(
		"WA Message",
		filters={
			"content_type": "image",
			"media_url": ["is", "set"],
			"thumbnail_url": ["in", ["", None]],
		},
		fields=["name", "media_url"],
		limit=limit,
		order_by="creation desc",  # newest first: most likely to be looked at
	)


def backfill_wa_thumbnails(batch_size: int = BATCH_SIZE) -> dict:
	"""Generate thumbnails for existing image messages.

	Resumable: picks up wherever it left off, because completed rows have a
	thumbnail_url and drop out of the query. A row that can't be thumbnailed
	(missing file, corrupt image, already small enough) is marked with its own
	media_url so it isn't retried forever — the UI treats that as "no separate
	thumbnail" and loads the original, which is the same fallback used for
	media that never had one.
	"""
	done = skipped = failed = 0

	while True:
		batch = _pending_messages(batch_size)
		if not batch:
			break
		before = done + skipped + failed

		for row in batch:
			try:
				thumb = thumbnail_for_media_url(row.media_url)
				if thumb:
					frappe.db.set_value(
						"WA Message", row.name, "thumbnail_url", thumb, update_modified=False
					)
					done += 1
				else:
					# Point at the original so this row stops being "pending".
					frappe.db.set_value(
						"WA Message", row.name, "thumbnail_url", row.media_url, update_modified=False
					)
					skipped += 1
			except Exception:
				failed += 1
				frappe.log_error(
					frappe.get_traceback(), f"WA thumbnail backfill failed: {row.name}"
				)
				# Don't let one bad file stop the run.
				frappe.db.set_value(
					"WA Message", row.name, "thumbnail_url", row.media_url, update_modified=False
				)
		frappe.db.commit()

		# Every row above is written one way or another, so a batch that leaves
		# the pending set unchanged means writes aren't sticking. Bail rather
		# than spin on the same rows forever.
		if done + skipped + failed == before:
			frappe.log_error(
				f"WA thumbnail backfill made no progress on a batch of {len(batch)}; stopping.",
				"WA thumbnail backfill stalled",
			)
			break

	result = {"generated": done, "skipped": skipped, "failed": failed}
	frappe.logger().info(f"WA thumbnail backfill complete: {result}")
	return result


@frappe.whitelist()
def enqueue_backfill() -> str:
	"""Queue the backfill. Exposed so it can be re-run by hand if a run is lost."""
	frappe.only_for("System Manager")
	frappe.enqueue(
		"helpdesk.integrations.wa_thumbnail_backfill.backfill_wa_thumbnails",
		queue="long",
		timeout=14400,
	)
	return "queued"
