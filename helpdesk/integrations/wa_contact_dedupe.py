"""Find and merge Contacts the WhatsApp automation duplicated.

Before suffix matching landed (2026-08-05), `on_whatsapp_message_insert()` minted
a fresh Contact whenever a number failed to match — which it did for every
contact stored nationally (0799…) against WhatsApp's international form
(254799…). Those Contacts carry a name and a number and nothing else.

This is a one-off maintenance tool, deliberately kept out of wa.py: it is not on
the message path, and wa.py is already very large.

Usage:

    bench --site <site> execute helpdesk.integrations.wa_contact_dedupe.report_duplicate_contacts
    bench --site <site> execute helpdesk.integrations.wa_contact_dedupe.merge_duplicate_contacts
    bench --site <site> execute helpdesk.integrations.wa_contact_dedupe.merge_duplicate_contacts \\
        --kwargs "{'dry_run': 0}"

`merge_duplicate_contacts` is a dry run unless told otherwise.
"""

import frappe
from frappe.utils import cint

from helpdesk.integrations.wa import _phone_suffix

# A suffix built from fewer than this many distinct digits is treated as a
# placeholder rather than a real number. Without it, every contact sharing
# 0000000000 looks like one enormous duplicate group — on a dev site that is 162
# frappe test fixtures, and merging them would be irreversible.
MIN_DISTINCT_DIGITS = 4


def _load():
	"""Everything the rules need, in three queries."""
	contacts = {
		c.name: c
		for c in frappe.get_all(
			"Contact",
			fields=["name", "first_name", "last_name", "email_id", "user", "phone", "mobile_no", "creation"],
		)
	}
	numbers: dict[str, str] = {}
	groups: dict[str, set] = {}

	def add(contact_name, raw):
		suffix = _phone_suffix(raw or "")
		if not suffix or contact_name not in contacts:
			return
		groups.setdefault(suffix, set()).add(contact_name)
		numbers.setdefault(f"{contact_name}|{suffix}", raw)

	for row in frappe.get_all(
		"Contact Phone", filters={"parenttype": "Contact"}, fields=["parent", "phone"]
	):
		add(row.parent, row.phone)
	for c in contacts.values():
		add(c.name, c.mobile_no or c.phone)

	customers = {}
	for link in frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "link_doctype": "HD Customer"},
		fields=["parent", "link_name"],
	):
		customers.setdefault(link.parent, link.link_name)

	return contacts, groups, numbers, customers


def _is_automation_shaped(contact, customer) -> bool:
	"""Whether a Contact looks like something the WhatsApp automation minted.

	Name and number only: no email, no customer, no user account. A contact a
	human touched will fail at least one of these.
	"""
	return not (
		(contact.get("email_id") or "").strip()
		or (contact.get("user") or "").strip()
		or customer
	)


def _skip(suffix, names, reason):
	"""A skipped group, summarised.

	Only a sample of names is kept: a placeholder number can be shared by
	hundreds of contacts, and listing them all buries the report — the whole
	output is printed by `bench execute`.
	"""
	names = sorted(names)
	return {
		"suffix": suffix,
		"reason": reason,
		"count": len(names),
		"sample": names[:5],
	}


def _classify(contacts, groups, numbers, customers):
	candidates, skipped = [], []

	for suffix, names in groups.items():
		if len(names) < 2:
			continue
		if len(set(suffix)) < MIN_DISTINCT_DIGITS:
			skipped.append(_skip(suffix, names, "placeholder_number"))
			continue
		if len(names) > 2:
			skipped.append(_skip(suffix, names, "group_too_large"))
			continue

		a, b = sorted(names)
		shaped = [n for n in (a, b) if _is_automation_shaped(contacts[n], customers.get(n))]
		if len(shaped) == 2:
			skipped.append(_skip(suffix, names, "ambiguous_no_keeper"))
			continue
		if not shaped:
			skipped.append(_skip(suffix, names, "no_duplicate_shape"))
			continue

		dupe = shaped[0]
		keeper = b if dupe == a else a
		candidates.append({
			"suffix": suffix,
			"duplicate": dupe,
			"duplicate_name": contacts[dupe].first_name,
			"duplicate_number": numbers.get(f"{dupe}|{suffix}"),
			"duplicate_created": str(contacts[dupe].creation),
			"duplicate_tickets": frappe.db.count("HD Ticket", {"contact": dupe}),
			"keeper": keeper,
			"keeper_name": contacts[keeper].first_name,
			"keeper_number": numbers.get(f"{keeper}|{suffix}"),
			"keeper_customer": customers.get(keeper),
			"keeper_tickets": frappe.db.count("HD Ticket", {"contact": keeper}),
		})

	candidates.sort(key=lambda c: c["suffix"])
	skipped.sort(key=lambda s: (s["reason"], s["suffix"]))
	return candidates, skipped


@frappe.whitelist()
def report_duplicate_contacts(verbose: int = 1) -> dict:
	"""List contacts the WhatsApp automation appears to have duplicated. Read-only.

	Returns {"candidates": [...], "skipped": [...]}. Every group that is not a
	clean pair is reported as skipped with a reason rather than silently dropped,
	so the output accounts for everything it looked at.
	"""
	candidates, skipped = _classify(*_load())

	if cint(verbose):
		print(f"\n{len(candidates)} merge candidate(s):\n")
		for c in candidates:
			print(f"  {c['suffix']}")
			print(
				f"    duplicate: {c['duplicate']!r} ({c['duplicate_name']})"
				f" number={c['duplicate_number']!r} tickets={c['duplicate_tickets']}"
				f" created={c['duplicate_created']}"
			)
			print(
				f"    keeper   : {c['keeper']!r} ({c['keeper_name']})"
				f" number={c['keeper_number']!r} tickets={c['keeper_tickets']}"
				f" customer={c['keeper_customer']!r}"
			)
		reasons: dict[str, int] = {}
		for s in skipped:
			reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
		if reasons:
			print("\nskipped:")
			for reason, count in sorted(reasons.items()):
				print(f"  {count:5} {reason}")
		print("\nNothing has been changed. To apply:")
		print("  bench --site <site> execute"
		      " helpdesk.integrations.wa_contact_dedupe.merge_duplicate_contacts"
		      " --kwargs \"{'dry_run': 0}\"\n")

	return {"candidates": candidates, "skipped": skipped}


def _still_valid(candidate) -> str | None:
	"""Re-derive the rules against the database. Returns a reason when it no longer holds."""
	dupe, keeper = candidate["duplicate"], candidate["keeper"]
	for name in (dupe, keeper):
		if not frappe.db.exists("Contact", name):
			return f"{name} no longer exists"

	contact = frappe.db.get_value("Contact", dupe, ["email_id", "user"], as_dict=True)
	customer = frappe.db.get_value(
		"Dynamic Link",
		{"parenttype": "Contact", "parent": dupe, "link_doctype": "HD Customer"},
		"link_name",
	)
	if not _is_automation_shaped(contact, customer):
		return f"{dupe} no longer looks automation-created"
	return None


@frappe.whitelist()
def merge_duplicate_contacts(dry_run: int = 1, limit: int = 0) -> dict:
	"""Repoint tickets onto the keeper and remove the duplicate.

	A dry run unless `dry_run` is 0. Each pair is re-checked against the database
	before anything is written, so a stale report cannot cause a bad merge.

	Deletion is not forced. If anything still references the duplicate — a
	Communication, a ToDo, another app's link — frappe refuses and the pair is
	reported as failed, rather than the row being destroyed along with whatever
	pointed at it.
	"""
	dry_run = cint(dry_run)
	limit = cint(limit)
	candidates = report_duplicate_contacts(verbose=0)["candidates"]
	if limit:
		candidates = candidates[:limit]

	applied, failed = [], []
	for candidate in candidates:
		reason = _still_valid(candidate)
		if reason:
			failed.append({**candidate, "error": f"skipped: {reason}"})
			continue

		dupe, keeper = candidate["duplicate"], candidate["keeper"]
		keeper_customer = candidate["keeper_customer"]
		tickets = frappe.get_all("HD Ticket", filters={"contact": dupe}, fields=["name", "customer"])

		record = {
			"suffix": candidate["suffix"],
			"duplicate": dupe,
			"keeper": keeper,
			"tickets_repointed": [t.name for t in tickets],
			"customers_filled": [t.name for t in tickets if keeper_customer and not t.customer],
			"deleted": False,
		}

		if dry_run:
			applied.append({**record, "dry_run": True})
			continue

		try:
			for ticket in tickets:
				# set_value, not save: HD Ticket.key is set-once, and re-saving
				# the doc to change a link would trip CannotChangeConstantError.
				frappe.db.set_value("HD Ticket", ticket.name, "contact", keeper, update_modified=False)
				if keeper_customer and not ticket.customer:
					frappe.db.set_value(
						"HD Ticket", ticket.name, "customer", keeper_customer, update_modified=False
					)
			frappe.delete_doc("Contact", dupe, ignore_permissions=True)
			record["deleted"] = True
			applied.append(record)
		except Exception as e:
			frappe.db.rollback()
			failed.append({**candidate, "error": f"{type(e).__name__}: {str(e)[:200]}"})

	if not dry_run:
		frappe.db.commit()

	print(
		f"\n{'DRY RUN — nothing written' if dry_run else 'applied'}:"
		f" {len(applied)} pair(s), {len(failed)} failed"
	)
	for record in applied:
		print(
			f"  {record['duplicate']} -> {record['keeper']}"
			f" ({len(record['tickets_repointed'])} ticket(s),"
			f" {len(record['customers_filled'])} customer field(s) filled)"
		)
	for record in failed:
		print(f"  FAILED {record['duplicate']}: {record['error']}")
	if dry_run:
		print("\nRe-run with --kwargs \"{'dry_run': 0}\" to apply.\n")

	return {"applied": applied, "failed": failed, "dry_run": bool(dry_run)}
