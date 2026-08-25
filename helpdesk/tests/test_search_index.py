"""Search indexing: the RediSearch capability guard, and HD Article in the SQLite index.

Two independent problems, one file, because they are two halves of the same
migration.

1. `helpdesk.search` (RediSearch) is legacy but still wired into `after_migrate`
   and `scheduler_events["all"]`. On a Redis without the query engine every tick
   raises `unknown command 'FT.CREATE'` -- forever, because `Search.index_exists()`
   suppresses ResponseError and so always reports "no index". The guard makes the
   two automatic entry points no-op instead, and does it without caching so a
   bench that later gains the module starts indexing again on its own.

2. `helpdesk.search_sqlite` is the current backend but never indexed HD Article,
   so articles were reachable only through the broken RediSearch path.

The article permission encoding is the subtle part and is asserted here directly:
articles carry a sentinel `reference_ticket` (0 public, -1 internal) because the
base class ANDs a single `reference_ticket IN (...)` clause onto every query and
offers no way to express "OR doctype = 'HD Article'".
"""

import unittest
from unittest.mock import patch

import frappe
from redis.exceptions import ResponseError

from helpdesk.search import (
    build_index_if_not_exists,
    build_index_in_background,
    is_redisearch_available,
)
from helpdesk.utils import is_agent
from helpdesk.search_sqlite import (
    ARTICLE_INTERNAL_KEY,
    ARTICLE_PUBLIC_KEY,
    HelpdeskSearch,
)

PREFIX = "_test-searchidx-"


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
            "title": PREFIX + title,
            "content": f"<p>{PREFIX}{title} body zzqqx searchable marker</p>",
            "status": status,
            "internal": internal,
            "author": "Administrator",
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()
    return doc


class TestRediSearchGuard(unittest.TestCase):
    """The 226-errors-in-one-log problem: a scheduled job that can only fail."""

    def test_unknown_command_means_unavailable(self):
        with patch.object(
            frappe.cache(),
            "execute_command",
            side_effect=ResponseError("unknown command 'FT._LIST'"),
        ):
            self.assertFalse(is_redisearch_available())

    def test_working_command_means_available(self):
        with patch.object(frappe.cache(), "execute_command", return_value=[]):
            self.assertTrue(is_redisearch_available())

    def test_other_response_error_does_not_claim_unavailable(self):
        """A ResponseError that is not "unknown command" is an index problem, not a
        capability answer. Reporting False there would silently disable indexing on
        a Redis that actually supports it."""
        with patch.object(
            frappe.cache(),
            "execute_command",
            side_effect=ResponseError("Index already exists"),
        ):
            self.assertTrue(is_redisearch_available())

    def test_connection_error_does_not_claim_unavailable(self):
        with patch.object(
            frappe.cache(), "execute_command", side_effect=OSError("connection reset")
        ):
            self.assertTrue(is_redisearch_available())

    def test_scheduled_entry_point_is_noop_without_redisearch(self):
        with patch("helpdesk.search.is_redisearch_available", return_value=False):
            with patch("helpdesk.search.build_index") as build:
                build_index_if_not_exists()
        build.assert_not_called()

    def test_background_entry_point_is_noop_without_redisearch(self):
        with patch("helpdesk.search.is_redisearch_available", return_value=False):
            with patch("frappe.enqueue") as enqueue:
                build_index_in_background()
        enqueue.assert_not_called()

    def test_scheduled_entry_point_still_runs_when_available(self):
        """The guard must not disable indexing on a bench that does have the module."""
        with patch("helpdesk.search.is_redisearch_available", return_value=True):
            with patch("helpdesk.search.HelpdeskSearch") as search_cls:
                search_cls.return_value.index_exists.return_value = False
                with patch("helpdesk.search.build_index") as build:
                    build_index_if_not_exists()
        build.assert_called_once()


class TestArticleIndexing(unittest.TestCase):
    """HD Article was absent from INDEXABLE_DOCTYPES, so nothing indexed it."""

    @classmethod
    def setUpClass(cls):
        cleanup()
        cls.search = HelpdeskSearch()

    @classmethod
    def tearDownClass(cls):
        cleanup()

    def setUp(self):
        if not self.search.index_exists():
            self.skipTest("search index not built on this site")

    def test_hd_article_is_indexable(self):
        self.assertIn("HD Article", HelpdeskSearch.INDEXABLE_DOCTYPES)

    def test_only_published_articles_are_indexed(self):
        config = HelpdeskSearch.INDEXABLE_DOCTYPES["HD Article"]
        self.assertEqual(config.get("filters"), {"status": "Published"})

    def test_public_article_gets_public_sentinel(self):
        doc = make_article("public-one")
        document = self.search.prepare_document(doc)
        self.assertEqual(document["reference_ticket"], ARTICLE_PUBLIC_KEY)

    def test_internal_article_gets_internal_sentinel(self):
        doc = make_article("internal-one", internal=1)
        document = self.search.prepare_document(doc)
        self.assertEqual(document["reference_ticket"], ARTICLE_INTERNAL_KEY)

    def test_sentinels_cannot_collide_with_a_real_ticket(self):
        """Ticket names are positive autoincrement ints, cast with int() in
        prepare_document. Both sentinels must sit outside that range."""
        self.assertLessEqual(ARTICLE_PUBLIC_KEY, 0)
        self.assertLess(ARTICLE_INTERNAL_KEY, ARTICLE_PUBLIC_KEY)

    def test_public_sentinel_is_always_accessible(self):
        self.assertIn(ARTICLE_PUBLIC_KEY, self.search._get_accessible_tickets())

    def test_published_article_is_searchable(self):
        doc = make_article("findable")
        self.search.index_doc("HD Article", doc.name)
        result = self.search.search(PREFIX + "findable")
        names = [r.get("name") for r in result["results"]]
        self.assertIn(doc.name, names)

    def test_internal_article_hidden_from_non_agents(self):
        doc = make_article("secret", internal=1)
        self.search.index_doc("HD Article", doc.name)

        with patch("helpdesk.search_sqlite.is_agent", return_value=False):
            result = self.search.search(PREFIX + "secret")
        names = [r.get("name") for r in result["results"]]
        self.assertNotIn(doc.name, names)

    def test_internal_article_visible_to_agents(self):
        doc = make_article("agentsonly", internal=1)
        self.search.index_doc("HD Article", doc.name)

        with patch("helpdesk.search_sqlite.is_agent", return_value=True):
            result = self.search.search(PREFIX + "agentsonly")
        names = [r.get("name") for r in result["results"]]
        self.assertIn(doc.name, names)

    def test_articles_survive_a_site_with_no_accessible_tickets(self):
        """The regression that made this worth asserting: an empty accessible-ticket
        list makes the base class emit `1=0`, which would drop articles too if they
        did not carry a sentinel of their own."""
        doc = make_article("noticketsite")
        self.search.index_doc("HD Article", doc.name)

        # A plain function, not a MagicMock: `get_scoring_pipeline` sweeps in any
        # attribute answering `hasattr(attr, "_is_scoring_function")`, and a mock
        # answers True to every hasattr, so it would be called as a scorer.
        with patch.object(
            HelpdeskSearch, "_get_accessible_ticket_names", new=lambda self: []
        ):
            result = self.search.search(PREFIX + "noticketsite")
        names = [r.get("name") for r in result["results"]]
        self.assertIn(doc.name, names)


class TestInternalArticleVisibility(unittest.TestCase):
    """Internal articles must never reach a customer.

    Separate from TestArticleIndexing, and deliberately NOT mocking `is_agent`.
    The property that matters is not "the non-agent branch filters correctly" --
    it is "a real customer session takes that branch". A mocked gate proves the
    former and would keep passing if the gate stopped being consulted at all.

    Exercised through `helpdesk.api.search.search`, the whitelisted endpoint, so
    the assertion covers what an end user can actually call. The UI only routes
    the search page for agents, but the endpoint is reachable by any logged-in
    user, so UI routing is not the control here.
    """

    @classmethod
    def setUpClass(cls):
        cleanup()
        cls.customer = "_test-searchidx-customer@example.com"
        if not frappe.db.exists("User", cls.customer):
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": cls.customer,
                    "first_name": "Search Index Customer",
                    "user_type": "Website User",
                    "send_welcome_email": 0,
                }
            ).insert(ignore_permissions=True)
            frappe.db.commit()

        cls.search = HelpdeskSearch()
        cls.public = make_article("visible-to-all")
        cls.internal = make_article("internal-only", internal=1)

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        cleanup()
        frappe.delete_doc("User", cls.customer, force=True, ignore_permissions=True)
        frappe.db.commit()

    def setUp(self):
        if not self.search.index_exists():
            self.skipTest("search index not built on this site")
        self.search.index_doc("HD Article", self.public.name)
        self.search.index_doc("HD Article", self.internal.name)

    def tearDown(self):
        frappe.set_user("Administrator")

    def _search_as(self, user, term):
        from helpdesk.api.search import search as api_search

        frappe.set_user(user)
        result = api_search(query=term, limit=100)
        return [r.get("name") for r in result["results"]]

    def test_customer_session_is_not_an_agent(self):
        """Guards the premise the rest of this class rests on."""
        frappe.set_user(self.customer)
        self.assertFalse(is_agent())

    def test_customer_cannot_find_internal_article(self):
        names = self._search_as(self.customer, PREFIX + "internal-only")
        self.assertNotIn(self.internal.name, names)

    def test_customer_can_still_find_public_article(self):
        """The gate must be a filter, not a blanket block on article search."""
        names = self._search_as(self.customer, PREFIX + "visible-to-all")
        self.assertIn(self.public.name, names)

    def test_agent_can_find_internal_article(self):
        names = self._search_as("Administrator", PREFIX + "internal-only")
        self.assertIn(self.internal.name, names)

    def test_internal_articles_absent_from_customer_filter_counts(self):
        """Counts are a side channel: a customer must not learn how many internal
        articles exist, even without seeing them."""
        from helpdesk.api.search import get_filter_options

        frappe.set_user("Administrator")
        agent_count = get_filter_options()["doctypes"].get("HD Article", 0)
        frappe.set_user(self.customer)
        customer_count = get_filter_options()["doctypes"].get("HD Article", 0)

        self.assertLess(customer_count, agent_count)

    def test_no_internal_article_leaks_across_a_broad_sweep(self):
        """Term-by-term sweep drawn from the internal articles' own titles -- the
        worst case for a leak, and the shape of the audit that verified this
        change against live data."""
        internal_names = set(
            frappe.get_all(
                "HD Article",
                filters={"status": "Published", "internal": 1},
                pluck="name",
            )
        )
        if not internal_names:
            self.skipTest("no internal articles on this site")

        terms = set()
        for title in frappe.get_all(
            "HD Article",
            filters={"status": "Published", "internal": 1},
            pluck="title",
        ):
            terms.update(w for w in (title or "").split() if len(w) > 3)

        leaked = set()
        for term in sorted(terms)[:25]:
            leaked.update(set(self._search_as(self.customer, term)) & internal_names)

        self.assertEqual(leaked, set(), f"internal articles leaked to a customer: {leaked}")
