"""Mirror ERPNext customers into HD Customer.

Ownership is the whole design. ERPNext owns identity — `tax_id`, `territory`,
`customer_group`. Helpdesk owns everything an agent typed — `helpdesk_notes`,
`domain`, `image`. The sync writes only the first set and never touches the
second, because a mirror that destroys an agent's note is worse than no mirror.

It also never RENAMES. `HD Customer` is autonamed by `customer_name`, so the
docname is the name; renaming would cascade through every ticket and
entitlement pointing at it. If ERPNext renames a customer, the link holds via
`erpnext_customer` and the local name simply stays as it was.

Everything fails open: an unreachable ERPNext leaves the existing mirror in
place and reports why. Nothing here may block support.
"""

import frappe
from frappe.utils import now_datetime

from helpdesk.integrations.erpnext_remote import call, is_configured, settings

DOCTYPE = "ERPNext Sync Settings"
PAGE_SIZE = 200
# A guard against an endpoint that always reports has_more, rather than a real
# expectation. 200 * 50 is far beyond any plausible customer count here.
MAX_PAGES = 50

# Written by the sync. Everything else on HD Customer belongs to the helpdesk.
ERP_OWNED_FIELDS = ("tax_id", "territory", "customer_group")


def fetch_page(params: dict) -> dict:
	"""Seam for tests, and the single place the endpoint name is written."""
	return call("helpdesk_customer_sync", params)


def match_customer(row: dict) -> str | None:
	"""Find the HD Customer this ERPNext customer corresponds to.

	Strict at every step, in descending order of confidence. Never fuzzy:
	wrongly merging two real customers is far worse than creating a duplicate
	somebody can merge deliberately.
	"""
	erp_name = row.get("customer")

	linked = frappe.db.get_value("HD Customer", {"erpnext_customer": erp_name}, "name")
	if linked:
		return linked

	tax_id = (row.get("tax_id") or "").strip()
	if tax_id:
		by_tax = frappe.db.get_value("HD Customer", {"tax_id": tax_id}, "name")
		if by_tax:
			return by_tax

	name = (row.get("customer_name") or "").strip()
	for candidate in (name, erp_name):
		if candidate and frappe.db.exists("HD Customer", candidate):
			return candidate

	return None


def apply_row(row: dict) -> str:
	"""Create or update one HD Customer. Returns "created" | "updated" | "unchanged"."""
	existing = match_customer(row)

	if not existing:
		doc = frappe.get_doc({
			"doctype": "HD Customer",
			"customer_name": row.get("customer_name") or row.get("customer"),
			"erpnext_customer": row.get("customer"),
			**{f: row.get(f) for f in ERP_OWNED_FIELDS},
		})
		doc.insert(ignore_permissions=True)
		sync_contacts(doc.name, row.get("contacts") or [])
		return "created"

	changes = {}
	current = frappe.db.get_value(
		"HD Customer", existing, ["erpnext_customer", *ERP_OWNED_FIELDS], as_dict=True
	) or {}

	# Adopt a hand-created customer we have just recognised.
	if not current.get("erpnext_customer"):
		changes["erpnext_customer"] = row.get("customer")

	for field in ERP_OWNED_FIELDS:
		incoming = row.get(field)
		if incoming and current.get(field) != incoming:
			changes[field] = incoming

	sync_contacts(existing, row.get("contacts") or [])

	if not changes:
		return "unchanged"

	frappe.db.set_value("HD Customer", existing, changes, update_modified=False)
	return "updated"


def sync_customers(page_size: int = PAGE_SIZE) -> dict:
	"""Pull customers from ERPNext and mirror them.

	Incremental: asks only for rows modified since the last successful run. The
	endpoint orders by `modified asc` precisely so this is safe to page.
	"""
	if not is_configured():
		return {
			"ok": False,
			"error": "ERPNext sync is not configured",
			"created": 0,
			"updated": 0,
			"unchanged": 0,
		}

	cfg = settings()
	since = cfg.get("last_customer_sync")

	created = updated = unchanged = 0
	offset = 0
	high_water = since

	for _ in range(MAX_PAGES):
		params = {"limit": page_size, "offset": offset}
		if since:
			params["modified_after"] = str(since)

		result = fetch_page(params)
		if not result.get("ok"):
			# Fail open: keep what we already mirrored, do not advance the
			# watermark, and say why.
			return {
				"ok": False,
				"error": result.get("error"),
				"created": created,
				"updated": updated,
				"unchanged": unchanged,
			}

		data = result.get("data") or {}
		rows = data.get("customers") or []

		for row in rows:
			try:
				outcome = apply_row(row)
			except Exception:
				# One malformed customer must not abort the whole run.
				frappe.log_error(
					frappe.get_traceback(),
					f"ERPNext sync: could not mirror {row.get('customer')}",
				)
				continue
			if outcome == "created":
				created += 1
			elif outcome == "updated":
				updated += 1
			else:
				unchanged += 1

			modified = row.get("modified")
			if modified and (not high_water or str(modified) > str(high_water)):
				high_water = modified

		if not data.get("has_more"):
			break
		offset += page_size

	frappe.db.set_single_value(DOCTYPE, "last_customer_sync", now_datetime())
	frappe.db.commit()

	return {
		"ok": True,
		"error": None,
		"created": created,
		"updated": updated,
		"unchanged": unchanged,
		"high_water": str(high_water) if high_water else None,
	}


@frappe.whitelist()
def enqueue_customer_sync() -> dict:
	"""Run the mirror in the background, from the settings screen."""
	frappe.only_for(["System Manager", "Administrator"])
	frappe.enqueue(
		"helpdesk.integrations.erpnext_sync.sync_customers",
		queue="long",
		timeout=1800,
		job_id="erpnext_customer_sync",
		deduplicate=True,
	)
	return {"status": "queued"}


# ── Contacts ─────────────────────────────────────────────────────────────────
#
# Most contacts on this system did not come from ERPNext: hundreds arrive from
# WhatsApp, and others are staff deliberately kept out of ERPNext. So ownership
# is the whole design here.
#
#   * a Contact carrying `erpnext_contact` is ERPNext-owned and maintained here
#   * a Contact WITHOUT it belongs to Helpdesk and is never touched, at all
#
# Matching is by that marker, then by email scoped to the SAME customer. Phone
# is deliberately not a match key: WhatsApp already claims contacts by phone,
# and two systems matching on one key is how silent merges happen.

CONTACT_LINK_DOCTYPE = "HD Customer"


def contacts_linked_to(customer: str) -> list[str]:
	try:
		return frappe.get_all(
			"Dynamic Link",
			filters={
				"parenttype": "Contact",
				"parentfield": "links",
				"link_doctype": CONTACT_LINK_DOCTYPE,
				"link_name": customer,
			},
			pluck="parent",
		)
	except Exception:
		return []


def match_contact(row: dict, customer: str) -> str | None:
	"""Find the Contact this ERPNext contact corresponds to, or None."""
	erp_name = row.get("contact")

	try:
		owned = frappe.db.get_value("Contact", {"erpnext_contact": erp_name}, "name")
	except Exception:
		# Custom field not migrated yet — treat everything as unowned and create.
		return None
	if owned:
		return owned

	email = (row.get("email") or "").strip()
	if not email:
		return None

	# Scoped to this customer's contacts. A global email match would silently
	# reassign a real person from one company to another.
	for candidate in contacts_linked_to(customer):
		if frappe.db.get_value("Contact", candidate, "email_id") != email:
			continue
		# Only adopt a contact ERPNext already owns. One without the marker
		# belongs to Helpdesk and is off limits, matching email or not.
		if frappe.db.get_value("Contact", candidate, "erpnext_contact"):
			return candidate
	return None


def ensure_customer_link(doc, customer: str) -> None:
	existing = {
		l.link_name for l in (doc.get("links") or [])
		if l.link_doctype == CONTACT_LINK_DOCTYPE
	}
	if customer not in existing:
		doc.append("links", {"link_doctype": CONTACT_LINK_DOCTYPE, "link_name": customer})


def split_name(full_name: str, fallback: str) -> tuple[str, str]:
	parts = (full_name or "").strip().split()
	if not parts:
		return fallback, ""
	return parts[0], " ".join(parts[1:])


def apply_contact(row: dict, customer: str) -> str:
	"""Create or update one Contact. Returns "created" | "updated" | "skipped"."""
	erp_name = row.get("contact")
	if not erp_name:
		return "skipped"

	existing = match_contact(row, customer)
	first, last = split_name(row.get("full_name"), erp_name)
	email = (row.get("email") or "").strip()
	mobile = (row.get("mobile_no") or "").strip()

	if not existing:
		doc = frappe.get_doc({
			"doctype": "Contact",
			"first_name": first,
			"last_name": last,
			"designation": row.get("designation"),
			"erpnext_contact": erp_name,
		})
		if email:
			doc.append("email_ids", {"email_id": email, "is_primary": 1})
		if mobile:
			# Never set mobile_no directly: Contact.validate() derives it from
			# this child table and would overwrite a direct assignment.
			doc.append("phone_nos", {"phone": mobile, "is_primary_mobile_no": 1})
		ensure_customer_link(doc, customer)
		doc.insert(ignore_permissions=True)
		return "created"

	doc = frappe.get_doc("Contact", existing)
	if not doc.get("erpnext_contact"):
		# Belt and braces: match_contact already refuses these.
		return "skipped"

	dirty = False
	if first and doc.first_name != first:
		doc.first_name = first
		dirty = True
	if doc.last_name != last:
		doc.last_name = last
		dirty = True
	if row.get("designation") and doc.designation != row.get("designation"):
		doc.designation = row.get("designation")
		dirty = True

	# Additive on child tables. WhatsApp discovers numbers ERPNext has never
	# seen; a mirror that removed them would delete real contact details.
	if email and email not in [e.email_id for e in (doc.email_ids or [])]:
		doc.append("email_ids", {"email_id": email})
		dirty = True
	if mobile and mobile not in [p.phone for p in (doc.phone_nos or [])]:
		doc.append("phone_nos", {"phone": mobile})
		dirty = True

	before_links = len(doc.get("links") or [])
	ensure_customer_link(doc, customer)
	if len(doc.get("links") or []) != before_links:
		dirty = True

	if not dirty:
		return "skipped"

	doc.save(ignore_permissions=True)
	return "updated"


def sync_contacts(customer: str, contacts: list[dict]) -> dict:
	"""Mirror one customer's contacts. Never raises: a bad contact is logged
	and skipped rather than aborting the customer, let alone the run."""
	created = updated = 0
	for row in contacts or []:
		try:
			outcome = apply_contact(row, customer)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"ERPNext sync: could not mirror contact {row.get('contact')}",
			)
			continue
		if outcome == "created":
			created += 1
		elif outcome == "updated":
			updated += 1
	return {"created": created, "updated": updated}
