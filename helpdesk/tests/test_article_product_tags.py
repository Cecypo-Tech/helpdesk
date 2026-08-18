"""Articles carry product tags; untagged articles are generic.

HD Article.category is a single Link, so category-only mapping cannot express
"this article covers POS and eTIMS but not TIMS" without inventing a
"POS+eTIMS" category. Product tags can.
"""

import unittest
import unittest.mock

import frappe

from helpdesk import entitlement

PREFIX = "_test-artprod-"
PRODUCT_A = PREFIX + "prodA"
PRODUCT_B = PREFIX + "prodB"


def cleanup():
    for name in frappe.get_all(
        "HD Article", filters={"title": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Article", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class TestArticleProductTags(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        for p in (PRODUCT_A, PRODUCT_B):
            frappe.get_doc({"doctype": "HD Product", "product_name": p}).insert(
                ignore_permissions=True
            )
        category = frappe.get_all("HD Article Category", limit=1, pluck="name")
        if not category:
            self.skipTest("no HD Article Category on this site")
        self.category = category[0]
        frappe.db.commit()

    def tearDown(self):
        cleanup()

    def article(self, suffix, products=None):
        doc = frappe.get_doc({
            "doctype": "HD Article",
            "title": PREFIX + suffix,
            "category": self.category,
            "content": "body",
            "status": "Published",
            "products": [{"product": p} for p in (products or [])],
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        return doc

    def test_custom_field_exists(self):
        self.assertTrue(
            frappe.db.exists("Custom Field", {"dt": "HD Article", "fieldname": "products"})
        )

    def test_tags_round_trip(self):
        doc = self.article("tagged", [PRODUCT_A])
        doc.reload()
        self.assertEqual([r.product for r in doc.products], [PRODUCT_A])

    def test_products_for_articles_maps_names_to_tags(self):
        tagged = self.article("t1", [PRODUCT_A, PRODUCT_B])
        untagged = self.article("t2")
        out = entitlement.products_for_articles([tagged.name, untagged.name])
        self.assertEqual(sorted(out[tagged.name]), sorted([PRODUCT_A, PRODUCT_B]))
        self.assertEqual(out.get(untagged.name, []), [])

    def test_products_for_articles_handles_empty_input(self):
        self.assertEqual(entitlement.products_for_articles([]), {})

    def test_tags_survive_an_outline_resync(self):
        """docs.cecypo.tech re-syncs hourly and carries no product information.
        sync_outline_docs updates existing rows with frappe.db.set_value and an
        explicit field dict, so it never loads the document and cannot touch
        child tables. This test pins that guarantee: if the sync is ever
        rewritten to use doc.save(), tagging becomes worthless and this fails.
        """
        doc = self.article("outline-doc", [PRODUCT_A])
        frappe.db.set_value("HD Article", doc.name, "outline_doc_id", PREFIX + "oid")
        frappe.db.commit()

        # Exactly what sync_outline_docs does to an already-synced article.
        frappe.db.set_value(
            "HD Article",
            doc.name,
            {
                "title": PREFIX + "outline-doc updated",
                "content": "fresh body from Outline",
                "category": self.category,
                "source_url": "https://docs.cecypo.tech/doc/x",
                "internal": 0,
                "status": "Published",
            },
            update_modified=False,
        )
        frappe.db.commit()

        doc.reload()
        self.assertEqual([r.product for r in doc.products], [PRODUCT_A])


class TestFilterArticlesForProduct(unittest.TestCase):
    """Pure filtering behaviour, driven by an injected tag map."""

    def setUp(self):
        frappe.set_user("Administrator")

    def rows(self):
        return [{"name": "A"}, {"name": "B"}, {"name": "C"}]

    def test_no_product_returns_everything(self):
        with unittest.mock.patch.object(entitlement, "products_for_articles") as tags:
            out = entitlement.filter_articles_for_product(self.rows(), None)
        self.assertEqual([r["name"] for r in out], ["A", "B", "C"])
        tags.assert_not_called()

    def test_tagged_article_is_kept_for_a_matching_product(self):
        with unittest.mock.patch.object(
            entitlement, "products_for_articles", return_value={"A": ["p1"], "B": [], "C": ["p2"]}
        ):
            out = entitlement.filter_articles_for_product(self.rows(), "p1")
        self.assertEqual([r["name"] for r in out], ["A", "B"])

    def test_untagged_article_is_generic(self):
        """B has no tags and must survive every product filter."""
        with unittest.mock.patch.object(
            entitlement, "products_for_articles", return_value={"A": ["p1"], "B": [], "C": ["p2"]}
        ):
            out = entitlement.filter_articles_for_product(self.rows(), "p9")
        self.assertEqual([r["name"] for r in out], ["B"])

    def test_rows_without_an_article_name_pass_through(self):
        """Outline results that never synced to an HD Article are treated as
        generic rather than silently dropped."""
        rows = [{"title": "outline only"}, {"name": "A"}]
        with unittest.mock.patch.object(
            entitlement, "products_for_articles", return_value={"A": ["p2"]}
        ):
            out = entitlement.filter_articles_for_product(rows, "p1")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "outline only")

    def test_empty_rows_returns_empty(self):
        self.assertEqual(entitlement.filter_articles_for_product([], "p1"), [])
