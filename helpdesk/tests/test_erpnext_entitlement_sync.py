"""Derive HD Customer Product rows from recurring Sales Orders.

The chain: a Sales Order with an Auto Repeat is a support contract; its items
say what is covered; delivery_date says until when. Helpdesk folds many orders
into one row per (customer, product).

Two rules carry the business logic:

  * support_expiry is MAX(delivery_date) across that product's orders. The Auto
    Repeat issues a fresh SO each period, so a customer accumulates orders and
    the newest one is their current cover.

  * renewal_unpaid is per_billed < 100. Auto Repeat submits the renewal order
    about a month BEFORE expiry, so taking the latest date alone would silently
    grant another year to somebody who never renewed. The flag makes that
    visible instead of hiding it.

Unmapped item codes are ignored, never auto-created: the catalogue is a
deliberate 5-15 rows and would otherwise fill with billing SKUs.
"""

import unittest
from unittest.mock import patch

import frappe

from helpdesk.integrations import erpnext_sync

PREFIX = "_test-entsync-"
CUSTOMER = PREFIX + "co"
PRODUCT = PREFIX + "eTIMS"


def payload(rows):
    return {"ok": True, "error": None,
            "data": {"version": 1, "as_of": "2026-08-19 10:00:00",
                     "count": len(rows), "truncated": False, "entitlements": rows}}


def ent(item_code, delivery_date, per_billed=100.0, customer=None, so="SO-1"):
    return {
        "customer": customer or (PREFIX + "erp-co"),
        "item_code": item_code,
        "item_name": item_code,
        "delivery_date": delivery_date,
        "so_status": "Completed" if per_billed >= 100 else "To Bill",
        "per_billed": per_billed,
        "sales_order": so,
        "ordered_on": "2026-01-01",
        "currency": "KES",
    }


def cleanup():
    for name in frappe.get_all("HD Customer Product", filters={"customer": CUSTOMER}, pluck="name"):
        frappe.delete_doc("HD Customer Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all("HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"):
        frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all("HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"):
        frappe.delete_doc("HD Customer", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class _Base(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        p = patch.object(erpnext_sync, "is_configured", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.saved = frappe.db.get_single_value(erpnext_sync.DOCTYPE, "last_entitlement_sync")
        self.addCleanup(self.restore)

        frappe.get_doc({
            "doctype": "HD Customer", "customer_name": CUSTOMER,
            "erpnext_customer": PREFIX + "erp-co",
        }).insert(ignore_permissions=True)
        self.product = frappe.get_doc({
            "doctype": "HD Product", "product_name": PRODUCT,
            "erpnext_items": [{"item_code": "TimsParser"}],
        }).insert(ignore_permissions=True).name
        frappe.db.commit()

    def restore(self):
        frappe.db.set_single_value(erpnext_sync.DOCTYPE, "last_entitlement_sync", self.saved)
        frappe.db.commit()

    def tearDown(self):
        cleanup()

    def run_sync(self, rows):
        with patch.object(erpnext_sync, "fetch_entitlements", return_value=payload(rows)):
            return erpnext_sync.sync_entitlements()

    def row(self):
        return frappe.db.get_value(
            "HD Customer Product", {"customer": CUSTOMER, "product": self.product},
            ["support_expiry", "renewal_unpaid", "source", "source_document"], as_dict=True
        )


class TestMapping(_Base):
    def test_creates_an_entitlement_from_a_mapped_item(self):
        result = self.run_sync([ent("TimsParser", "2027-07-01")])

        self.assertTrue(result["ok"])
        self.assertEqual(result["created"], 1)
        row = self.row()
        self.assertEqual(str(row.support_expiry), "2027-07-01")
        self.assertEqual(row.source, "ERPNext")

    def test_unmapped_item_is_ignored_and_counted(self):
        """The catalogue is a deliberate 5-15 rows. Auto-creating a product per
        billing SKU would fill it with noise."""
        result = self.run_sync([ent("SomeRandomSKU", "2027-07-01")])

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["unmapped"], 1)
        self.assertIsNone(self.row())

    def test_several_item_codes_can_mean_one_product(self):
        doc = frappe.get_doc("HD Product", self.product)
        doc.append("erpnext_items", {"item_code": "TimsParserAnnual"})
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        result = self.run_sync([
            ent("TimsParser", "2026-07-01", so="SO-A"),
            ent("TimsParserAnnual", "2027-07-01", so="SO-B"),
        ])

        self.assertEqual(result["created"], 1, "both codes fold into one product")
        self.assertEqual(str(self.row().support_expiry), "2027-07-01")


class TestExpiryAndRenewal(_Base):
    def test_latest_delivery_date_wins(self):
        """Auto Repeat issues a fresh order each period, so a customer
        accumulates orders and the newest is their current cover."""
        self.run_sync([
            ent("TimsParser", "2025-07-01", so="SO-OLD"),
            ent("TimsParser", "2027-07-01", so="SO-NEW"),
        ])
        row = self.row()
        self.assertEqual(str(row.support_expiry), "2027-07-01")
        self.assertEqual(row.source_document, "SO-NEW")

    def test_unbilled_winning_order_is_flagged_unpaid(self):
        """The renewal order is submitted a month before expiry, before anyone
        pays. Without this flag that silently grants another year of cover."""
        self.run_sync([ent("TimsParser", "2027-07-01", per_billed=0.0, so="SO-NEW")])
        self.assertEqual(self.row().renewal_unpaid, 1)

    def test_fully_billed_order_is_not_flagged(self):
        self.run_sync([ent("TimsParser", "2027-07-01", per_billed=100.0)])
        self.assertEqual(self.row().renewal_unpaid, 0)

    def test_flag_follows_the_winning_order_not_an_older_one(self):
        """An old paid order must not make an unpaid renewal look settled."""
        self.run_sync([
            ent("TimsParser", "2025-07-01", per_billed=100.0, so="SO-OLD"),
            ent("TimsParser", "2027-07-01", per_billed=0.0, so="SO-NEW"),
        ])
        row = self.row()
        self.assertEqual(row.renewal_unpaid, 1)
        self.assertEqual(row.source_document, "SO-NEW")


class TestOwnershipAndFailure(_Base):
    def test_never_overwrites_a_manual_entitlement(self):
        """A human decision outranks a mirror — the rule the whole entitlement
        model was built around."""
        frappe.get_doc({
            "doctype": "HD Customer Product", "customer": CUSTOMER,
            "product": self.product, "support_expiry": "2030-01-01",
            "source": "Manual",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

        self.run_sync([ent("TimsParser", "2027-07-01")])

        row = self.row()
        self.assertEqual(str(row.support_expiry), "2030-01-01")
        self.assertEqual(row.source, "Manual")

    def test_unreachable_erpnext_changes_nothing(self):
        self.run_sync([ent("TimsParser", "2027-07-01")])
        fail = {"ok": False, "data": None, "error": "connection refused"}
        with patch.object(erpnext_sync, "fetch_entitlements", return_value=fail):
            result = erpnext_sync.sync_entitlements()

        self.assertFalse(result["ok"])
        self.assertEqual(str(self.row().support_expiry), "2027-07-01")

    def test_unknown_customer_is_skipped_not_fatal(self):
        result = self.run_sync([ent("TimsParser", "2027-07-01", customer="_test-entsync-nobody")])
        self.assertTrue(result["ok"])
        self.assertEqual(result["created"], 0)

    def test_running_twice_is_idempotent(self):
        rows = [ent("TimsParser", "2027-07-01")]
        first = self.run_sync(rows)
        second = self.run_sync(rows)
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 0)


class TestAutoCreateProducts(_Base):
    """Create a product for an unmapped item code, rather than making somebody
    hand-map every SKU.

    Safe here because the source is already curated: only items on Sales Orders
    carrying an Auto Repeat are considered, which is a support-contract SKU list
    rather than the whole item master.

    The product is named after the ITEM CODE. Sales Order Item.item_name is
    denormalised onto the order line, so renaming or merging an Item in ERPNext
    leaves historical lines carrying the old name — the code is the only stable
    identifier there is.
    """

    def enable_auto(self, on=True):
        frappe.db.set_single_value(erpnext_sync.DOCTYPE, "auto_create_products", 1 if on else 0)
        frappe.db.commit()
        self.addCleanup(
            lambda: frappe.db.set_single_value(erpnext_sync.DOCTYPE, "auto_create_products", 0)
        )

    def test_product_is_named_after_the_item_code(self):
        self.enable_auto()
        row = ent(PREFIX + "NewSKU", "2027-07-01")
        row["item_name"] = PREFIX + "Some Marketing Name"

        result = self.run_sync([row])

        self.assertEqual(result["products_created"], 1)
        self.assertTrue(frappe.db.exists("HD Product", PREFIX + "NewSKU"))
        self.assertFalse(frappe.db.exists("HD Product", PREFIX + "Some Marketing Name"))
        self.assertEqual(result["created"], 1, "and the entitlement lands in the same run")

    def test_stale_item_names_cannot_fork_the_catalogue(self):
        """The FrappeCloud case: one code, several historical names on old order
        lines. Naming by code collapses them to one product."""
        self.enable_auto()
        a = ent(PREFIX + "DualName", "2025-07-01", so="SO-A"); a["item_name"] = PREFIX + "Old Name"
        b = ent(PREFIX + "DualName", "2027-07-01", so="SO-B"); b["item_name"] = PREFIX + "New Name"

        result = self.run_sync([a, b])

        self.assertEqual(result["products_created"], 1)
        mapped = frappe.get_all(
            "HD Product Erpnext Item", filters={"item_code": PREFIX + "DualName"}, pluck="parent"
        )
        self.assertEqual(len(set(mapped)), 1)

    def test_adopts_an_existing_product_of_that_code_instead_of_duplicating(self):
        """Somebody already created the product by hand; just map the code onto
        it rather than forking a curated catalogue."""
        self.enable_auto()
        existing = frappe.get_doc({
            "doctype": "HD Product", "product_name": PREFIX + "AdoptMe"
        }).insert(ignore_permissions=True).name
        frappe.db.commit()

        result = self.run_sync([ent(PREFIX + "AdoptMe", "2027-07-01")])

        self.assertEqual(result["products_created"], 0)
        self.assertEqual(
            frappe.get_all(
                "HD Product Erpnext Item", filters={"item_code": PREFIX + "AdoptMe"}, pluck="parent"
            ),
            [existing],
        )

    def test_disabled_by_setting_leaves_the_item_unmapped(self):
        self.enable_auto(False)
        result = self.run_sync([ent(PREFIX + "StillUnmapped", "2027-07-01")])

        self.assertEqual(result["products_created"], 0)
        self.assertEqual(result["unmapped"], 1)
        self.assertEqual(result["created"], 0)
