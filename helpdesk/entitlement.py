"""Customer product entitlement.

Advisory only. Nothing in this module refuses service: the worst outcome of a
wrong answer here is a misleading badge, never a customer being denied help.

No ERPNext dependency. Entitlements are local rows; syncing them from ERPNext
or POS licensing is a separate sub-project.
"""

import frappe
from frappe.utils import getdate

STATUS_COVERED = "Covered"
STATUS_EXPIRED = "Expired"
STATUS_NOT_ENTITLED = "Not Entitled"
STATUS_UNKNOWN = "Unknown"


def is_expired(support_expiry) -> bool:
	"""True when support lapsed before today.

	A blank expiry means the entitlement never lapses — reading it as expired
	would mark every open-ended entitlement out of contract. Support lasts
	through the whole of the expiry day, so equality is not expiry.
	"""
	if not support_expiry:
		return False
	return getdate(support_expiry) < getdate()


def get_entitlements(customer: str | None) -> list[dict]:
	"""Every product this customer holds, newest expiry information included."""
	if not customer:
		return []
	rows = frappe.get_all(
		"HD Customer Product",
		filters={"customer": customer},
		fields=["name", "product", "support_expiry", "source"],
		order_by="product asc",
	)
	for row in rows:
		row["expired"] = is_expired(row.get("support_expiry"))
	return rows


def get_entitlement(customer: str | None, product: str | None) -> dict | None:
	"""The single entitlement row for this customer and product, if any."""
	if not customer or not product:
		return None
	row = frappe.db.get_value(
		"HD Customer Product",
		{"customer": customer, "product": product},
		["name", "product", "support_expiry", "source"],
		as_dict=True,
	)
	if not row:
		return None
	row["expired"] = is_expired(row.get("support_expiry"))
	return row


def compute_support_status(customer: str | None, product: str | None) -> str:
	"""Classify this customer's coverage for this product.

	Unknown is returned when we cannot tell — no customer, or no product — and
	is deliberately distinct from Not Entitled, which is a positive finding that
	the customer does not hold the product.
	"""
	if not customer or not product:
		return STATUS_UNKNOWN
	row = get_entitlement(customer, product)
	if not row:
		return STATUS_NOT_ENTITLED
	return STATUS_EXPIRED if row["expired"] else STATUS_COVERED


def resolve_product_for_ticket(ticket: str | None) -> str | None:
	"""Which product a ticket is about.

	Order: the ticket's own hd_product, else the customer's sole entitlement.
	The sole-entitlement rule earns its place — a customer who owns only one
	product gets correctly scoped answers before any agent tags the ticket.
	With two or more we do not guess; a wrong scope is worse than none.

	Wrapped in try/except because hd_product is a Custom Field: on a site that
	has not migrated since the fixtures landed the column does not exist and
	Frappe raises OperationalError. Same hazard as baileys_jid.
	"""
	if not ticket:
		return None

	try:
		row = frappe.db.get_value(
			"HD Ticket", ticket, ["hd_product", "customer"], as_dict=True
		)
	except Exception:
		return None

	if not row:
		return None
	if row.get("hd_product"):
		return row["hd_product"]

	entitlements = get_entitlements(row.get("customer"))
	if len(entitlements) == 1:
		return entitlements[0]["product"]
	return None


def products_for_articles(article_names: list[str]) -> dict[str, list[str]]:
	"""Map each article name to the products it is tagged with.

	Articles absent from the result, or present with an empty list, carry no
	tags and are generic.

	Wrapped in try/except because `products` is a Custom Field: on a site that
	has not migrated since the fixtures landed the child table does not exist.
	Returning {} there makes every article generic, which keeps the bot
	answering rather than silently muting it.
	"""
	if not article_names:
		return {}

	try:
		rows = frappe.get_all(
			"HD Article Product",
			filters={"parent": ["in", article_names], "parenttype": "HD Article"},
			fields=["parent", "product"],
		)
	except Exception:
		return {}

	mapped: dict[str, list[str]] = {}
	for row in rows:
		mapped.setdefault(row["parent"], []).append(row["product"])
	return mapped


def filter_articles_for_product(rows: list[dict], product: str | None) -> list[dict]:
	"""Drop articles tagged for other products. Untagged articles always pass.

	Untagged-means-generic is deliberate: it makes the safe state the default,
	so a newly written article is visible everywhere until somebody narrows it,
	rather than invisible until somebody remembers to tag it.

	Rows with no `name` are Outline results that never synced to an HD Article.
	They pass through rather than being dropped — we have no tags for them, and
	silently discarding them would shrink the bot's knowledge for no stated
	reason.
	"""
	if not product or not rows:
		return rows

	names = [r["name"] for r in rows if r.get("name")]
	tags = products_for_articles(names)

	kept = []
	for row in rows:
		name = row.get("name")
		if not name:
			kept.append(row)
			continue
		article_products = tags.get(name) or []
		if not article_products or product in article_products:
			kept.append(row)
	return kept
