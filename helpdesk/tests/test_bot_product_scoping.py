"""The bot answers from articles covering the customer's product.

Articles tagged for other products are dropped; untagged articles are generic
and always survive. The global allowlist in Helpdesk Bot Settings is unchanged
and still bounds everything.
"""

import unittest
from unittest.mock import patch

from helpdesk.integrations import bot


class TestCombinedSearchPassesProduct(unittest.TestCase):
    def test_product_is_threaded_into_the_search(self):
        with patch.object(bot, "_get_allowed_categories", return_value=["a"]), \
             patch.object(bot, "_search_kb", return_value=[]) as search_kb:
            bot._combined_kb_search("query", 3, product="eTIMS")

        self.assertEqual(search_kb.call_args.kwargs["allowed_categories"], ["a"])
        self.assertEqual(search_kb.call_args.kwargs["product"], "eTIMS")

    def test_without_a_product_behaviour_is_unchanged(self):
        """Existing callers pass no product; the allowlist must still apply."""
        with patch.object(bot, "_get_allowed_categories", return_value=["a"]), \
             patch.object(bot, "_search_kb", return_value=[]) as search_kb:
            bot._combined_kb_search("query", 3)

        self.assertEqual(search_kb.call_args.kwargs["allowed_categories"], ["a"])
        self.assertIsNone(search_kb.call_args.kwargs["product"])

    def test_product_alone_still_filters_outline_results(self):
        """Outline filtering used to run only when categories were configured.
        A product is a restriction too, so it must trigger it."""
        with patch.object(bot, "_get_allowed_categories", return_value=[]), \
             patch.object(bot, "_search_kb", return_value=[]), \
             patch.object(bot, "_filter_outline_by_category", return_value=[]) as filt, \
             patch(
                 "helpdesk.integrations.outline.search",
                 return_value=[{"outline_doc_id": "x", "title": "t", "content": "c"}],
             ):
            bot._combined_kb_search("query", 3, product="eTIMS")

        filt.assert_called_once()
        self.assertEqual(filt.call_args.kwargs["product"], "eTIMS")


class TestLikeFallbackFiltersByProduct(unittest.TestCase):
    """The LIKE fallback runs when semantic search is unavailable. It truncates
    with SQL LIMIT, so it must overfetch and filter before cutting to `limit`."""

    def test_overfetches_then_filters_then_truncates(self):
        rows = [{"name": f"A{i}"} for i in range(8)]

        with patch("helpdesk.integrations.embeddings.search_articles", return_value=[]), \
             patch("frappe.db.sql", return_value=rows) as sql, \
             patch(
                 "helpdesk.entitlement.filter_articles_for_product",
                 side_effect=lambda r, p: r[:5],
             ) as filt:
            out = bot._search_kb("q", 3, allowed_categories=None, product="eTIMS")

        self.assertEqual(sql.call_args[0][1]["limit"], 12, "must overfetch limit * 4")
        filt.assert_called_once()
        self.assertEqual(len(out), 3, "must truncate to limit after filtering")

    def test_no_product_does_not_filter_or_overfetch(self):
        rows = [{"name": "A"}]
        with patch("helpdesk.integrations.embeddings.search_articles", return_value=[]), \
             patch("frappe.db.sql", return_value=rows) as sql, \
             patch("helpdesk.entitlement.filter_articles_for_product") as filt:
            bot._search_kb("q", 3, allowed_categories=None, product=None)

        self.assertEqual(sql.call_args[0][1]["limit"], 3)
        filt.assert_not_called()

    def test_product_is_passed_to_semantic_search(self):
        with patch(
            "helpdesk.integrations.embeddings.search_articles", return_value=[{"name": "A"}]
        ) as semantic:
            bot._search_kb("q", 3, allowed_categories=["a"], product="eTIMS")

        self.assertEqual(semantic.call_args.kwargs["product"], "eTIMS")


class TestOutlineFilterByProduct(unittest.TestCase):
    def test_unmappable_outline_results_are_still_dropped(self):
        """Pre-existing conservative behaviour: when a restriction applies, a
        document that cannot be resolved to a local article never reaches the
        LLM. A product restriction counts as one."""
        results = [{"outline_doc_id": "unknown"}]
        with patch("frappe.db.get_all", return_value=[]):
            out = bot._filter_outline_by_category(results, [], product="eTIMS")
        self.assertEqual(out, [])

    def test_product_filter_is_applied_to_resolved_articles(self):
        results = [{"outline_doc_id": "d1"}, {"outline_doc_id": "d2"}]
        rows = [
            {"name": "A1", "outline_doc_id": "d1"},
            {"name": "A2", "outline_doc_id": "d2"},
        ]
        with patch("frappe.db.get_all", return_value=rows), \
             patch(
                 "helpdesk.entitlement.filter_articles_for_product",
                 return_value=[{"name": "A1", "outline_doc_id": "d1"}],
             ):
            out = bot._filter_outline_by_category(results, [], product="eTIMS")

        self.assertEqual([r["outline_doc_id"] for r in out], ["d1"])
