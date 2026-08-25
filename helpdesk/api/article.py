import re

import frappe

from helpdesk.search_sqlite import HelpdeskSearch

NUM_RESULTS = 5


def sanitize_query(query: str) -> str:
    q = query.strip().lower()
    q = re.sub(r"[^a-z0-9\s]", " ", q)
    # Collapse multiple spaces into one
    q = re.sub(r"\s+", " ", q)
    return q.strip()


@frappe.whitelist()
def search(query: str) -> list:
    """Article suggestions for the new-ticket form.

    Runs on the SQLite full-text index, not the legacy RediSearch one. The old
    path called `helpdesk.search.search`, which needs the `FT.*` commands the
    RediSearch module provides. Plain Redis does not have them -- so on any bench
    or host without the module this endpoint raised `unknown command 'FT.SEARCH'`
    and the widget silently rendered nothing at all (it only shows its "No
    answers found" state on an empty *successful* response). Moving to SQLite
    removes the dependency instead of requiring the module everywhere.

    Two behaviour changes came with the move, both chosen deliberately:

    1. Results are whole articles. The RediSearch index stored one document per
       heading section, so `name` was `<article>#<heading>` and suggestions
       deep-linked into the article. The SQLite index stores whole articles, so
       `name` is the article and `headings` is empty.

    2. No query-expansion cascade. The old endpoint retried through noun phrases
       and nouns, in AND then OR, using textblob/NLTK -- compensation for
       RediSearch's strict AND matching, which also put an NLTK corpus download
       on this customer-facing path. Frappe's SQLite search does its own query
       preparation and spelling correction, so one query replaces up to five.

    Internal articles are filtered by `HelpdeskSearch._get_accessible_tickets()`,
    which only admits the internal sentinel for agents. This endpoint is reached
    from the CUSTOMER portal (`SearchArticles.vue` renders under
    `v-if="isCustomerPortal"`), so that gate is the thing standing between a
    customer and an internal runbook. Do not bypass it here.
    """
    query = sanitize_query(query)
    if not query:
        return []

    search = HelpdeskSearch(articles_only=True)
    if not search.index_exists():
        return []

    result = search.search(query, filters={"doctype": "HD Article"})

    items = []
    for row in result.get("results", []):
        if row.get("doctype") != "HD Article":
            continue
        items.append(
            {
                "id": f"HD Article:{row.get('name')}",
                "name": row.get("name"),
                "subject": row.get("title"),
                "headings": "",
                "description": row.get("content"),
                # Not used by the widget, but the Ticket Search Analysis report
                # sums `score` across results. Dropping the key would turn that
                # report into a KeyError.
                "score": row.get("score", 0),
            }
        )
        if len(items) == NUM_RESULTS:
            break

    return items
