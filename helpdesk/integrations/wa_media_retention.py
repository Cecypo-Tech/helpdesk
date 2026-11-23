"""Delete WhatsApp media older than WhatsApp Helpdesk Settings.media_retention_days.

Runs nightly. WA Line media is saved as a public File with no attachment link,
referenced only by WA Message.media_url / thumbnail_url, so it is found through
the messages. The message keeps its row (text, sender, time) and gets
`media_purged`, which the chat renders as "Media expired".

Also removes the base64 copy of the file that older ingest code stored in
raw_message: it is the bulk of the WA Message table and is never read.
"""

import time

import frappe
from frappe.utils import add_to_date, cint, now_datetime

DEFAULT_MEDIA_RETENTION_DAYS = 30
MEDIA_TYPES = ("image", "video", "audio", "document", "sticker")
BATCH = 500
# Under the long queue's timeout; whatever is left is picked up by a follow-up job.
TIME_BUDGET_SECONDS = 20 * 60
JOB_ID = "helpdesk:wa-media-retention"
# Files that may be deleted with their message. A File attached anywhere else
# (an HD Ticket, a Communication) is still in use and stays.
_WA_DOCTYPES = (None, "", "WA Message", "WhatsApp Message")


def retention_days() -> int:
	# Unset (a Single saved before the field existed) means the default, not 0.
	days = frappe.get_cached_doc("WhatsApp Helpdesk Settings").get("media_retention_days")
	return DEFAULT_MEDIA_RETENTION_DAYS if days is None else cint(days)


@frappe.whitelist()
def run_media_cleanup_now() -> str:
	frappe.only_for("System Manager")
	frappe.enqueue(purge_old_media, queue="long", timeout=TIME_BUDGET_SECONDS + 600, job_id=JOB_ID, deduplicate=True)
	return "queued"


def purge_old_media() -> None:
	days = retention_days()
	if days <= 0:
		return
	cutoff = add_to_date(now_datetime(), days=-days)
	deadline = time.monotonic() + TIME_BUDGET_SECONDS
	finished = (
		_strip_base64_copies(cutoff, deadline)
		and _purge_wa_line_media(cutoff, deadline)
		and _purge_waba_media(cutoff, deadline)
	)
	if not finished and not frappe.flags.in_test:
		# Not deduplicated on JOB_ID: this job may itself hold that id.
		frappe.enqueue(purge_old_media, queue="long", timeout=TIME_BUDGET_SECONDS + 600)


def _is_local(url: str) -> bool:
	return bool(url) and (url.startswith("/files/") or url.startswith("/private/files/"))


def _delete_file(url: str) -> bool:
	"""Delete every File row for `url` (the last one removes it from disk).
	False when the file is still attached to something other than a WhatsApp message."""
	files = frappe.get_all("File", filters={"file_url": url}, fields=["name", "attached_to_doctype"])
	if any(f.attached_to_doctype not in _WA_DOCTYPES for f in files):
		return False
	for f in files:
		frappe.delete_doc("File", f.name, ignore_permissions=True, force=True, delete_permanently=True)
	return True


def _strip_base64_copies(cutoff, deadline) -> bool:
	"""Drop the base64 file copy from raw_message on rows inside the window.
	(Older rows lose raw_message entirely when their media is purged.)"""
	names = frappe.db.sql_list(
		"""
		SELECT name FROM `tabWA Message`
		WHERE creation >= %(cutoff)s AND content_type IN %(types)s
		  AND raw_message LIKE '%%"base64"%%'
		""",
		{"cutoff": cutoff, "types": MEDIA_TYPES},
	)
	paths = ", ".join(["'$.base64'"] + [f"'$.{t}Message.base64'" for t in MEDIA_TYPES])
	for i in range(0, len(names), 200):
		if time.monotonic() > deadline:
			return False
		# JSON_VALID guard: JSON_REMOVE returns NULL for a malformed document.
		frappe.db.sql(
			f"""
			UPDATE `tabWA Message` SET raw_message = JSON_REMOVE(raw_message, {paths})
			WHERE name IN %(names)s AND JSON_VALID(raw_message)
			""",
			{"names": tuple(names[i : i + 200])},
		)
		frappe.db.commit()
	return True


def _purge_wa_line_media(cutoff, deadline) -> bool:
	# Group messages are stored once per line and share one file; a file a
	# message inside the window still shows stays until that message ages out.
	in_use = set(
		frappe.db.sql_list(
			"""
			SELECT media_url FROM `tabWA Message` WHERE creation >= %(c)s AND IFNULL(media_url, '') != ''
			UNION SELECT thumbnail_url FROM `tabWA Message` WHERE creation >= %(c)s AND IFNULL(thumbnail_url, '') != ''
			""",
			{"c": cutoff},
		)
	)
	after = "1970-01-01"  # resume point, so each batch doesn't rescan purged rows from the start
	while time.monotonic() < deadline:
		rows = frappe.db.sql(
			"""
			SELECT name, creation, media_url, thumbnail_url FROM `tabWA Message`
			WHERE creation >= %(after)s AND creation < %(cutoff)s
			  AND media_purged = 0 AND content_type IN %(types)s
			ORDER BY creation LIMIT %(n)s
			""",
			{"after": after, "cutoff": cutoff, "types": MEDIA_TYPES, "n": BATCH},
			as_dict=True,
		)
		if not rows:
			return True
		after = rows[-1].creation
		kept: dict[str, bool] = {}  # url -> file still exists

		def keep(url: str) -> str:
			if not _is_local(url):
				return ""  # remote CDN links expire on their own
			if url not in kept:
				kept[url] = url in in_use or not _delete_file(url)
			return url if kept[url] else ""

		for r in rows:
			frappe.db.sql(
				"""
				UPDATE `tabWA Message`
				SET media_url = %s, thumbnail_url = %s, raw_message = '', media_purged = 1
				WHERE name = %s
				""",
				(keep(r.media_url or ""), keep(r.thumbnail_url or ""), r.name),
			)
		frappe.db.commit()
	return False


def _purge_waba_media(cutoff, deadline) -> bool:
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return True
	# One pass over every candidate: rows whose file stays (still in use) keep
	# `attach` and are simply looked at again tomorrow. WABA volume is small.
	rows = frappe.db.sql(
		"""
		SELECT name, attach FROM `tabWhatsApp Message`
		WHERE creation < %(cutoff)s AND (attach LIKE '/files/%%' OR attach LIKE '/private/files/%%')
		""",
		{"cutoff": cutoff},
		as_dict=True,
	)
	if not rows:
		return True
	in_use = set(
		frappe.db.sql_list(
			"SELECT attach FROM `tabWhatsApp Message` WHERE creation >= %s AND IFNULL(attach, '') != ''",
			cutoff,
		)
	)
	for r in rows:
		if time.monotonic() > deadline:
			return False
		if r.attach in in_use or not _delete_file(r.attach):
			continue
		frappe.db.set_value("WhatsApp Message", r.name, "attach", "", update_modified=False)
		frappe.db.commit()
	return True
