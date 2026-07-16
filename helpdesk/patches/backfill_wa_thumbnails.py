import frappe


def execute():
	"""Queue thumbnail generation for existing WA Message images.

	Enqueued rather than run inline: sites have thousands of images and
	generating previews takes minutes, which would stall bench migrate and risk a
	deploy timeout. Messages without a thumbnail simply fall back to the original
	image, so the UI is correct before, during, and after the job runs.
	"""
	if not frappe.db.table_exists("WA Message"):
		return
	# The column is added by the DocType sync that runs before patches; if that
	# hasn't happened yet there's nothing to backfill into.
	if not frappe.db.has_column("WA Message", "thumbnail_url"):
		return

	frappe.enqueue(
		"helpdesk.integrations.wa_thumbnail_backfill.backfill_wa_thumbnails",
		queue="long",
		timeout=14400,
	)
