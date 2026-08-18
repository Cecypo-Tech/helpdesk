import frappe

INDEX_NAME = "hd_customer_product_customer_product"


def execute():
	"""Add a UNIQUE index on (customer, product) to tabHD Customer Product.

	Sub-project B syncs entitlements from ERPNext and POS licensing. Both need
	an idempotent upsert keyed on (customer, product); without a database-level
	constraint, two concurrent syncs can both find no row and both insert,
	leaving a customer holding the same product twice. Every downstream
	entitlement lookup would then see a duplicate.

	A DocType-level `unique: 1` flag only covers a single field, so a composite
	constraint needs a patch — same pattern as
	`add_wa_conversation_read_state_unique_index`.
	"""
	if not frappe.db.table_exists("HD Customer Product"):
		return

	existing = frappe.db.sql(
		"SHOW INDEX FROM `tabHD Customer Product` WHERE Key_name = %s",
		INDEX_NAME,
		as_dict=True,
	)
	if existing:
		return

	frappe.db.sql(
		f"ALTER TABLE `tabHD Customer Product` ADD UNIQUE INDEX `{INDEX_NAME}` (`customer`, `product`)"
	)
