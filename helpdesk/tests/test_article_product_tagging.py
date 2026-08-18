"""Agents can tag a knowledge-base article with the products it covers.

Without this the `products` field was only reachable from /app, one article at
a time, so bot product scoping stayed switched off in practice.

Tagging semantics come from the entitlement design: an article with NO tags is
generic and answers for every product. Clearing all tags is therefore a
meaningful action, not a no-op, and has to be supported.
"""

import unittest

import frappe

from helpdesk.api.knowledge_base import get_article, set_article_products

PREFIX = "_test-artprodapi-"


def cleanup():
    for name in frappe.get_all(
        "HD Article", filters={"title": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Article", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "User", filters={"email": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("User", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class TestArticleProductTagging(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        category = frappe.get_all("HD Article Category", limit=1, pluck="name")
        if not category:
            self.skipTest("no HD Article Category on this site")
        self.category = category[0]
        self.products = []
        for suffix in ("alpha", "beta"):
            self.products.append(
                frappe.get_doc(
                    {"doctype": "HD Product", "product_name": PREFIX + suffix}
                ).insert(ignore_permissions=True).name
            )
        self.article = frappe.get_doc({
            "doctype": "HD Article",
            "title": PREFIX + "doc",
            "category": self.category,
            "content": "body",
            "status": "Published",
        }).insert(ignore_permissions=True).name
        frappe.db.commit()

    def tearDown(self):
        frappe.set_user("Administrator")
        cleanup()

    def test_untagged_article_reports_no_products(self):
        self.assertEqual(get_article(self.article)["products"], [])

    def test_products_can_be_set_and_read_back(self):
        set_article_products(self.article, self.products)
        frappe.db.commit()
        self.assertEqual(sorted(get_article(self.article)["products"]), sorted(self.products))

    def test_setting_products_replaces_rather_than_appends(self):
        set_article_products(self.article, self.products)
        set_article_products(self.article, [self.products[0]])
        frappe.db.commit()
        self.assertEqual(get_article(self.article)["products"], [self.products[0]])

    def test_tags_can_be_cleared_making_the_article_generic_again(self):
        """Clearing is meaningful: an untagged article answers for every
        product, so this is how an agent widens an over-narrowed article."""
        set_article_products(self.article, self.products)
        set_article_products(self.article, [])
        frappe.db.commit()
        self.assertEqual(get_article(self.article)["products"], [])

    def test_unknown_product_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            set_article_products(self.article, ["_test-artprodapi-does-not-exist"])

    def test_non_agent_cannot_tag(self):
        user = frappe.get_doc({
            "doctype": "User",
            "email": PREFIX + "outsider@example.com",
            "first_name": "Outsider",
            "send_welcome_email": 0,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        self.addCleanup(lambda: frappe.set_user("Administrator"))

        frappe.set_user(user.name)
        self.assertRaises(
            frappe.PermissionError, set_article_products, self.article, self.products
        )
