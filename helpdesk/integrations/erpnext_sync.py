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


def sync_customers(page_size: int = PAGE_SIZE, full: bool = False) -> dict:
	"""Pull customers from ERPNext and mirror them.

	Incremental: asks only for rows modified since the last successful run. The
	endpoint orders by `modified asc` precisely so this is safe to page.

	`full=True` ignores the stored watermark and re-reads everything. That
	escape hatch is not theoretical — the first time this ran against production
	it did nothing at all, because a stale watermark left by the test suite made
	it ask for records newer than any that existed.
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
	since = None if full else cfg.get("last_customer_sync")

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

		# Commit each page rather than the whole run. A full sync is ~15 pages
		# and 85 seconds against production; holding one write transaction open
		# for that long invites the lock contention this app has already been
		# bitten by, discards every page if the last one fails, and shows an
		# operator no progress. The watermark makes resuming natural, so a
		# partially completed run is genuinely useful rather than wasted.
		if high_water and str(high_water) != str(since):
			frappe.db.set_single_value(DOCTYPE, "last_customer_sync", high_water)
		frappe.db.commit()

		if not data.get("has_more"):
			break
		offset += page_size

	# Advance the watermark to the newest record actually SEEN, never to now().
	# Helpdesk and ERPNext are different machines with different clocks; if
	# ERPNext's is behind, stamping now() here would skip everything modified
	# inside that skew — permanently, because the watermark only moves forward.
	# An empty page leaves it untouched for the same reason.
	if high_water and str(high_water) != str(since):
		frappe.db.set_single_value(DOCTYPE, "last_customer_sync", high_water)
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
	if frappe.session.user != "Administrator":
		frappe.only_for(["System Manager", "Administrator"])
	frappe.enqueue(
		"helpdesk.integrations.erpnext_sync.sync_customers",
		queue="long",
		timeout=1800,
		job_id="erpnext_customer_sync",
		deduplicate=True,
	)
	return {"status": "queued"}


def run_scheduled_sync() -> dict:
	"""Hourly tick that runs a full sync when sync_interval_hours has elapsed.

	The interval lives in settings, so the scheduler is asked every hour and
	decides here rather than in hooks.py — a cron expression cannot read a
	Single. Without this, sync_interval_hours was a setting nothing consumed and
	the mirror only moved when somebody pressed a button.

	Customers first, then entitlements: entitlements map onto HD Customers by
	erpnext_customer, so a fresh customer's contract cannot land before the
	customer does. Both are enqueued rather than run inline; the entitlement job
	deduplicates, so a slow customer pass simply means it picks up next hour.
	"""
	if not is_configured():
		return {"ok": False, "error": "not configured", "ran": False}

	interval = int(settings().get("sync_interval_hours") or 0)
	if interval <= 0:
		# Explicitly opted out of scheduling. Manual sync still works.
		return {"ok": True, "error": None, "ran": False, "reason": "interval is 0"}

	last = settings().get("last_customer_sync")
	if last:
		hours = frappe.utils.time_diff_in_hours(frappe.utils.now_datetime(), last)
		if hours < interval:
			return {"ok": True, "error": None, "ran": False,
			        "reason": f"{round(hours, 1)}h of {interval}h elapsed"}

	enqueue_customer_sync()
	enqueue_entitlement_sync()
	return {"ok": True, "error": None, "ran": True}


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


# ── Entitlements ─────────────────────────────────────────────────────────────
#
# A Sales Order with an Auto Repeat is a support contract. Its items say what is
# covered; the Auto Repeat schedule says until when. Many orders fold into one
# row per (customer, product).
#
# Two rules carry the business logic, and both exist because of how Auto Repeat
# actually behaves here:
#
#   support_expiry = Auto Repeat.next_schedule_date + 1 month. Orders are issued
#   a month AHEAD of the period they renew, so the next scheduled order date is
#   one month before cover lapses. A customer signing on 19 Aug 2026 renews on
#   1 Aug 2027 and is covered to 1 Sep 2027.
#
#   NOT delivery_date, which was the first guess and is wrong: on live data it
#   equals the order date (2026-08-01 ordered, 2026-08-01 "delivery"), so using
#   it would expire every customer within weeks of them renewing.
#
#   renewal_unpaid = per_billed < 100 on the order that won. Auto Repeat SUBMITS
#   the renewal about a month BEFORE expiry, so taking the newest date alone
#   would silently grant another year of cover to somebody who never renewed.
#   The flag surfaces that instead of hiding it.

ENTITLEMENT_PAGE = 2000



def entitlement_expiry(row: dict) -> str | None:
	"""When cover lapses for one recurring order line.

	Orders are raised a month before the period they pay for, so the Auto
	Repeat's next_schedule_date is one month short of the true expiry.

	If the Auto Repeat is gone — deleted, or an ERPNext that predates the
	schedule fields in the endpoint — fall back to the order date plus a full
	cycle plus the same month. That is deliberately the generous direction: this
	gates support, and wrongly locking out a paying customer is the worse error.
	"""
	scheduled = (row.get("ar_next_schedule_date") or "").strip() if isinstance(
		row.get("ar_next_schedule_date"), str
	) else row.get("ar_next_schedule_date")

	if scheduled:
		return str(frappe.utils.add_months(frappe.utils.getdate(scheduled), 1))

	ordered = row.get("ordered_on")
	if ordered:
		return str(frappe.utils.add_months(frappe.utils.getdate(ordered), 13))

	return None


def fetch_entitlements(params: dict | None = None) -> dict:
	"""Seam for tests, and the only place the endpoint name is written."""
	return call("helpdesk_customer_entitlements", params or {"limit": ENTITLEMENT_PAGE})


def item_to_product_map() -> dict[str, str]:
	"""ERPNext Item code -> HD Product. Many codes may mean one product."""
	mapping: dict[str, str] = {}
	try:
		rows = frappe.get_all(
			"HD Product Erpnext Item",
			filters={"parenttype": "HD Product"},
			fields=["parent", "item_code"],
		)
	except Exception:
		# Custom field not migrated yet — nothing is mapped, so nothing syncs.
		return {}
	for row in rows:
		code = (row.get("item_code") or "").strip()
		if code:
			mapping[code] = row["parent"]
	return mapping


def erp_customer_map() -> dict[str, str]:
	"""ERPNext customer name -> HD Customer."""
	try:
		rows = frappe.get_all(
			"HD Customer",
			filters={"erpnext_customer": ["!=", ""]},
			fields=["name", "erpnext_customer"],
		)
	except Exception:
		return {}
	return {r["erpnext_customer"]: r["name"] for r in rows}


def sync_entitlements() -> dict:
	"""Derive HD Customer Product rows from recurring Sales Orders."""
	if not is_configured():
		return {"ok": False, "error": "ERPNext sync is not configured",
		        "created": 0, "updated": 0, "unchanged": 0, "unmapped": 0,
		        "products_created": 0}

	result = fetch_entitlements()
	if not result.get("ok"):
		return {"ok": False, "error": result.get("error"),
		        "created": 0, "updated": 0, "unchanged": 0, "unmapped": 0,
		        "products_created": 0}

	data = result.get("data") or {}
	rows = data.get("entitlements") or []

	items = item_to_product_map()
	customers = erp_customer_map()
	products_created = 0

	if settings().get("auto_create_products"):
		# The product is named after the ITEM CODE, not the item name.
		#
		# Sales Order Item.item_name is denormalised — copied onto the line when
		# the order is raised — so renaming or merging an Item in ERPNext leaves
		# historical lines carrying the old name. Here, lines still read
		# "FrappeCloud Hosting" and "FrappeCloud Hosting [B]" against the single
		# code "FrappeCloud Hosting [Subscription]" after those items were
		# merged. The code is the only stable identifier, so it is both the key
		# and the label. Rename the product afterwards if a friendlier name is
		# wanted on the ticket badge.
		unmapped_codes_to_create = []
		for row in rows:
			code = (row.get("item_code") or "").strip()
			if code and code not in items and code not in unmapped_codes_to_create:
				unmapped_codes_to_create.append(code)

		for code in unmapped_codes_to_create:
			try:
				product = ensure_product_for_item(code)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"ERPNext sync: could not create a product for item {code}",
				)
				continue
			if product:
				if product["created"]:
					products_created += 1
				items[code] = product["name"]

	# Fold to one winner per (customer, product): the order with the latest
	# computed expiry. Its per_billed is what the flag reflects — an older paid
	# order must not make an unpaid renewal look settled.
	best: dict[tuple, dict] = {}
	unmapped_codes = set()

	for row in rows:
		product = items.get((row.get("item_code") or "").strip())
		if not product:
			unmapped_codes.add(row.get("item_code"))
			continue
		customer = customers.get(row.get("customer"))
		if not customer:
			continue

		# Rank on (expiry, order date). The tie-break is not cosmetic: every
		# order sharing an Auto Repeat computes the SAME expiry, because the
		# endpoint joins the schedule's CURRENT next_schedule_date onto all of
		# that contract's historical orders. So on the day a renewal is raised,
		# the old paid order and the new unpaid one tie exactly — and whichever
		# wins decides renewal_unpaid. Newest order must win, or a renewal
		# nobody has paid yet inherits last year's per_billed = 100 and looks
		# settled.
		rank = (str(entitlement_expiry(row) or ""), str(row.get("ordered_on") or ""))
		key = (customer, product)
		current = best.get(key)
		if current is None or rank > (
			str(entitlement_expiry(current) or ""), str(current.get("ordered_on") or "")
		):
			best[key] = row

	created = updated = unchanged = 0

	for (customer, product), row in best.items():
		try:
			outcome = apply_entitlement(customer, product, row)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"ERPNext sync: could not mirror entitlement {customer}/{product}",
			)
			continue
		if outcome == "created":
			created += 1
		elif outcome == "updated":
			updated += 1
		else:
			unchanged += 1

	if unmapped_codes:
		# Logged rather than silent: an unmapped code is a gap somebody should
		# see, not a decision the sync gets to make quietly.
		frappe.logger().warning(
			"ERPNext sync: %s unmapped item code(s): %s"
			% (len(unmapped_codes), sorted(c for c in unmapped_codes if c)[:20])
		)

	frappe.db.set_single_value(DOCTYPE, "last_entitlement_sync", frappe.utils.now_datetime())
	frappe.db.commit()

	return {"ok": True, "error": None, "created": created, "updated": updated,
	        "unchanged": unchanged, "unmapped": len(unmapped_codes),
	        "products_created": products_created}


def apply_entitlement(customer: str, product: str, row: dict) -> str:
	"""Create or update one HD Customer Product row from the winning order."""
	expiry = entitlement_expiry(row)
	unpaid = 1 if float(row.get("per_billed") or 0) < 100 else 0
	so = row.get("sales_order")

	existing = frappe.db.get_value(
		"HD Customer Product", {"customer": customer, "product": product},
		["name", "source", "support_expiry", "renewal_unpaid", "source_document"],
		as_dict=True,
	)

	if not existing:
		frappe.get_doc({
			"doctype": "HD Customer Product",
			"customer": customer,
			"product": product,
			"support_expiry": expiry,
			"renewal_unpaid": unpaid,
			"source": "ERPNext",
			"source_document": so,
		}).insert(ignore_permissions=True)
		return "created"

	# A human decision outranks a mirror. This is the ownership rule the whole
	# entitlement model was built around.
	if existing.get("source") == "Manual":
		return "unchanged"

	changes = {}
	if str(existing.get("support_expiry") or "") != str(expiry or ""):
		changes["support_expiry"] = expiry
	if int(existing.get("renewal_unpaid") or 0) != unpaid:
		changes["renewal_unpaid"] = unpaid
	if existing.get("source_document") != so:
		changes["source_document"] = so
	if existing.get("source") != "ERPNext":
		changes["source"] = "ERPNext"

	if not changes:
		return "unchanged"

	frappe.db.set_value("HD Customer Product", existing["name"], changes, update_modified=False)
	return "updated"


@frappe.whitelist()
def enqueue_entitlement_sync() -> dict:
	if frappe.session.user != "Administrator":
		frappe.only_for(["System Manager", "Administrator"])
	frappe.enqueue(
		"helpdesk.integrations.erpnext_sync.sync_entitlements",
		queue="long", timeout=1800, job_id="erpnext_entitlement_sync", deduplicate=True,
	)
	return {"status": "queued"}


def ensure_product_for_item(item_code: str) -> dict | None:
	"""Map an ERPNext item code to an HD Product, creating one if needed.

	The product is named after the item CODE. Item names are denormalised onto
	Sales Order lines and go stale when items are renamed or merged, so the code
	is the only stable identifier available.

	If a product of that name already exists — somebody created it by hand — the
	code is mapped onto it rather than making a near-duplicate. That makes
	auto-creation safe to leave on: it fills gaps, it does not fork the
	catalogue.
	"""
	existing = frappe.db.exists("HD Product", item_code)
	if existing:
		doc = frappe.get_doc("HD Product", item_code)
		codes = {(r.item_code or "").strip() for r in (doc.get("erpnext_items") or [])}
		if item_code not in codes:
			doc.append("erpnext_items", {"item_code": item_code})
			doc.save(ignore_permissions=True)
		return {"name": doc.name, "created": False}

	doc = frappe.get_doc({
		"doctype": "HD Product",
		"product_name": item_code,
		"erpnext_items": [{"item_code": item_code}],
	})
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "created": True}
