# ── Server Script: helpdesk_customer_entitlements ────────────────────────────
# Install on the ERPNext bench.
#   Script Type : API
#   API Method  : helpdesk_customer_entitlements
#   Allow Guest : NO
#   Rate limit  : recommended, 60 / 60s
#
# Endpoint:  GET|POST /api/method/helpdesk_customer_entitlements
# Params (optional):
#   customers   comma-separated Customer names; omit for all
#   limit       max rows (default 2000, hard cap 5000)
#
# Response: {"message": {"version", "as_of", "count", "entitlements": [...]}}
#
# Returns ONE ROW per (customer, item, sales order). Folding those into
# "what does this customer currently hold, and until when" happens in Helpdesk,
# not here — that rule changes as the business changes, and this file can only
# be edited by pasting into a production textarea.
#
# WHY SALES ORDERS AND NOT SUBSCRIPTIONS: this instance does not use ERPNext's
# Subscription doctype at all (zero records). Recurring support is a Sales Order
# with an Auto Repeat attached, and Sales Order.delivery_date is the renewal /
# expiry date — the operator's own renewal email labels it exactly that.
#
# RestrictedPython: no identifier may begin with an underscore, output goes to
# frappe.flags, and frappe.db.sql accepts SELECT only.

args = frappe.form_dict

limit = min(int(args.get("limit") or 2000), 5000)
customers_csv = (args.get("customers") or "").strip()

wanted = []
for part in customers_csv.split(","):
    cleaned = part.strip()
    if cleaned and cleaned not in wanted:
        wanted.append(cleaned)

# Two separate literal queries rather than one built with .format(): under
# RestrictedPython, str.format is blocked outright ("format is an unsafe
# attribute"), and % interpolation into SQL is how injection happens. Spelling
# both out is duller and safer.
params = {"limit": limit}

SELECT_CLAUSE = """
    SELECT so.name          AS sales_order,
           so.customer      AS customer,
           so.delivery_date AS delivery_date,
           so.status        AS so_status,
           so.per_billed    AS per_billed,
           so.transaction_date AS ordered_on,
           so.currency      AS currency,
           soi.item_code    AS item_code,
           soi.item_name    AS item_name
    FROM `tabSales Order Item` soi
    JOIN `tabSales Order` so ON so.name = soi.parent
    WHERE so.docstatus = 1
      AND so.auto_repeat IS NOT NULL
      AND so.auto_repeat != ''
"""

ORDER_CLAUSE = """
    ORDER BY so.customer ASC, soi.item_code ASC, so.delivery_date DESC
    LIMIT %(limit)s
"""

if wanted:
    params["customers"] = wanted
    rows = frappe.db.sql(
        SELECT_CLAUSE + " AND so.customer IN %(customers)s " + ORDER_CLAUSE,
        params,
        as_dict=True,
    )
else:
    rows = frappe.db.sql(SELECT_CLAUSE + ORDER_CLAUSE, params, as_dict=True)

entitlements = []
for r in rows:
    entitlements.append({
        "customer": r["customer"],
        "item_code": r["item_code"],
        "item_name": r["item_name"],
        "delivery_date": str(r["delivery_date"]) if r["delivery_date"] else None,
        "so_status": r["so_status"],
        "per_billed": float(r["per_billed"] or 0),
        "sales_order": r["sales_order"],
        "ordered_on": str(r["ordered_on"]) if r["ordered_on"] else None,
        "currency": r["currency"],
    })

frappe.flags.version = 1
frappe.flags.as_of = frappe.utils.now()
frappe.flags.count = len(entitlements)
frappe.flags.truncated = len(rows) == limit
frappe.flags.entitlements = entitlements

# ── Notes ────────────────────────────────────────────────────────────────────
# ONLY SALES ORDERS WITH AN AUTO REPEAT are returned. That is what distinguishes
# a recurring support contract from a one-off sale. Sales Order.auto_repeat is a
# Link field, so this is a direct join rather than a reverse lookup.
#
# per_billed AND so_status ARE PASSED THROUGH, NOT INTERPRETED. The Auto Repeat
# runs with submit_on_creation, so a renewal Sales Order is created AND SUBMITTED
# about a month before expiry — before anybody has paid. Helpdesk uses per_billed
# to show "Covered, renewal unpaid" rather than silently granting another year of
# cover to a customer who never renewed. Deciding that here would bury a
# business rule in a file nobody can test.
#
# WHY per_billed RATHER THAN status: status is derived and can be reconfigured
# per site; per_billed is a number that means one thing. Note it answers "has an
# invoice been raised", NOT "have they paid" — an invoice can be raised and
# ignored. That case is already covered elsewhere: an unpaid invoice shows up as
# an overdue balance through helpdesk_customer_standing, so the two signals stay
# distinct instead of double-counting.
#
# NO PAYMENT JOIN. Following Sales Order -> Sales Invoice -> outstanding would be
# a more precise answer at the cost of a much heavier query and a second business
# rule in this textarea.
#
# PERMISSIONS: frappe.db.sql bypasses permissions entirely inside safe_exec, so
# this returns every recurring order regardless of the caller's roles. The API
# key is the access control. Use a dedicated user with NO roles, restrict_ip set
# to the Helpdesk bench, and rotate the key like a secret.
