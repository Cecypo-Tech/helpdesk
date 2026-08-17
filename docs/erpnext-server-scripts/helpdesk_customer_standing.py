# ── Server Script: helpdesk_customer_standing ────────────────────────────────
# Install on the ERPNext bench.
#   Script Type : API
#   API Method  : helpdesk_customer_standing
#   Allow Guest : NO
#   Rate limit  : recommended (see notes at the bottom)
#
# Endpoint:  GET|POST /api/method/helpdesk_customer_standing
# Params (one of):
#   customer        exact Customer name
#   tax_id          exact KRA PIN — resolved to customers first
#   customers       comma-separated Customer names (max 100)
# Optional:
#   include_invoices  "1" to include per-invoice detail (max 50 per customer)
#
# Response: {"message": {"version", "as_of", "standing": [...]}}
#
# Standing is ADVISORY. Helpdesk flags and routes on it; it never refuses
# support. See docs/superpowers/specs — the failure mode being avoided is a
# customer whose statutory eTIMS filing is broken being denied help because
# their finance department is late paying.

args = frappe.form_dict

customer_name = (args.get("customer") or "").strip()
tax_id = (args.get("tax_id") or "").strip()
customers_csv = (args.get("customers") or "").strip()
include_invoices = str(args.get("include_invoices") or "") in ("1", "true", "True", "yes")

wanted = []
if customer_name:
    wanted.append(customer_name)

if customers_csv:
    for part in customers_csv.split(","):
        cleaned = part.strip()
        if cleaned and cleaned not in wanted:
            wanted.append(cleaned)

if tax_id:
    for row in frappe.get_all("Customer", filters={"tax_id": tax_id}, fields=["name"]):
        if row["name"] not in wanted:
            wanted.append(row["name"])

wanted = wanted[:100]

standing = []

if wanted:
    # Aggregates cannot go through frappe.get_all here: inside safe_exec,
    # remove_unsafe_fields() strips any field containing "(", so SUM(...) would
    # be dropped silently and return wrong numbers. frappe.db.sql is SELECT-only
    # in this context and is the correct tool.
    rows = frappe.db.sql(
        """
        SELECT
            customer,
            SUM(outstanding_amount)                                             AS outstanding,
            SUM(CASE WHEN due_date < CURDATE() THEN outstanding_amount ELSE 0 END) AS overdue,
            SUM(CASE WHEN due_date < CURDATE() THEN 1 ELSE 0 END)               AS overdue_count,
            MIN(CASE WHEN due_date < CURDATE() THEN due_date ELSE NULL END)     AS oldest_due_date,
            MAX(currency)                                                       AS currency
        FROM `tabSales Invoice`
        WHERE docstatus = 1
          AND outstanding_amount > 0
          AND customer IN %(customers)s
        GROUP BY customer
        """,
        {"customers": wanted},
        as_dict=True,
    )

    by_customer = {}
    for row in rows:
        by_customer[row["customer"]] = row

    invoices_by_customer = {}
    if include_invoices:
        detail = frappe.db.sql(
            """
            SELECT name, customer, posting_date, due_date, currency,
                   grand_total, outstanding_amount, status
            FROM `tabSales Invoice`
            WHERE docstatus = 1
              AND outstanding_amount > 0
              AND customer IN %(customers)s
            ORDER BY due_date ASC
            """,
            {"customers": wanted},
            as_dict=True,
        )
        for inv in detail:
            bucket = invoices_by_customer.setdefault(inv["customer"], [])
            if len(bucket) < 50:
                bucket.append(
                    {
                        "invoice": inv["name"],
                        "posting_date": str(inv["posting_date"]),
                        "due_date": str(inv["due_date"]),
                        "currency": inv["currency"],
                        "grand_total": float(inv["grand_total"] or 0),
                        "outstanding": float(inv["outstanding_amount"] or 0),
                        "status": inv["status"],
                    }
                )

    today = frappe.utils.getdate()

    for name in wanted:
        agg = by_customer.get(name)

        if not agg:
            # No open invoices at all. Distinct from "we could not tell".
            standing.append(
                {
                    "customer": name,
                    "outstanding": 0.0,
                    "overdue": 0.0,
                    "overdue_count": 0,
                    "oldest_due_date": None,
                    "days_overdue": 0,
                    "currency": None,
                    "is_overdue": False,
                    "invoices": [],
                }
            )
            continue

        oldest = agg.get("oldest_due_date")
        days_overdue = 0
        if oldest:
            days_overdue = frappe.utils.date_diff(today, oldest)

        overdue_amount = float(agg.get("overdue") or 0)

        standing.append(
            {
                "customer": name,
                "outstanding": float(agg.get("outstanding") or 0),
                "overdue": overdue_amount,
                "overdue_count": int(agg.get("overdue_count") or 0),
                "oldest_due_date": str(oldest) if oldest else None,
                "days_overdue": days_overdue,
                "currency": agg.get("currency"),
                "is_overdue": overdue_amount > 0,
                "invoices": invoices_by_customer.get(name, []),
            }
        )

frappe.flags.version = 1
frappe.flags.as_of = frappe.utils.now()
frappe.flags.standing = standing

# ── Notes ────────────────────────────────────────────────────────────────────
# WHY due_date AND NOT status='Overdue': ERPNext's Sales Invoice.status is
# maintained by a scheduled job. If that job is behind, status lies. due_date
# against CURDATE() is computed at read time and cannot drift.
#
# CURRENCY: MAX(currency) is a deliberate simplification and is only correct
# when a customer transacts in one currency. If you invoice the same customer in
# both KES and USD, these totals add unlike units — group by currency before
# trusting the figure.
#
# PERMISSIONS: frappe.db.sql bypasses permissions entirely. The API key is the
# only access control. Use a dedicated user and rotate the key like a secret.
#
# PRIVACY: this returns financial detail. Helpdesk must show it to agents only,
# and must never let the bot quote it into a WhatsApp thread — a WhatsApp number
# can be a shared office handset.
