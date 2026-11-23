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
from frappe.core.doctype.file.utils import delete_file as _unlink
from frappe.utils import add_to_date, cint, get_hook_method, now_datetime

DEFAULT_MEDIA_RETENTION_DAYS = 30
MEDIA_TYPES = ("image", "video", "audio", "document", "sticker")
BATCH = 500
# Under the long queue's timeout; whatever is left is picked up by a follow-up job.
TIME_BUDGET_SECONDS = 20 * 60
JOB_ID = "helpdesk:wa-media-retention"
# A file is deleted only when every File row for its URL is WhatsApp's own:
# attached to a WhatsApp message, or saved unattached by WA Line ingest under a
# wa_media_ name. Frappe reuses one file for identical uploads, so a URL can
# also belong to a ticket attachment or a File Manager upload; those stay.
_WA_DOCTYPES = ("WA Message", "WhatsApp Message")


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
	# One run at a time: the nightly run, run_media_cleanup_now and a follow-up
	# job would otherwise take the same batches and delete the same files.
	lock = frappe.cache.lock(frappe.cache.make_key(JOB_ID), timeout=TIME_BUDGET_SECONDS + 900)
	if not lock.acquire(blocking=False):
		return
	try:
		cutoff = add_to_date(now_datetime(), days=-days)
		deadline = time.monotonic() + TIME_BUDGET_SECONDS
		in_use = _in_use_urls(cutoff)
		finished = (
			_strip_base64_copies(cutoff, deadline)
			and _purge_wa_line_media(cutoff, deadline, in_use)
			and _purge_waba_media(cutoff, deadline, in_use)
		)
	finally:
		try:
			lock.release()
		except Exception:
			pass  # expired; nothing to release
	# After the release, or the follow-up would find the lock held and stop.
	if not finished and not frappe.flags.in_test:
		# Not deduplicated on JOB_ID: this job may itself hold that id.
		frappe.enqueue(purge_old_media, queue="long", timeout=TIME_BUDGET_SECONDS + 600)


def _is_local(url: str) -> bool:
	return bool(url) and (url.startswith("/files/") or url.startswith("/private/files/"))


def _in_use_urls(cutoff) -> set[str]:
	"""Files a message inside the window still shows, on either integration.
	Group messages are stored once per WA Line and share one file."""
	urls = set(
		frappe.db.sql_list(
			"""
			SELECT media_url FROM `tabWA Message` WHERE creation >= %(c)s AND IFNULL(media_url, '') != ''
			UNION SELECT thumbnail_url FROM `tabWA Message` WHERE creation >= %(c)s AND IFNULL(thumbnail_url, '') != ''
			""",
			{"c": cutoff},
		)
	)
	if frappe.db.exists("DocType", "WhatsApp Message"):
		urls.update(
			frappe.db.sql_list(
				"SELECT attach FROM `tabWhatsApp Message` WHERE creation >= %s AND IFNULL(attach, '') != ''",
				cutoff,
			)
		)
	return urls


def _is_ours(f) -> bool:
	if f.attached_to_doctype:
		return f.attached_to_doctype in _WA_DOCTYPES
	return (f.file_name or "").startswith("wa_media_")


def _delete_file(url: str, pending: set[str]) -> bool:
	"""Delete every File row for `url`; the disk file is removed after the
	batch commits (see _commit). False, deleting nothing, when any row is not
	WhatsApp's own.

	Direct DELETE rather than frappe.delete_doc: delete_doc scans tabFile on the
	unindexed content_hash and every table for dynamic links, about 2 s a file
	on a site with 100k files. WhatsApp's files have no links or comments to
	clean up."""
	files = frappe.get_all(
		"File", filters={"file_url": url}, fields=["name", "attached_to_doctype", "file_name", "thumbnail_url"]
	)
	if not all(_is_ours(f) for f in files):
		return False
	if get_hook_method("delete_file_data_content"):
		# Files live in remote storage that only its own app can delete.
		for f in files:
			frappe.delete_doc("File", f.name, ignore_permissions=True, force=True, delete_permanently=True)
		return True
	frappe.db.delete("File", {"name": ("in", [f.name for f in files])})
	pending.add(url)
	pending.update(f.thumbnail_url for f in files if _is_local(f.thumbnail_url))
	return True


def _commit(pending: set[str]) -> None:
	frappe.db.commit()
	for url in pending:
		try:
			_unlink(url)
		except OSError:
			pass  # already gone
	pending.clear()


def _row(urls, state, pending, work) -> None:
	"""Run `work` for one message in its own transaction. On any error undo it,
	log it and leave the message for the next run.

	One commit per message, not per batch: old rows still carry a ~1 MB base64
	copy in raw_message, and 500 of those in one transaction overran the
	database's transaction size limit on a production site."""
	try:
		work()
		_commit(pending)
	except Exception:
		frappe.db.rollback()
		for u in urls:
			state.pop(u, None)
		pending.clear()
		frappe.log_error(
			title="WhatsApp media retention: could not delete file",
			message="\n".join(urls) + "\n\n" + frappe.get_traceback(),
		)
		frappe.db.commit()


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


def _url_states(urls: list[str], in_use: set[str], state: dict, pending: set[str]) -> dict:
	"""url -> "gone" (deleted, or remote), "kept" (not WhatsApp's own; stays for
	good) or "wait" (a recent message still shows it; look again another day)."""
	out = {}
	for url in urls:
		if not _is_local(url):
			out[url] = "gone"  # remote CDN links expire on their own
			continue
		if url not in state:
			state[url] = "wait" if url in in_use else ("gone" if _delete_file(url, pending) else "kept")
		out[url] = state[url]
	return out


def _purge_wa_line_media(cutoff, deadline, in_use: set[str]) -> bool:
	# Resume point on (creation, name): each row is read once per run, even the
	# ones left for another day.
	after, after_name = "1970-01-01", ""
	state: dict[str, str] = {}
	pending: set[str] = set()
	while time.monotonic() < deadline:
		rows = frappe.db.sql(
			"""
			SELECT name, creation, media_url, thumbnail_url FROM `tabWA Message`
			WHERE creation < %(cutoff)s AND media_purged = 0 AND content_type IN %(types)s
			  AND (creation > %(after)s OR (creation = %(after)s AND name > %(after_name)s))
			ORDER BY creation, name LIMIT %(n)s
			""",
			{"after": after, "after_name": after_name, "cutoff": cutoff, "types": MEDIA_TYPES, "n": BATCH},
			as_dict=True,
		)
		if not rows:
			return True
		after, after_name = rows[-1].creation, rows[-1].name
		for r in rows:
			urls = [u for u in (r.media_url, r.thumbnail_url) if u]

			def work(r=r, urls=urls):
				states = _url_states(urls, in_use, state, pending)
				if "wait" in states.values():
					return  # stays unpurged so a later run deletes the file and marks it
				frappe.db.sql(
					"""
					UPDATE `tabWA Message`
					SET media_url = %s, thumbnail_url = %s, raw_message = '', media_purged = 1
					WHERE name = %s
					""",
					(
						r.media_url if states.get(r.media_url) == "kept" else "",
						r.thumbnail_url if states.get(r.thumbnail_url) == "kept" else "",
						r.name,
					),
				)

			_row(urls, state, pending, work)
	return False


def _purge_waba_media(cutoff, deadline, in_use: set[str]) -> bool:
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return True
	# One pass over every candidate: rows whose file stays keep `attach` and are
	# simply looked at again tomorrow. WABA volume is small.
	rows = frappe.db.sql(
		"""
		SELECT name, attach FROM `tabWhatsApp Message`
		WHERE creation < %(cutoff)s AND (attach LIKE '/files/%%' OR attach LIKE '/private/files/%%')
		""",
		{"cutoff": cutoff},
		as_dict=True,
	)
	state: dict[str, str] = {}
	pending: set[str] = set()
	for r in rows:
		if time.monotonic() > deadline:
			return False

		def work(r=r):
			if _url_states([r.attach], in_use, state, pending)[r.attach] == "gone":
				frappe.db.set_value("WhatsApp Message", r.name, "attach", "", update_modified=False)

		_row([r.attach], state, pending, work)
	return True
