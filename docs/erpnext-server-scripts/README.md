# ERPNext Server Scripts for Helpdesk

Two API-type Server Scripts that live on the **ERPNext** bench. Helpdesk calls
them over HTTP. Nothing here is installed as an app, and helpdesk has no ERPNext
code dependency — see `docs/superpowers/specs/2026-08-17-product-entitlement-design.md`
for the standing invariant that WhatsApp support keeps working with no ERPNext at all.

| file | API method | purpose |
|---|---|---|
| `helpdesk_customer_sync.py` | `helpdesk_customer_sync` | customers + territory + tax_id + contacts |
| `helpdesk_customer_standing.py` | `helpdesk_customer_standing` | outstanding and overdue invoice totals |

## Installing

For each script: **Server Script** → New, then

| field | value |
|---|---|
| Script Type | `API` |
| API Method | the method name from the table above |
| Allow Guest | **unchecked** |
| Enable Rate Limit | checked — 60 requests / 60 seconds is a sane start |

Paste the file contents into **Script** and save. The endpoint is then
`/api/method/<api_method>`.

## Prerequisite: `server_script_enabled`

Server Scripts only run when this is set in **`common_site_config.json`**:

```json
{ "server_script_enabled": 1 }
```

Site-level `site_config.json` is **ignored** for this key — `is_safe_exec_enabled()`
reads `frappe.get_common_site_config()` and nothing else (`frappe/utils/safe_exec.py:86`).
On Frappe Cloud this is a bench-level setting, not something you can toggle per site
from the UI.

## Authentication

Call with a token header, using a **dedicated ERPNext user**:

```
Authorization: token <api_key>:<api_secret>
```

```bash
curl -s -H "Authorization: token KEY:SECRET" \
  "https://erp.example.com/api/method/helpdesk_customer_sync?limit=5"
```

Response shape is `{"message": { ... }}` — an API Server Script returns whatever
it assigned to `frappe.flags`.

## Security: roles do not protect these endpoints

Inside `safe_exec`:

- `frappe.get_all` is forced to `ignore_permissions=True` (`safe_exec.py:306`)
- `frappe.db.sql` bypasses permissions entirely

So **restricting the API user's roles does not restrict what these endpoints
return.** `helpdesk_customer_sync` returns every non-disabled Customer; standing
returns financial data for any customer asked about.

Consequences:

1. The API key **is** the access control. Dedicated user, rotated like a secret,
   never shared with a human login.
2. Keep rate limiting on — it is the only brake if a key leaks.
3. Restrict by network where possible.
4. Helpdesk must show AR figures to agents only, and must never let the bot quote
   them into a WhatsApp thread. A WhatsApp number can be a shared office handset.

## Constraints these scripts are written around

RestrictedPython and `safe_exec` impose rules that are easy to trip:

- **No identifier may begin with `_`.** Not variables, not functions, not dict
  keys accessed by subscript.
- **`remove_unsafe_fields()` silently strips any field containing `(`.** So
  `fields=["SUM(outstanding_amount)"]` is dropped with no error and returns wrong
  numbers. Aggregates must use `frappe.db.sql`.
- **`frappe.db.sql` is SELECT-only** and the query must literally start with
  `select` (or `with` on MariaDB) after stripping — a leading `--` comment breaks it.
- Return values go in `frappe.flags`, not `return`.

## Known caveats

- **Single currency, by decision (2026-08-17).** `helpdesk_customer_standing`
  uses `MAX(currency)` and sums across invoices, which is correct only while a
  customer transacts in one currency. Confirmed acceptable: this business
  invoices in KES only.

  This is a live assumption, not a solved problem. **If you ever raise an invoice
  for an existing customer in a second currency, these totals silently add unlike
  units** — no error, just a wrong number on an agent's screen. The fix is to add
  `currency` to the `GROUP BY` and return one row per currency; do that before
  the first foreign-currency invoice, not after.
- **`has_more` is a heuristic** in the sync endpoint: it reports whether the page
  came back full, so a final page of exactly `limit` rows costs one extra empty call.
- **Paging.** Use `modified_after` plus the largest `modified` from the previous
  page. Ordering is `modified asc` for exactly this reason. Do not page with
  `offset` alone across a table that is changing underneath you.
- **Overdue is computed from `due_date < CURDATE()`, not `status`.** ERPNext's
  `Sales Invoice.status` is maintained by a scheduled job; if that job is behind,
  status lies. `due_date` is evaluated at read time and cannot drift.

## Verified

Both scripts were installed and executed on `dev.localhost` against real data on
2026-08-17: customer sync returned contacts with populated `tax_id` and
`territory`; standing returned correct overdue totals, counts, oldest due date
and day count, and resolved a customer by `tax_id`. Over HTTP the endpoints
return 403 to an unauthenticated caller (a nonexistent method returns 417),
confirming the routes register and reject on authentication.
