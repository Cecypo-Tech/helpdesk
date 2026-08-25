"""`helpdesk.api.article.search` runs on the SQLite index, not RediSearch.

This endpoint feeds `SearchArticles.vue`, which renders under
`v-if="isCustomerPortal"` on the new-ticket form. So it is a CUSTOMER-facing
surface, and the internal-article gate on it is load-bearing: a leak here offers
internal runbooks to customers while they file a ticket.

The old implementation called `helpdesk.search.search`, which needs the `FT.*`
commands from the RediSearch module. Without the module it raised
`unknown command 'FT.SEARCH'` -- and the widget rendered nothing at all, because
its "No answers found" state only appears on an empty *successful* response. A
hard failure was indistinguishable from no matches.
"""

import unittest
from unittest.mock import patch

import frappe

from helpdesk.api.article import NUM_RESULTS, sanitize_query, search
from helpdesk.search_sqlite import HelpdeskSearch, strip_markdown

# Deliberately alphanumeric, with no hyphen or underscore. The index tokenizer
# is `unicode61 ... tokenchars '-_'`, so it treats those as WORD characters and
# indexes "_test-artapi-public" as a single token -- while `sanitize_query`
# strips them and splits the query into separate words. Fixtures named that way
# can never be found, which looks like a broken endpoint rather than a broken
# fixture.
PREFIX = "zzqqxart"
MARKER = "zzqqxmarker"


def cleanup():
    for name in frappe.get_all(
        "HD Article", filters={"title": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Article", name, force=True, ignore_permissions=True)
    frappe.db.commit()


def make_article(title, status="Published", internal=0):
    doc = frappe.get_doc(
        {
            "doctype": "HD Article",
            "title": f"{PREFIX} {title}",
            "content": f"<p>{MARKER} {title} body text</p>",
            "status": status,
            "internal": internal,
            "author": "Administrator",
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()
    return doc


class TestArticleSearchApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup()
        cls.customer = "_test-artapi-customer@example.com"
        if not frappe.db.exists("User", cls.customer):
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": cls.customer,
                    "first_name": "Article Api Customer",
                    "user_type": "Website User",
                    "send_welcome_email": 0,
                }
            ).insert(ignore_permissions=True)
            frappe.db.commit()

        cls.search = HelpdeskSearch()
        cls.public = make_article("publicanswer")
        cls.internal = make_article("internalanswer", internal=1)
        cls.draft = make_article("draftanswer", status="Draft")

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        cleanup()
        frappe.delete_doc("User", cls.customer, force=True, ignore_permissions=True)
        frappe.db.commit()

    def setUp(self):
        if not self.search.index_exists():
            self.skipTest("search index not built on this site")
        for doc in (self.public, self.internal):
            self.search.index_doc("HD Article", doc.name)

    def tearDown(self):
        frappe.set_user("Administrator")

    # --- the regression that motivated the change -------------------------

    def test_search_does_not_touch_redisearch(self):
        """The point of the change. If anything reintroduces the RediSearch path,
        this fails on any bench without the module -- which is most of them."""
        with patch("helpdesk.search.HelpdeskSearch") as legacy:
            search(f"{PREFIX} publicanswer")
        legacy.assert_not_called()

    def test_search_returns_results_for_a_customer(self):
        frappe.set_user(self.customer)
        names = [r["name"] for r in search(f"{PREFIX} publicanswer")]
        self.assertIn(self.public.name, names)

    # --- permissions ------------------------------------------------------

    def test_internal_article_is_not_offered_to_a_customer(self):
        frappe.set_user(self.customer)
        names = [r["name"] for r in search(f"{PREFIX} internalanswer")]
        self.assertNotIn(self.internal.name, names)

    def test_internal_article_is_offered_to_an_agent(self):
        frappe.set_user("Administrator")
        names = [r["name"] for r in search(f"{PREFIX} internalanswer")]
        self.assertIn(self.internal.name, names)

    def test_no_internal_article_survives_a_shared_marker_query(self):
        """Both fixtures share MARKER, so a query on it would return the internal
        one too if the gate were missing. Asserting on a query that CAN match both
        is what makes this a real test."""
        frappe.set_user(self.customer)
        names = [r["name"] for r in search(MARKER)]
        self.assertIn(self.public.name, names)
        self.assertNotIn(self.internal.name, names)

    def test_draft_article_is_never_offered(self):
        """A Draft is unpublished and possibly unfinished. `INDEXABLE_DOCTYPES`
        declares a `status: Published` filter, but frappe's `update_doc_index`
        doc_event ignores config filters and indexes on save regardless -- so the
        declaration alone did NOT keep drafts out."""
        frappe.set_user("Administrator")
        self.search.index_doc("HD Article", self.draft.name)
        names = [r["name"] for r in search(f"{PREFIX} draftanswer")]
        self.assertNotIn(self.draft.name, names)

    def test_unpublishing_evicts_an_already_indexed_article(self):
        """The dangerous direction. Publishing indexes; unpublishing fires the
        same doc_event, and the base implementation does nothing when
        prepare_document declines -- which would leave the row from when it was
        published, keeping a withdrawn article searchable."""
        frappe.set_user("Administrator")
        doc = make_article("retractable")
        self.search.index_doc("HD Article", doc.name)
        self.assertIn(
            doc.name, [r["name"] for r in search(f"{PREFIX} retractable")]
        )

        doc.status = "Archived"
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        self.search.index_doc("HD Article", doc.name)

        self.assertNotIn(
            doc.name, [r["name"] for r in search(f"{PREFIX} retractable")]
        )

    # --- response shape ---------------------------------------------------

    def test_result_shape_matches_what_the_widget_reads(self):
        """SearchArticles.vue reads id, name, subject, headings and description."""
        results = search(f"{PREFIX} publicanswer")
        self.assertTrue(results)
        for key in ("id", "name", "subject", "headings", "description"):
            self.assertIn(key, results[0])

    def test_result_carries_score_for_the_analysis_report(self):
        """Ticket Search Analysis sums item["score"]; a missing key is a KeyError
        in a report nobody runs often enough to notice quickly."""
        results = search(f"{PREFIX} publicanswer")
        self.assertIn("score", results[0])

    def test_name_has_no_heading_anchor(self):
        """Whole-article results now. The widget builds its route param straight
        from `name`, so a stray '#<heading>' would produce a broken link."""
        results = search(f"{PREFIX} publicanswer")
        self.assertNotIn("#", results[0]["name"])

    def test_only_articles_come_back(self):
        results = search(MARKER)
        for r in results:
            self.assertTrue(frappe.db.exists("HD Article", r["name"]))

    def test_results_are_capped(self):
        self.assertLessEqual(len(search(MARKER)), NUM_RESULTS)

    # --- input handling ---------------------------------------------------

    def test_empty_query_returns_empty_list_rather_than_raising(self):
        """The widget gates at 3 characters, but the endpoint is whitelisted and
        callable directly. It must not raise on punctuation-only input, which
        sanitize_query reduces to ''."""
        self.assertEqual(search("!!!"), [])
        self.assertEqual(search("   "), [])

    def test_sanitize_query_strips_punctuation_and_collapses_space(self):
        self.assertEqual(sanitize_query("  How's   the  BACKUP? "), "how s the backup")

    # --- readability of what the widget shows -----------------------------

    def test_subject_carries_no_highlight_markup(self):
        """The widget interpolates the title as TEXT, so any <mark> from
        SQLite's highlight() renders literally on screen as "<mark>Tremol</mark>"."""
        results = search(f"{PREFIX} publicanswer")
        self.assertTrue(results)
        self.assertNotIn("<mark>", results[0]["subject"])
        self.assertNotIn("</mark>", results[0]["subject"])

    def test_snippet_is_not_raw_markdown(self):
        """Article bodies arrive as markdown inside HTML. Unstripped, snippets
        read like "## Software Reset ... ![](/api/attachments...) | Brand |"."""
        results = search(MARKER)
        self.assertTrue(results)
        for r in results:
            desc = r["description"] or ""
            for noise in ("![](", "## ", "|---", "**"):
                self.assertNotIn(noise, desc)


class TestStripMarkdown(unittest.TestCase):
    """Cases taken from the real article bodies on this site."""

    def test_images_are_removed(self):
        out = strip_markdown('![](/api/attachments.redirect?id=87227966 "=380x74") Press')
        self.assertNotIn("![](", out)
        self.assertIn("Press", out)

    def test_headings_lose_their_hashes(self):
        self.assertEqual(strip_markdown("## Software Reset steps"), "Software Reset steps")

    def test_links_keep_their_text(self):
        self.assertEqual(
            strip_markdown("See [the manual](https://x.example/doc) now"),
            "See the manual now",
        )

    def test_table_rules_and_pipes_go(self):
        out = strip_markdown("| Brand | IPs | |-------|-----| | Tremol | 196.207.27.42 |")
        self.assertNotIn("|", out)
        self.assertNotIn("---", out)
        self.assertIn("Tremol", out)

    def test_directives_are_removed(self):
        self.assertNotIn(":::", strip_markdown(":::warning Network settings reset :::"))

    def test_emphasis_is_unwrapped(self):
        self.assertEqual(strip_markdown("# **POWERING ON**"), "POWERING ON")

    def test_hyphenated_words_survive(self):
        """The table-rule pattern matches runs of 3+, so ordinary hyphenation and
        short ranges must come through untouched."""
        self.assertEqual(
            strip_markdown("tab-software reset on the FT-100MX"),
            "tab-software reset on the FT-100MX",
        )

    def test_literal_escape_sequences_are_treated_as_whitespace(self):
        """The Outline import embeds literal backslash-n in article bodies. They
        survive the base indexer's whitespace collapse, and they read as a word
        character to every other pattern here -- which is how "excel\\n#### MYSQL"
        kept its heading marker."""
        out = strip_markdown("to excel" + chr(92) + "n#### MYSQL COUNT")
        self.assertNotIn(chr(92) + "n", out)
        self.assertNotIn("#", out)
        self.assertEqual(out, "to excel MYSQL COUNT")

    def test_image_with_a_rewritten_url_is_removed(self):
        """The base indexer rewrites bare URLs to "[link]" before this runs, which
        eats an image's closing paren and leaves "![]([link]" behind."""
        out = strip_markdown("renew --dry-run ![]([link] Ctrl X")
        self.assertNotIn("![](", out)

    def test_empty_input_is_safe(self):
        self.assertEqual(strip_markdown(""), "")
        self.assertEqual(strip_markdown(None), "")
