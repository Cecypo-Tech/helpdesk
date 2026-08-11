"""Find and remove WhatsApp Messages Meta delivered more than once.

Meta fans each inbound event out to every app subscribed to the WABA, and
retries any delivery that does not return 200 promptly. Until
`flag_duplicate_whatsapp_message` landed, `frappe_whatsapp` stored every one of
them: `message_id` carries no unique constraint, so the same wamid produced a
row per delivery and the conversation showed the customer's message twice.

The guard stops new duplicates. This clears the ones already stored.

This is a one-off maintenance tool, deliberately kept out of wa.py: it is not on
the message path, and wa.py is already very large.

Usage:

    bench --site <site> execute helpdesk.integrations.wa_duplicate_cleanup.report_duplicate_messages
    bench --site <site> execute helpdesk.integrations.wa_duplicate_cleanup.merge_duplicate_messages
    bench --site <site> execute helpdesk.integrations.wa_duplicate_cleanup.merge_duplicate_messages \\
        --kwargs "{'dry_run': 0}"

`merge_duplicate_messages` is a dry run unless told otherwise.
"""

import frappe
from frappe.utils import cint


def _duplicate_groups() -> dict[str, list[frappe._dict]]:
	"""Every message_id stored more than once, oldest row first within each group.

	Blank ids are excluded rather than grouped. A row can reach the table before
	Meta has issued an id, so "" is an absence of a value, not a value — treating
	it as one would collapse every such row into a single enormous group.
	"""
	rows = frappe.db.sql(
		"""
		SELECT `name`, `message_id`, `type`, `from`, `to`,
		       `reference_doctype`, `reference_name`, `creation`
		FROM `tabWhatsApp Message`
		WHERE `message_id` IS NOT NULL AND `message_id` != ''
		  AND `message_id` IN (
		      SELECT `message_id` FROM `tabWhatsApp Message`
		      WHERE `message_id` IS NOT NULL AND `message_id` != ''
		      GROUP BY `message_id` HAVING COUNT(*) > 1
		  )
		ORDER BY `message_id`, `creation`, `name`
		""",
		as_dict=True,
	)
	groups: dict[str, list[frappe._dict]] = {}
	for row in rows:
		groups.setdefault(row.message_id, []).append(row)
	return groups


def _survivor(group: list[frappe._dict]) -> frappe._dict:
	"""Which row of a duplicate group to keep.

	Oldest first, because that is the delivery the rest of the site already
	reacted to — its name is what any ticket, comment or read-state points at.

	A row carrying a ticket link wins over an older one without, though. The
	linkage is the part that is expensive to lose, and a later delivery can be
	the linked one: on_whatsapp_message_insert returns early for a duplicate, so
	whichever row won the race is the one that got a reference.
	"""
	linked = [r for r in group if r.reference_doctype == "HD Ticket" and r.reference_name]
	return (linked or group)[0]


@frappe.whitelist()
def report_duplicate_messages(verbose: int = 0) -> dict:
	"""Count the duplicate groups, and what they touch. Writes nothing."""
	frappe.only_for("System Manager")

	groups = _duplicate_groups()
	extra = sum(len(g) - 1 for g in groups.values())
	tickets = {
		r.reference_name
		for g in groups.values()
		for r in g
		if r.reference_doctype == "HD Ticket" and r.reference_name
	}

	result = {
		"groups": len(groups),
		"rows_to_remove": extra,
		"tickets_touched": len(tickets),
	}
	if cint(verbose):
		result["detail"] = [
			{
				"message_id": mid,
				"count": len(g),
				"keep": _survivor(g).name,
				"remove": [r.name for r in g if r.name != _survivor(g).name],
			}
			for mid, g in groups.items()
		]
	return result


@frappe.whitelist(methods=["POST"])
def merge_duplicate_messages(dry_run: int = 1, limit: int = 0) -> dict:
	"""Keep one row per message_id and delete the rest.

	A dry run unless dry_run is explicitly falsy, so the default of running this
	with no arguments cannot destroy anything.
	"""
	frappe.only_for("System Manager")

	dry = cint(dry_run) != 0
	cap = cint(limit)
	groups = _duplicate_groups()

	removed: list[str] = []
	for _mid, group in groups.items():
		if cap and len(removed) >= cap:
			break
		keep = _survivor(group)
		for row in group:
			if row.name == keep.name:
				continue
			removed.append(row.name)
			if not dry:
				# Raw delete: these rows have no children and no inbound links —
				# the survivor is what everything points at — and delete_doc would
				# run a document lifecycle over a row that logically never existed.
				frappe.db.delete("WhatsApp Message", {"name": row.name})

	if not dry and removed:
		frappe.db.commit()

	return {
		"dry_run": dry,
		"groups": len(groups),
		"removed": len(removed),
		"names": removed[:50],
	}
