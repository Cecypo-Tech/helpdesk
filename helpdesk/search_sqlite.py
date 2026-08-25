# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

import frappe
from frappe.search.sqlite_search import SQLiteSearch, SQLiteSearchIndexMissingError

from helpdesk.utils import is_agent

# Articles are not ticket-scoped, but the base class ANDs a single
# `reference_ticket IN (...)` clause onto every query and gives a subclass no way
# to express "OR doctype = 'HD Article'". So articles ride the same column with
# sentinel values, and `_get_accessible_tickets()` decides which of them the
# current user is allowed to see.
#
# HD Ticket names are positive autoincrement integers, cast with `int()` in
# `prepare_document`, so anything <= 0 is unreachable as a real ticket.
ARTICLE_PUBLIC_KEY = 0
ARTICLE_INTERNAL_KEY = -1


class HelpdeskSearchIndexMissingError(SQLiteSearchIndexMissingError):
    pass


class HelpdeskSearch(SQLiteSearch):
    INDEX_NAME = "helpdesk_search.db"

    def __init__(self, db_name=None, articles_only=False):
        """`articles_only` skips the accessible-ticket lookup.

        `_get_accessible_ticket_names()` runs `frappe.get_list("HD Ticket")` on
        every search. The article-suggestion widget on the new-ticket form fires
        one search per debounced keystroke and cannot match a ticket anyway, so
        paying for that list there is pure waste -- on a busy site it is the
        most expensive part of a request that only ever returns articles.
        """
        super().__init__(db_name)
        self.articles_only = articles_only

    INDEX_SCHEMA = {
        "metadata_fields": [
            "agent_group",
            "customer",
            "status",
            "priority",
            "owner",
            "reference_doctype",
            "reference_name",
            "reference_ticket",
        ],
        "tokenizer": "unicode61 remove_diacritics 2 tokenchars '-_'",
    }

    INDEXABLE_DOCTYPES = {
        "HD Ticket": {
            "fields": [
                "name",
                {"title": "subject"},
                {"content": "description"},
                "modified",
                "agent_group",
                "status",
                "priority",
                "raised_by",
                "owner",
            ],
        },
        "HD Ticket Comment": {
            "fields": [
                "name",
                "content",
                "modified",
                "reference_ticket",
                "commented_by",
                "owner",
            ],
        },
        "Communication": {
            "fields": [
                "name",
                "content",
                "modified",
                "reference_doctype",
                "reference_name",
                "sender",
                "owner",
            ],
            "filters": {"reference_doctype": "HD Ticket"},
        },
        "HD Article": {
            "fields": [
                "name",
                "title",
                "content",
                "modified",
                "author",
                # Selected purely so `prepare_document` can read it during a bulk
                # build -- `get_documents_paginated` SELECTs only declared fields,
                # and `internal` is not a schema metadata column.
                "internal",
            ],
            # Draft and Archived articles are not answers to anything, so they
            # never enter the index rather than being filtered out on read.
            "filters": {"status": "Published"},
        },
    }

    def get_search_filters(self):
        """Return permission filters based on accessible tickets."""
        accessible_tickets = self._get_accessible_tickets()
        return {"reference_ticket": accessible_tickets}

    def _get_accessible_tickets(self):
        """Ticket keys the current user may see, plus the article sentinels.

        The public sentinel is always present: an article that is indexed is
        readable. The internal one is added only for agents, which reproduces the
        visibility rule the RediSearch path applied on read.

        Appending sentinels also fixes a sharp edge in the base class -- an empty
        accessible-ticket list is compiled to `1=0`, so on a site with no visible
        tickets every search returned nothing at all, articles included.
        """
        keys = list(self._get_accessible_ticket_names())
        keys.append(ARTICLE_PUBLIC_KEY)
        if is_agent():
            keys.append(ARTICLE_INTERNAL_KEY)
        return keys

    def _get_accessible_ticket_names(self):
        """Get tickets accessible to current user based on helpdesk permissions."""
        if getattr(self, "articles_only", False):
            return []
        return frappe.get_list("HD Ticket", pluck="name")

    def _passes_index_filters(self, doc) -> bool:
        """Whether `doc` satisfies the `filters` declared for its doctype.

        `INDEXABLE_DOCTYPES[...]["filters"]` is applied by the bulk build, which
        reads through `get_documents_paginated`. It is NOT applied on save:
        frappe's `update_doc_index` doc_event calls `index_doc` directly and
        never looks at the config filters. So without this check a Draft article
        is indexed the moment it is saved, and the customer-facing suggestion
        widget offers it -- an unpublished, possibly unfinished article.
        """
        config = self.doc_configs.get(doc.doctype) or {}
        for field, expected in (config.get("filters") or {}).items():
            if getattr(doc, field, None) != expected:
                return False
        return True

    def index_doc(self, doctype, docname):
        """Index one document, or evict it if it no longer qualifies.

        The eviction half matters more than it looks. Publishing an article
        indexes it; unpublishing it fires the same doc_event, and the base
        implementation simply does nothing when `prepare_document` declines --
        leaving the row from when it *was* published. A withdrawn article would
        stay searchable, and keep being suggested to customers.
        """
        doc = frappe.get_doc(doctype, docname)
        if not self._passes_index_filters(doc):
            self.remove_doc(doctype, docname)
            return
        super().index_doc(doctype, docname)

    def prepare_document(self, doc):
        """Prepare a document for indexing with helpdesk-specific handling."""
        if not self._passes_index_filters(doc):
            return None

        document = super().prepare_document(doc)
        if not document:
            return None

        if (
            doc.doctype == "HD Ticket Comment"
            and doc.reference_ticket
            and type(doc.reference_ticket) is str
        ):
            document["reference_ticket"] = int(doc.reference_ticket)

        if doc.doctype == "Communication":
            # For communications, ensure reference fields are set for ticket doctype
            document["reference_doctype"] = doc.reference_doctype
            if (
                doc.reference_doctype == "HD Ticket"
                and doc.reference_name
                and type(doc.reference_name) is str
            ):
                document["reference_name"] = int(doc.reference_name)

        if doc.doctype == "HD Ticket":
            document["reference_ticket"] = int(doc.name)

        if doc.doctype == "HD Article":
            document["reference_ticket"] = (
                ARTICLE_INTERNAL_KEY
                if frappe.utils.cint(getattr(doc, "internal", 0))
                else ARTICLE_PUBLIC_KEY
            )
            # `owner` is not in the declared field list, so a bulk build never
            # SELECTs it; fall back explicitly rather than relying on _dict
            # returning None for a missing key.
            document["owner"] = getattr(doc, "author", None) or getattr(
                doc, "owner", None
            )

        # Map commented_by to owner for HD Ticket Comment
        if doc.doctype == "HD Ticket Comment":
            document["owner"] = doc.commented_by

        # Map sender to owner for Communication
        if doc.doctype == "Communication":
            document["owner"] = doc.sender

        return document

    def get_filter_options(self):
        """Get available filter options for search interface."""
        if not self.index_exists():
            return {
                "teams": {},
                "statuses": {},
                "priorities": {},
                "customers": {},
                "doctypes": {},
            }

        # Get accessible tickets first
        accessible_tickets = self._get_accessible_tickets()
        if not accessible_tickets:
            return {
                "teams": {},
                "statuses": {},
                "priorities": {},
                "customers": {},
                "doctypes": {},
            }

        # Query the search index for available options
        sql = """
			SELECT
				agent_group,
				status,
				priority,
				customer,
				doctype,
				COUNT(*) as count
			FROM search_fts
			WHERE (name IN ({placeholders}) OR reference_name IN ({placeholders}) OR reference_ticket IN ({placeholders}))
			GROUP BY agent_group, status, priority, customer, doctype
		""".format(
            placeholders=",".join(["?" for _ in accessible_tickets])
        )

        params = accessible_tickets * 3
        results = self.sql(sql, params, read_only=True)

        # Aggregate the results
        teams = {}
        statuses = {}
        priorities = {}
        customers = {}
        doctypes = {}

        for row in results:
            if row["agent_group"]:
                teams[row["agent_group"]] = (
                    teams.get(row["agent_group"], 0) + row["count"]
                )
            if row["status"]:
                statuses[row["status"]] = statuses.get(row["status"], 0) + row["count"]
            if row["priority"]:
                priorities[row["priority"]] = (
                    priorities.get(row["priority"], 0) + row["count"]
                )
            if row["customer"]:
                customers[row["customer"]] = (
                    customers.get(row["customer"], 0) + row["count"]
                )
            if row["doctype"]:
                doctypes[row["doctype"]] = (
                    doctypes.get(row["doctype"], 0) + row["count"]
                )

        return {
            "teams": teams,
            "statuses": statuses,
            "priorities": priorities,
            "customers": customers,
            "doctypes": doctypes,
        }


def build_index():
    """Build search index - can be called from console."""
    search = HelpdeskSearch()
    search.build_index()
