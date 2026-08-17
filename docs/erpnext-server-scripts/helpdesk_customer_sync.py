# ── Server Script: helpdesk_customer_sync ────────────────────────────────────
# Install on the ERPNext bench.
#   Script Type : API
#   API Method  : helpdesk_customer_sync
#   Allow Guest : NO
#   Rate limit  : recommended (see notes at the bottom)
#
# Endpoint:  GET|POST /api/method/helpdesk_customer_sync
# Params:
#   modified_after  ISO datetime — incremental sync; returns rows changed since
#   tax_id          exact KRA PIN lookup
#   customer        exact Customer name lookup
#   limit           default 200, hard max 500
#   offset          for paging alongside limit
#
# Response: {"message": {"version", "synced_at", "count", "has_more", "customers"}}
#
# NOTE ON NAMES: RestrictedPython forbids identifiers beginning with an
# underscore, so nothing here uses one.

args = frappe.form_dict

limit = min(int(args.get("limit") or 200), 500)
offset = int(args.get("offset") or 0)
modified_after = (args.get("modified_after") or "").strip()
tax_id = (args.get("tax_id") or "").strip()
customer_name = (args.get("customer") or "").strip()

filters = {"disabled": 0}
if customer_name:
    filters["name"] = customer_name
if tax_id:
    filters["tax_id"] = tax_id
if modified_after:
    filters["modified"] = [">", modified_after]

customers = frappe.get_all(
    "Customer",
    filters=filters,
    fields=[
        "name",
        "customer_name",
        "tax_id",
        "territory",
        "customer_group",
        "default_currency",
        "modified",
    ],
    order_by="modified asc",
    limit_page_length=limit,
    limit_start=offset,
)

names = []
for row in customers:
    names.append(row["name"])

contacts_by_customer = {}

if names:
    # Contacts attach to a Customer through Dynamic Link, not a direct field.
    links = frappe.get_all(
        "Dynamic Link",
        filters={
            "link_doctype": "Customer",
            "link_name": ["in", names],
            "parenttype": "Contact",
        },
        fields=["parent", "link_name"],
    )

    contact_names = []
    for link in links:
        if link["parent"] not in contact_names:
            contact_names.append(link["parent"])

    contact_rows = {}
    emails_by_contact = {}
    phones_by_contact = {}

    if contact_names:
        for c in frappe.get_all(
            "Contact",
            filters={"name": ["in", contact_names]},
            fields=[
                "name",
                "first_name",
                "last_name",
                "email_id",
                "mobile_no",
                "phone",
                "designation",
                "status",
            ],
        ):
            contact_rows[c["name"]] = c

        # A contact can hold several addresses/numbers; the child tables are the
        # authoritative list and email_id/mobile_no are only the primaries.
        for e in frappe.get_all(
            "Contact Email",
            filters={"parent": ["in", contact_names]},
            fields=["parent", "email_id", "is_primary"],
        ):
            emails_by_contact.setdefault(e["parent"], []).append(e)

        for p in frappe.get_all(
            "Contact Phone",
            filters={"parent": ["in", contact_names]},
            fields=["parent", "phone", "is_primary_mobile_no", "is_primary_phone"],
        ):
            phones_by_contact.setdefault(p["parent"], []).append(p)

    for link in links:
        c = contact_rows.get(link["parent"])
        if not c:
            continue

        parts = []
        if c.get("first_name"):
            parts.append(c["first_name"])
        if c.get("last_name"):
            parts.append(c["last_name"])

        emails = []
        for e in emails_by_contact.get(c["name"], []):
            emails.append({"email": e["email_id"], "is_primary": e["is_primary"]})

        phones = []
        for p in phones_by_contact.get(c["name"], []):
            phones.append(
                {
                    "phone": p["phone"],
                    "is_primary_mobile": p["is_primary_mobile_no"],
                    "is_primary_phone": p["is_primary_phone"],
                }
            )

        contacts_by_customer.setdefault(link["link_name"], []).append(
            {
                "contact": c["name"],
                "full_name": " ".join(parts),
                "email": c.get("email_id"),
                "mobile_no": c.get("mobile_no"),
                "phone": c.get("phone"),
                "designation": c.get("designation"),
                "status": c.get("status"),
                "emails": emails,
                "phones": phones,
            }
        )

out = []
for row in customers:
    out.append(
        {
            "customer": row["name"],
            "customer_name": row["customer_name"],
            "tax_id": row.get("tax_id"),
            "territory": row.get("territory"),
            "customer_group": row.get("customer_group"),
            "currency": row.get("default_currency"),
            "modified": str(row["modified"]),
            "contacts": contacts_by_customer.get(row["name"], []),
        }
    )

frappe.flags.version = 1
frappe.flags.synced_at = frappe.utils.now()
frappe.flags.count = len(out)
frappe.flags.has_more = len(customers) == limit
frappe.flags.customers = out

# ── Notes ────────────────────────────────────────────────────────────────────
# PERMISSIONS: inside safe_exec, frappe.get_all forces ignore_permissions=True.
# Restricting the API user's roles therefore does NOT restrict this endpoint —
# it returns every non-disabled Customer regardless. Treat the API key as the
# only access control, give it to a dedicated user, and rotate it like a secret.
#
# INCREMENTAL SYNC: page with modified_after + limit, and carry the largest
# `modified` you saw into the next call. Ordering is `modified asc` precisely so
# that is safe. Do not page with offset alone across a changing table.
#
# has_more is a heuristic: it reports whether the page came back full. A final
# page that happens to be exactly `limit` rows yields one extra empty call.
