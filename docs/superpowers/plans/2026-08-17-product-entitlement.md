# Product Catalogue and Customer Entitlement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record what each customer bought, show it on the ticket, scope the bot's knowledge base to it, and freeze whether support was in contract when the ticket was raised.

**Architecture:** Three new doctypes (a flat `HD Product` catalogue, its KB-category child table, and a standalone `HD Customer Product` entitlement row), two custom fields on `HD Ticket` added via fixtures, one pure-logic module `helpdesk/entitlement.py` that every consumer calls, a `before_save` doc_event that stamps status once, and a narrowing of the bot's existing category allowlist.

**Tech Stack:** Frappe v16, MariaDB, Vue 3 + frappe-ui (desk frontend), Python `unittest` via `bench run-tests`.

**Spec:** `docs/superpowers/specs/2026-08-17-product-entitlement-design.md`

## Global Constraints

- **Nothing refuses service.** Entitlement and expiry are advisory. No code path may block ticket creation, reply, or bot response on entitlement state.
- **WhatsApp support must work with no ERPNext installed.** `helpdesk/integrations/tests/test_no_erpnext.py` must keep passing. No task here may import from `helpdesk.integrations.erpnext` or from `erpnext`.
- **No ERPNext calls at all in this sub-project.** Entitlements are local-only; syncing is sub-project B.
- **`hd_product` and `support_status` are Custom Fields via fixtures**, never edits to `hd_ticket.json`. Editing that file conflicts on every `git merge upstream/develop`.
- **Every query filtering `HD Ticket` on `hd_product` or `support_status` must be wrapped in `try/except`.** On a site that has not migrated since the fixtures landed, the column does not exist and Frappe raises `OperationalError`. This is the documented `baileys_jid` hazard in `CLAUDE.md`.
- **Leave upstream's dead `HD Ticket.product` Select alone.** Do not repurpose, rename, or delete it.
- **Blank `support_expiry` means permanently covered**, not expired.
- Run the full suite with `bench --site dev.localhost run-tests --app helpdesk`. Baseline before this plan: **187 tests, OK (skipped=2)**.

---

### Task 1: `HD Product` catalogue and its category child table

**Files:**
- Create: `helpdesk/helpdesk/doctype/hd_product/__init__.py`
- Create: `helpdesk/helpdesk/doctype/hd_product/hd_product.json`
- Create: `helpdesk/helpdesk/doctype/hd_product/hd_product.py`
- Create: `helpdesk/helpdesk/doctype/hd_product_article_category/__init__.py`
- Create: `helpdesk/helpdesk/doctype/hd_product_article_category/hd_product_article_category.json`
- Create: `helpdesk/helpdesk/doctype/hd_product_article_category/hd_product_article_category.py`
- Test: `helpdesk/tests/test_product_catalogue.py`

**Interfaces:**
- Consumes: nothing.
- Produces: doctype `HD Product` (named by `product_name`) with fields `product_name`, `description`, `disabled`, `default_team`, and child table `article_categories` of `HD Product Article Category` rows each holding `category` → `HD Article Category`.

- [ ] **Step 1: Write the failing test**

Create `helpdesk/tests/test_product_catalogue.py`:

```python
"""HD Product is a flat catalogue of what we sell, 5-15 rows.

Named by product_name so links read as "eTIMS" rather than a hash.
"""

import unittest

import frappe

PREFIX = "_test-prod-"


def cleanup():
    for name in frappe.get_all(
        "HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class TestProductCatalogue(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()

    def tearDown(self):
        cleanup()

    def test_product_is_named_by_product_name(self):
        doc = frappe.get_doc({
            "doctype": "HD Product",
            "product_name": PREFIX + "eTIMS",
        }).insert(ignore_permissions=True)
        self.assertEqual(doc.name, PREFIX + "eTIMS")

    def test_product_name_is_unique(self):
        frappe.get_doc({
            "doctype": "HD Product",
            "product_name": PREFIX + "POS",
        }).insert(ignore_permissions=True)
        with self.assertRaises(frappe.DuplicateEntryError):
            frappe.get_doc({
                "doctype": "HD Product",
                "product_name": PREFIX + "POS",
            }).insert(ignore_permissions=True)

    def test_product_carries_article_categories(self):
        category = frappe.get_all("HD Article Category", limit=1, pluck="name")
        if not category:
            self.skipTest("no HD Article Category on this site")
        doc = frappe.get_doc({
            "doctype": "HD Product",
            "product_name": PREFIX + "TIMS",
            "article_categories": [{"category": category[0]}],
        }).insert(ignore_permissions=True)
        doc.reload()
        self.assertEqual(len(doc.article_categories), 1)
        self.assertEqual(doc.article_categories[0].category, category[0])

    def test_disabled_defaults_to_zero(self):
        doc = frappe.get_doc({
            "doctype": "HD Product",
            "product_name": PREFIX + "Frappe",
        }).insert(ignore_permissions=True)
        self.assertEqual(doc.disabled, 0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_product_catalogue`

Expected: FAIL — `DoesNotExistError: DocType HD Product not found`.

- [ ] **Step 3: Create the child doctype**

`helpdesk/helpdesk/doctype/hd_product_article_category/__init__.py` — empty file.

`helpdesk/helpdesk/doctype/hd_product_article_category/hd_product_article_category.json`:

```json
{
 "actions": [],
 "allow_rename": 1,
 "creation": "2026-08-17 12:00:00.000000",
 "doctype": "DocType",
 "editable_grid": 1,
 "engine": "InnoDB",
 "field_order": ["category"],
 "fields": [
  {
   "fieldname": "category",
   "fieldtype": "Link",
   "in_list_view": 1,
   "label": "Category",
   "options": "HD Article Category",
   "reqd": 1
  }
 ],
 "istable": 1,
 "links": [],
 "modified": "2026-08-17 12:00:00.000000",
 "modified_by": "Administrator",
 "module": "Helpdesk",
 "name": "HD Product Article Category",
 "owner": "Administrator",
 "permissions": [],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

`helpdesk/helpdesk/doctype/hd_product_article_category/hd_product_article_category.py`:

```python
from frappe.model.document import Document


class HDProductArticleCategory(Document):
    pass
```

- [ ] **Step 4: Create the parent doctype**

`helpdesk/helpdesk/doctype/hd_product/__init__.py` — empty file.

`helpdesk/helpdesk/doctype/hd_product/hd_product.json`:

```json
{
 "actions": [],
 "allow_rename": 1,
 "autoname": "field:product_name",
 "creation": "2026-08-17 12:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": [
  "product_name",
  "description",
  "col_break_1",
  "disabled",
  "default_team",
  "section_kb",
  "article_categories"
 ],
 "fields": [
  {
   "fieldname": "product_name",
   "fieldtype": "Data",
   "in_list_view": 1,
   "label": "Product Name",
   "reqd": 1,
   "unique": 1
  },
  {
   "fieldname": "description",
   "fieldtype": "Small Text",
   "label": "Description"
  },
  {
   "fieldname": "col_break_1",
   "fieldtype": "Column Break"
  },
  {
   "default": "0",
   "fieldname": "disabled",
   "fieldtype": "Check",
   "label": "Disabled"
  },
  {
   "description": "Routing hint only. Nothing is routed automatically.",
   "fieldname": "default_team",
   "fieldtype": "Link",
   "label": "Default Team",
   "options": "HD Team"
  },
  {
   "fieldname": "section_kb",
   "fieldtype": "Section Break",
   "label": "Knowledge Base"
  },
  {
   "description": "Article categories covering this product. Used to scope bot answers.",
   "fieldname": "article_categories",
   "fieldtype": "Table",
   "label": "Article Categories",
   "options": "HD Product Article Category"
  }
 ],
 "links": [],
 "modified": "2026-08-17 12:00:00.000000",
 "modified_by": "Administrator",
 "module": "Helpdesk",
 "name": "HD Product",
 "owner": "Administrator",
 "permissions": [
  {"read": 1, "role": "Agent"},
  {"create": 1, "delete": 1, "read": 1, "role": "System Manager", "write": 1}
 ],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": [],
 "track_changes": 1
}
```

`helpdesk/helpdesk/doctype/hd_product/hd_product.py`:

```python
from frappe.model.document import Document


class HDProduct(Document):
    pass
```

- [ ] **Step 5: Apply the schema and run the tests**

Run:
```bash
cd /home/kushal/frappe-bench
bench --site dev.localhost migrate
bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_product_catalogue
```

Expected: PASS, 4 tests.

- [ ] **Step 6: Commit**

```bash
git add helpdesk/helpdesk/doctype/hd_product helpdesk/helpdesk/doctype/hd_product_article_category helpdesk/tests/test_product_catalogue.py
git commit -m "feat(product): add the HD Product catalogue

Flat, 5-15 rows, named by product_name so links read as \"eTIMS\" rather
than a hash. article_categories maps a product to the KB categories that
answer questions about it; the bot uses that mapping in a later task.

default_team is a routing hint only. Nothing routes automatically: an
outage at an out-of-contract customer must not be diverted to a sales desk."
```

---

### Task 2: `HD Customer Product` entitlement rows

**Files:**
- Create: `helpdesk/helpdesk/doctype/hd_customer_product/__init__.py`
- Create: `helpdesk/helpdesk/doctype/hd_customer_product/hd_customer_product.json`
- Create: `helpdesk/helpdesk/doctype/hd_customer_product/hd_customer_product.py`
- Create: `helpdesk/patches/add_hd_customer_product_unique_index.py`
- Modify: `helpdesk/patches.txt` (append one line)
- Test: `helpdesk/tests/test_customer_entitlement.py`

**Interfaces:**
- Consumes: `HD Product` from Task 1.
- Produces: doctype `HD Customer Product` with fields `customer`, `product`, `version`, `support_expiry`, `source` (`Manual`/`ERPNext`/`POS`, default `Manual`), `notes`; and a UNIQUE index named `hd_customer_product_customer_product` on `(customer, product)`.

- [ ] **Step 1: Write the failing test**

Create `helpdesk/tests/test_customer_entitlement.py`:

```python
"""One HD Customer Product row per (customer, product).

Standalone rather than a child table on HD Customer: sub-project B syncs
entitlements from ERPNext and POS licensing, and an idempotent upsert on a
unique key is far safer than reconciling child rows by idx.
"""

import unittest

import frappe

PREFIX = "_test-ent-"
CUSTOMER = PREFIX + "cust"
PRODUCT = PREFIX + "prod"


def cleanup():
    for name in frappe.get_all(
        "HD Customer Product", filters={"customer": CUSTOMER}, pluck="name"
    ):
        frappe.delete_doc("HD Customer Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Customer", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class TestCustomerEntitlement(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        frappe.get_doc({
            "doctype": "HD Customer", "name": CUSTOMER, "customer_name": CUSTOMER
        }).insert(ignore_permissions=True)
        frappe.get_doc({
            "doctype": "HD Product", "product_name": PRODUCT
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        cleanup()

    def make(self, **kwargs):
        payload = {
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": PRODUCT,
        }
        payload.update(kwargs)
        doc = frappe.get_doc(payload).insert(ignore_permissions=True)
        frappe.db.commit()
        return doc

    def test_entitlement_defaults_to_manual_source(self):
        doc = self.make()
        self.assertEqual(doc.source, "Manual")

    def test_blank_expiry_is_allowed(self):
        doc = self.make()
        self.assertFalse(doc.support_expiry)

    def test_duplicate_customer_product_is_rejected(self):
        """The unique index is what makes sub-project B's upsert safe."""
        self.make()
        with self.assertRaises(Exception):
            self.make()

    def test_same_product_for_a_different_customer_is_allowed(self):
        self.make()
        other = frappe.get_doc({
            "doctype": "HD Customer",
            "name": PREFIX + "cust2",
            "customer_name": PREFIX + "cust2",
        }).insert(ignore_permissions=True)
        doc = frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": other.name,
            "product": PRODUCT,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        self.assertTrue(doc.name)
        frappe.delete_doc("HD Customer Product", doc.name, force=True, ignore_permissions=True)
        frappe.delete_doc("HD Customer", other.name, force=True, ignore_permissions=True)
        frappe.db.commit()

    def test_unique_index_exists(self):
        rows = frappe.db.sql(
            "SHOW INDEX FROM `tabHD Customer Product` WHERE Key_name = %s",
            "hd_customer_product_customer_product",
            as_dict=True,
        )
        self.assertTrue(rows, "composite unique index was not created")
        self.assertEqual(rows[0]["Non_unique"], 0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_customer_entitlement`

Expected: FAIL — `DoesNotExistError: DocType HD Customer Product not found`.

- [ ] **Step 3: Create the doctype**

`helpdesk/helpdesk/doctype/hd_customer_product/__init__.py` — empty file.

`helpdesk/helpdesk/doctype/hd_customer_product/hd_customer_product.json`:

```json
{
 "actions": [],
 "allow_rename": 1,
 "creation": "2026-08-17 12:00:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": [
  "customer",
  "product",
  "version",
  "col_break_1",
  "support_expiry",
  "source",
  "section_notes",
  "notes"
 ],
 "fields": [
  {
   "fieldname": "customer",
   "fieldtype": "Link",
   "in_list_view": 1,
   "in_standard_filter": 1,
   "label": "Customer",
   "options": "HD Customer",
   "reqd": 1
  },
  {
   "fieldname": "product",
   "fieldtype": "Link",
   "in_list_view": 1,
   "in_standard_filter": 1,
   "label": "Product",
   "options": "HD Product",
   "reqd": 1
  },
  {
   "fieldname": "version",
   "fieldtype": "Data",
   "label": "Version"
  },
  {
   "fieldname": "col_break_1",
   "fieldtype": "Column Break"
  },
  {
   "description": "Blank means no expiry: permanently covered.",
   "fieldname": "support_expiry",
   "fieldtype": "Date",
   "in_list_view": 1,
   "label": "Support Expiry"
  },
  {
   "default": "Manual",
   "description": "Which system owns this row. A sync must never overwrite a Manual row.",
   "fieldname": "source",
   "fieldtype": "Select",
   "label": "Source",
   "options": "Manual\nERPNext\nPOS",
   "reqd": 1
  },
  {
   "fieldname": "section_notes",
   "fieldtype": "Section Break"
  },
  {
   "fieldname": "notes",
   "fieldtype": "Small Text",
   "label": "Notes"
  }
 ],
 "links": [],
 "modified": "2026-08-17 12:00:00.000000",
 "modified_by": "Administrator",
 "module": "Helpdesk",
 "name": "HD Customer Product",
 "owner": "Administrator",
 "permissions": [
  {"read": 1, "role": "Agent"},
  {"create": 1, "delete": 1, "read": 1, "role": "System Manager", "write": 1}
 ],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": [],
 "track_changes": 1
}
```

`helpdesk/helpdesk/doctype/hd_customer_product/hd_customer_product.py`:

```python
from frappe.model.document import Document


class HDCustomerProduct(Document):
    pass
```

- [ ] **Step 4: Create the unique index patch**

`helpdesk/patches/add_hd_customer_product_unique_index.py`:

```python
import frappe

INDEX_NAME = "hd_customer_product_customer_product"


def execute():
	"""Add a UNIQUE index on (customer, product) to tabHD Customer Product.

	Sub-project B syncs entitlements from ERPNext and POS licensing. Both need
	an idempotent upsert keyed on (customer, product); without a database-level
	constraint, two concurrent syncs can both find no row and both insert,
	leaving a customer holding the same product twice. Every downstream
	entitlement lookup would then see a duplicate.

	A DocType-level `unique: 1` flag only covers a single field, so a composite
	constraint needs a patch — same pattern as
	`add_wa_conversation_read_state_unique_index`.
	"""
	if not frappe.db.table_exists("HD Customer Product"):
		return

	existing = frappe.db.sql(
		"SHOW INDEX FROM `tabHD Customer Product` WHERE Key_name = %s",
		INDEX_NAME,
		as_dict=True,
	)
	if existing:
		return

	frappe.db.sql(
		f"ALTER TABLE `tabHD Customer Product` ADD UNIQUE INDEX `{INDEX_NAME}` (`customer`, `product`)"
	)
```

Append to the end of `helpdesk/patches.txt`:

```
helpdesk.patches.add_hd_customer_product_unique_index
```

- [ ] **Step 5: Apply and run the tests**

Run:
```bash
cd /home/kushal/frappe-bench
bench --site dev.localhost migrate
bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_customer_entitlement
```

Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add helpdesk/helpdesk/doctype/hd_customer_product helpdesk/patches/add_hd_customer_product_unique_index.py helpdesk/patches.txt helpdesk/tests/test_customer_entitlement.py
git commit -m "feat(product): add HD Customer Product entitlement rows

Standalone doctype rather than a child table on HD Customer. Sub-project B
will sync entitlements from ERPNext and POS licensing, and an idempotent
upsert against a unique (customer, product) key is far safer than
reconciling child rows by idx with no stable identity between runs.

The unique constraint is composite, so it needs a patch: a DocType-level
unique flag only covers a single field.

source records which system owns a row. A sync must never overwrite a
Manual row; a human decision outranks a mirror."
```

---

### Task 3: `hd_product` and `support_status` custom fields on `HD Ticket`

**Files:**
- Create: `helpdesk/patches/add_hd_ticket_product_fields.py`
- Modify: `helpdesk/patches.txt` (append one line)
- Modify: `helpdesk/hooks.py:178-184` (extend the existing Custom Field fixture filter)
- Test: `helpdesk/tests/test_ticket_product_fields.py`

**Interfaces:**
- Consumes: `HD Product` from Task 1.
- Produces: `HD Ticket.hd_product` (Link → `HD Product`) and `HD Ticket.support_status` (Select, options `\nCovered\nExpired\nNot Entitled\nUnknown`, read-only).

- [ ] **Step 1: Write the failing test**

Create `helpdesk/tests/test_ticket_product_fields.py`:

```python
"""hd_product and support_status are Custom Fields, added via fixtures.

They are NOT added to hd_ticket.json: that file belongs to upstream and
editing it conflicts on every `git merge upstream/develop`. Same approach as
baileys_jid / baileys_line.
"""

import unittest

import frappe


class TestTicketProductFields(unittest.TestCase):
    def test_hd_product_custom_field_exists(self):
        self.assertTrue(
            frappe.db.exists("Custom Field", {"dt": "HD Ticket", "fieldname": "hd_product"})
        )

    def test_support_status_custom_field_exists(self):
        self.assertTrue(
            frappe.db.exists("Custom Field", {"dt": "HD Ticket", "fieldname": "support_status"})
        )

    def test_hd_product_links_to_hd_product(self):
        options = frappe.db.get_value(
            "Custom Field", {"dt": "HD Ticket", "fieldname": "hd_product"}, "options"
        )
        self.assertEqual(options, "HD Product")

    def test_support_status_offers_the_four_states_and_blank(self):
        options = frappe.db.get_value(
            "Custom Field", {"dt": "HD Ticket", "fieldname": "support_status"}, "options"
        )
        self.assertEqual(options, "\nCovered\nExpired\nNot Entitled\nUnknown")

    def test_support_status_is_read_only(self):
        """It is stamped by the system and frozen. An agent editing it by hand
        would destroy the historical record it exists to keep."""
        read_only = frappe.db.get_value(
            "Custom Field", {"dt": "HD Ticket", "fieldname": "support_status"}, "read_only"
        )
        self.assertEqual(read_only, 1)

    def test_upstream_product_select_is_untouched(self):
        """Upstream's dead HD Ticket.product Select must survive intact —
        repurposing it would conflict on every upstream merge."""
        meta = frappe.get_meta("HD Ticket")
        field = meta.get_field("product")
        self.assertIsNotNone(field, "upstream product field was removed")
        self.assertEqual(field.fieldtype, "Select")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_ticket_product_fields`

Expected: FAIL — the first four tests fail because the Custom Fields do not exist. `test_upstream_product_select_is_untouched` should already PASS.

- [ ] **Step 3: Write the patch that creates the fields**

`helpdesk/patches/add_hd_ticket_product_fields.py`:

```python
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Add hd_product and support_status to HD Ticket as Custom Fields.

	Custom Fields rather than edits to hd_ticket.json: that file is upstream's
	and every edit conflicts on `git merge upstream/develop`. Same approach as
	baileys_jid / baileys_line.

	Upstream's `product` Select (options "Product A/B/C") is deliberately left
	alone. It is dead — zero references in Python or Vue — but repurposing it
	would conflict on every upstream merge for no benefit.

	create_custom_fields is idempotent, so re-running is safe.
	"""
	create_custom_fields(
		{
			"HD Ticket": [
				{
					"fieldname": "hd_product",
					"label": "Product",
					"fieldtype": "Link",
					"options": "HD Product",
					"insert_after": "ticket_type",
					"description": "What this ticket is about. Defaults to the customer's entitled products, but any active product may be chosen.",
				},
				{
					"fieldname": "support_status",
					"label": "Support Status",
					"fieldtype": "Select",
					"options": "\nCovered\nExpired\nNot Entitled\nUnknown",
					"insert_after": "hd_product",
					"read_only": 1,
					"description": "Stamped once, when the product first becomes known. Frozen afterwards so renewals do not rewrite history.",
				},
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()
```

Append to the end of `helpdesk/patches.txt`:

```
helpdesk.patches.add_hd_ticket_product_fields
```

- [ ] **Step 4: Add the fields to the fixture export list**

In `helpdesk/hooks.py`, the existing first fixture entry reads:

```python
fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            ["dt", "in", ["HD Ticket", "Customer", "HD Task"]],
            ["fieldname", "in", ["baileys_jid", "baileys_line", "helpdesk_notes"]],
        ],
    },
```

Change the `fieldname` list to include the two new fields:

```python
fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            ["dt", "in", ["HD Ticket", "Customer", "HD Task"]],
            [
                "fieldname",
                "in",
                [
                    "baileys_jid",
                    "baileys_line",
                    "helpdesk_notes",
                    "hd_product",
                    "support_status",
                ],
            ],
        ],
    },
```

- [ ] **Step 5: Apply and run the tests**

Run:
```bash
cd /home/kushal/frappe-bench
bench --site dev.localhost migrate
bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_ticket_product_fields
```

Expected: PASS, 6 tests.

- [ ] **Step 6: Export the fixtures**

Run:
```bash
cd /home/kushal/frappe-bench
bench --site dev.localhost export-fixtures --app helpdesk
```

Then confirm both fieldnames now appear in `helpdesk/fixtures/custom_field.json`:

```bash
cd apps/helpdesk
python3 -c "
import json
names = [e['fieldname'] for e in json.load(open('helpdesk/fixtures/custom_field.json'))]
print(names)
assert 'hd_product' in names and 'support_status' in names, 'fixture export missed the new fields'
print('OK')
"
```

- [ ] **Step 7: Commit**

```bash
git add helpdesk/patches/add_hd_ticket_product_fields.py helpdesk/patches.txt helpdesk/hooks.py helpdesk/fixtures/custom_field.json helpdesk/tests/test_ticket_product_fields.py
git commit -m "feat(product): add hd_product and support_status to HD Ticket

Custom Fields via fixtures, not edits to hd_ticket.json — that file is
upstream's and every edit conflicts on merge. Same approach as baileys_jid.

Upstream's dead product Select is left intact. It has zero references in
Python or Vue, but repurposing it would conflict on every upstream merge
for no benefit.

support_status is read-only: it is stamped by the system and frozen, and an
agent editing it by hand would destroy the historical record it exists to
keep."
```

---

### Task 4: `helpdesk/entitlement.py` — the single source of entitlement logic

**Files:**
- Create: `helpdesk/entitlement.py`
- Test: `helpdesk/tests/test_entitlement_logic.py`

**Interfaces:**
- Consumes: `HD Product`, `HD Customer Product` from Tasks 1-2; `HD Ticket.hd_product` from Task 3.
- Produces:
  - `is_expired(support_expiry) -> bool`
  - `get_entitlements(customer: str) -> list[dict]` — each `{"product", "version", "support_expiry", "source", "expired"}`
  - `get_entitlement(customer: str, product: str) -> dict | None`
  - `compute_support_status(customer: str | None, product: str | None) -> str` — one of `Covered`, `Expired`, `Not Entitled`, `Unknown`
  - `categories_for_product(product: str) -> list[str]`
  - `resolve_product_for_ticket(ticket: str) -> str | None`

- [ ] **Step 1: Write the failing test**

Create `helpdesk/tests/test_entitlement_logic.py`:

```python
"""Entitlement resolution. Advisory only — nothing here refuses service."""

import unittest

import frappe
from frappe.utils import add_days, today

from helpdesk import entitlement

PREFIX = "_test-entl-"
CUSTOMER = PREFIX + "cust"
PRODUCT_A = PREFIX + "prodA"
PRODUCT_B = PREFIX + "prodB"


def cleanup():
    for name in frappe.get_all(
        "HD Customer Product", filters={"customer": CUSTOMER}, pluck="name"
    ):
        frappe.delete_doc("HD Customer Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Customer", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class _Base(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        frappe.get_doc({
            "doctype": "HD Customer", "name": CUSTOMER, "customer_name": CUSTOMER
        }).insert(ignore_permissions=True)
        for p in (PRODUCT_A, PRODUCT_B):
            frappe.get_doc({"doctype": "HD Product", "product_name": p}).insert(
                ignore_permissions=True
            )
        frappe.db.commit()

    def tearDown(self):
        cleanup()

    def entitle(self, product, expiry=None):
        doc = frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": product,
            "support_expiry": expiry,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        return doc


class TestIsExpired(unittest.TestCase):
    def test_blank_expiry_is_never_expired(self):
        """Blank means permanently covered, not expired. Getting this backwards
        would mark every open-ended entitlement as out of contract."""
        self.assertFalse(entitlement.is_expired(None))
        self.assertFalse(entitlement.is_expired(""))

    def test_yesterday_is_expired(self):
        self.assertTrue(entitlement.is_expired(add_days(today(), -1)))

    def test_today_is_not_expired(self):
        """Support lasts through the whole expiry day."""
        self.assertFalse(entitlement.is_expired(today()))

    def test_tomorrow_is_not_expired(self):
        self.assertFalse(entitlement.is_expired(add_days(today(), 1)))


class TestSupportStatus(_Base):
    def test_no_customer_is_unknown(self):
        self.assertEqual(entitlement.compute_support_status(None, PRODUCT_A), "Unknown")

    def test_no_product_is_unknown(self):
        self.assertEqual(entitlement.compute_support_status(CUSTOMER, None), "Unknown")

    def test_entitled_with_no_expiry_is_covered(self):
        self.entitle(PRODUCT_A)
        self.assertEqual(entitlement.compute_support_status(CUSTOMER, PRODUCT_A), "Covered")

    def test_entitled_with_future_expiry_is_covered(self):
        self.entitle(PRODUCT_A, add_days(today(), 30))
        self.assertEqual(entitlement.compute_support_status(CUSTOMER, PRODUCT_A), "Covered")

    def test_entitled_with_past_expiry_is_expired(self):
        self.entitle(PRODUCT_A, add_days(today(), -1))
        self.assertEqual(entitlement.compute_support_status(CUSTOMER, PRODUCT_A), "Expired")

    def test_unentitled_product_is_not_entitled(self):
        self.entitle(PRODUCT_A)
        self.assertEqual(
            entitlement.compute_support_status(CUSTOMER, PRODUCT_B), "Not Entitled"
        )


class TestGetEntitlements(_Base):
    def test_returns_all_products_with_expiry_flag(self):
        self.entitle(PRODUCT_A)
        self.entitle(PRODUCT_B, add_days(today(), -5))
        rows = entitlement.get_entitlements(CUSTOMER)
        by_product = {r["product"]: r for r in rows}
        self.assertEqual(len(rows), 2)
        self.assertFalse(by_product[PRODUCT_A]["expired"])
        self.assertTrue(by_product[PRODUCT_B]["expired"])

    def test_no_customer_returns_empty(self):
        self.assertEqual(entitlement.get_entitlements(None), [])
        self.assertEqual(entitlement.get_entitlements(""), [])


class TestCategoriesForProduct(_Base):
    def test_product_without_categories_returns_empty(self):
        self.assertEqual(entitlement.categories_for_product(PRODUCT_A), [])

    def test_returns_mapped_categories(self):
        category = frappe.get_all("HD Article Category", limit=1, pluck="name")
        if not category:
            self.skipTest("no HD Article Category on this site")
        doc = frappe.get_doc("HD Product", PRODUCT_A)
        doc.append("article_categories", {"category": category[0]})
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        self.assertEqual(entitlement.categories_for_product(PRODUCT_A), [category[0]])

    def test_blank_product_returns_empty(self):
        self.assertEqual(entitlement.categories_for_product(None), [])


class TestResolveProductForTicket(_Base):
    def make_ticket(self, product=None):
        doc = frappe.get_doc({
            "doctype": "HD Ticket",
            "subject": PREFIX + "ticket",
            "description": "x",
            "customer": CUSTOMER,
            "hd_product": product,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        self.addCleanup(
            frappe.delete_doc, "HD Ticket", doc.name, force=True, ignore_permissions=True
        )
        return doc

    def test_explicit_ticket_product_wins(self):
        self.entitle(PRODUCT_A)
        self.entitle(PRODUCT_B)
        ticket = self.make_ticket(product=PRODUCT_B)
        self.assertEqual(entitlement.resolve_product_for_ticket(ticket.name), PRODUCT_B)

    def test_sole_entitlement_is_inferred(self):
        """A customer who owns only eTIMS gets correctly scoped answers before
        any agent touches the ticket."""
        self.entitle(PRODUCT_A)
        ticket = self.make_ticket()
        self.assertEqual(entitlement.resolve_product_for_ticket(ticket.name), PRODUCT_A)

    def test_multiple_entitlements_are_not_guessed(self):
        self.entitle(PRODUCT_A)
        self.entitle(PRODUCT_B)
        ticket = self.make_ticket()
        self.assertIsNone(entitlement.resolve_product_for_ticket(ticket.name))

    def test_missing_ticket_returns_none(self):
        self.assertIsNone(entitlement.resolve_product_for_ticket("no-such-ticket"))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_entitlement_logic`

Expected: FAIL — `ModuleNotFoundError: No module named 'helpdesk.entitlement'`.

- [ ] **Step 3: Write the implementation**

Create `helpdesk/entitlement.py`:

```python
"""Customer product entitlement.

Advisory only. Nothing in this module refuses service: the worst outcome of a
wrong answer here is a misleading badge, never a customer being denied help.

No ERPNext dependency. Entitlements are local rows; syncing them from ERPNext
or POS licensing is a separate sub-project.
"""

import frappe
from frappe.utils import getdate

STATUS_COVERED = "Covered"
STATUS_EXPIRED = "Expired"
STATUS_NOT_ENTITLED = "Not Entitled"
STATUS_UNKNOWN = "Unknown"


def is_expired(support_expiry) -> bool:
	"""True when support lapsed before today.

	A blank expiry means the entitlement never lapses — reading it as expired
	would mark every open-ended entitlement out of contract. Support lasts
	through the whole of the expiry day, so equality is not expiry.
	"""
	if not support_expiry:
		return False
	return getdate(support_expiry) < getdate()


def get_entitlements(customer: str | None) -> list[dict]:
	"""Every product this customer holds, newest expiry information included."""
	if not customer:
		return []
	rows = frappe.get_all(
		"HD Customer Product",
		filters={"customer": customer},
		fields=["name", "product", "version", "support_expiry", "source"],
		order_by="product asc",
	)
	for row in rows:
		row["expired"] = is_expired(row.get("support_expiry"))
	return rows


def get_entitlement(customer: str | None, product: str | None) -> dict | None:
	"""The single entitlement row for this customer and product, if any."""
	if not customer or not product:
		return None
	row = frappe.db.get_value(
		"HD Customer Product",
		{"customer": customer, "product": product},
		["name", "product", "version", "support_expiry", "source"],
		as_dict=True,
	)
	if not row:
		return None
	row["expired"] = is_expired(row.get("support_expiry"))
	return row


def compute_support_status(customer: str | None, product: str | None) -> str:
	"""Classify this customer's coverage for this product.

	Unknown is returned when we cannot tell — no customer, or no product — and
	is deliberately distinct from Not Entitled, which is a positive finding that
	the customer does not hold the product.
	"""
	if not customer or not product:
		return STATUS_UNKNOWN
	row = get_entitlement(customer, product)
	if not row:
		return STATUS_NOT_ENTITLED
	return STATUS_EXPIRED if row["expired"] else STATUS_COVERED


def categories_for_product(product: str | None) -> list[str]:
	"""HD Article Categories that answer questions about this product."""
	if not product:
		return []
	return frappe.get_all(
		"HD Product Article Category",
		filters={"parent": product, "parenttype": "HD Product"},
		pluck="category",
		order_by="idx asc",
	)


def resolve_product_for_ticket(ticket: str | None) -> str | None:
	"""Which product a ticket is about.

	Order: the ticket's own hd_product, else the customer's sole entitlement.
	The sole-entitlement rule earns its place — a customer who owns only one
	product gets correctly scoped answers before any agent tags the ticket.
	With two or more we do not guess; a wrong scope is worse than none.

	Wrapped in try/except because hd_product is a Custom Field: on a site that
	has not migrated since the fixtures landed the column does not exist and
	Frappe raises OperationalError. Same hazard as baileys_jid.
	"""
	if not ticket:
		return None

	try:
		row = frappe.db.get_value(
			"HD Ticket", ticket, ["hd_product", "customer"], as_dict=True
		)
	except Exception:
		return None

	if not row:
		return None
	if row.get("hd_product"):
		return row["hd_product"]

	entitlements = get_entitlements(row.get("customer"))
	if len(entitlements) == 1:
		return entitlements[0]["product"]
	return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_entitlement_logic`

Expected: PASS, 19 tests.

- [ ] **Step 5: Commit**

```bash
git add helpdesk/entitlement.py helpdesk/tests/test_entitlement_logic.py
git commit -m "feat(product): add entitlement resolution logic

One module every consumer calls, so the badge, the stamped status and the
bot's KB scoping can never disagree about what a customer holds.

Two decisions worth stating. A blank support_expiry means permanently
covered, not expired — reading it the other way would mark every
open-ended entitlement out of contract. And Unknown is distinct from Not
Entitled: the first means we cannot tell, the second is a positive finding
that the customer does not hold the product.

resolve_product_for_ticket infers the product when a customer holds exactly
one, so a single-product customer gets scoped answers before an agent tags
anything. With two or more it returns None rather than guessing.

The hd_product read is wrapped in try/except: it is a Custom Field, and on
an unmigrated site the column does not exist."
```

---

### Task 5: Stamp `support_status` once, when the product first becomes known

**Files:**
- Create: `helpdesk/overrides/ticket_product.py`
- Modify: `helpdesk/hooks.py` — add an `"HD Ticket"` entry to `doc_events`
- Test: `helpdesk/tests/test_support_status_stamp.py`

**Interfaces:**
- Consumes: `helpdesk.entitlement.compute_support_status` from Task 4; the custom fields from Task 3.
- Produces: `helpdesk.overrides.ticket_product.stamp_support_status(doc, method=None)`, wired to `HD Ticket` `before_save`.

- [ ] **Step 1: Write the failing test**

Create `helpdesk/tests/test_support_status_stamp.py`:

```python
"""support_status is stamped once and then frozen.

Stamping only at creation would be wrong: WhatsApp tickets are created
automatically on the first inbound message, long before an agent tags a
product, so a creation-only rule would leave support_status = Unknown
permanently on the channel this work started from.
"""

import unittest

import frappe
from frappe.utils import add_days, today

PREFIX = "_test-stamp-"
CUSTOMER = PREFIX + "cust"
PRODUCT = PREFIX + "prod"
OTHER = PREFIX + "other"


def cleanup():
    for name in frappe.get_all(
        "HD Ticket", filters={"subject": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Ticket", name, force=True, ignore_permissions=True,
                          delete_permanently=True)
    for name in frappe.get_all(
        "HD Customer Product", filters={"customer": CUSTOMER}, pluck="name"
    ):
        frappe.delete_doc("HD Customer Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Customer", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class TestSupportStatusStamp(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        frappe.get_doc({
            "doctype": "HD Customer", "name": CUSTOMER, "customer_name": CUSTOMER
        }).insert(ignore_permissions=True)
        for p in (PRODUCT, OTHER):
            frappe.get_doc({"doctype": "HD Product", "product_name": p}).insert(
                ignore_permissions=True
            )
        frappe.db.commit()

    def tearDown(self):
        cleanup()

    def entitle(self, product, expiry=None):
        frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": product,
            "support_expiry": expiry,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def ticket(self, **kwargs):
        payload = {
            "doctype": "HD Ticket",
            "subject": PREFIX + "t",
            "description": "x",
            "customer": CUSTOMER,
        }
        payload.update(kwargs)
        doc = frappe.get_doc(payload).insert(ignore_permissions=True)
        frappe.db.commit()
        return doc

    def test_stamped_at_creation_when_product_is_known(self):
        self.entitle(PRODUCT)
        doc = self.ticket(hd_product=PRODUCT)
        self.assertEqual(doc.support_status, "Covered")

    def test_expired_entitlement_stamps_expired(self):
        self.entitle(PRODUCT, add_days(today(), -1))
        doc = self.ticket(hd_product=PRODUCT)
        self.assertEqual(doc.support_status, "Expired")

    def test_unentitled_product_stamps_not_entitled(self):
        self.entitle(PRODUCT)
        doc = self.ticket(hd_product=OTHER)
        self.assertEqual(doc.support_status, "Not Entitled")

    def test_not_stamped_while_no_product_is_known(self):
        """The WhatsApp case: the ticket exists before anyone tags a product."""
        doc = self.ticket()
        self.assertFalse(doc.support_status)

    def test_stamped_when_product_is_set_later(self):
        """The empty -> set transition. Without this the WhatsApp channel would
        never get a status at all."""
        self.entitle(PRODUCT)
        doc = self.ticket()
        self.assertFalse(doc.support_status)

        doc.hd_product = PRODUCT
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        doc.reload()
        self.assertEqual(doc.support_status, "Covered")

    def test_frozen_when_entitlement_is_renewed(self):
        """The whole point of storing it: renewing must not rewrite what the
        status was when the customer asked."""
        self.entitle(PRODUCT, add_days(today(), -1))
        doc = self.ticket(hd_product=PRODUCT)
        self.assertEqual(doc.support_status, "Expired")

        row = frappe.get_all(
            "HD Customer Product",
            filters={"customer": CUSTOMER, "product": PRODUCT},
            pluck="name",
        )[0]
        frappe.db.set_value("HD Customer Product", row, "support_expiry", add_days(today(), 30))
        frappe.db.commit()

        doc.reload()
        doc.subject = PREFIX + "t edited"
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        doc.reload()
        self.assertEqual(doc.support_status, "Expired")

    def test_frozen_when_the_product_is_changed(self):
        self.entitle(PRODUCT)
        doc = self.ticket(hd_product=OTHER)
        self.assertEqual(doc.support_status, "Not Entitled")

        doc.hd_product = PRODUCT
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        doc.reload()
        self.assertEqual(doc.support_status, "Not Entitled")

    def test_unentitled_product_is_accepted_not_rejected(self):
        """Pre-sales, evaluations and stale mirror data are all legitimate
        reasons to pick a product the customer does not own. The constraint is
        a UI default, never server-side validation."""
        doc = self.ticket(hd_product=OTHER)
        self.assertTrue(doc.name)
        self.assertEqual(doc.support_status, "Not Entitled")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_support_status_stamp`

Expected: FAIL — `support_status` stays empty; the stamping hook does not exist yet.

- [ ] **Step 3: Write the hook handler**

Create `helpdesk/overrides/ticket_product.py`:

```python
"""Stamp HD Ticket.support_status when a product first becomes known."""

import frappe

from helpdesk import entitlement


def stamp_support_status(doc, method=None):
	"""Record coverage once, the first time we know what the ticket is about.

	Runs on before_save, which Frappe calls for both inserts and updates, so a
	single hook covers two cases: a ticket created with a product already set
	(portal and email flows), and a WhatsApp ticket created automatically on the
	first inbound message and tagged with a product minutes or days later.

	Stamping only at creation would leave support_status permanently Unknown on
	the WhatsApp channel, which is exactly where it matters most.

	Once set the value is frozen. It records what coverage WAS when the customer
	asked — the figure that matters for renewal conversations and for measuring
	absorbed out-of-contract support. Recomputing it would silently rewrite
	history the moment somebody renewed.

	Advisory only: this never blocks a save. An unentitled product is a recorded
	outcome, not an error.
	"""
	try:
		if doc.get("support_status"):
			return  # frozen
		product = doc.get("hd_product")
		if not product:
			return  # nothing to stamp yet
		doc.support_status = entitlement.compute_support_status(
			doc.get("customer"), product
		)
	except Exception:
		# A badge is never worth failing a ticket save over.
		frappe.log_error(
			frappe.get_traceback(), "Helpdesk: support_status stamping failed"
		)
```

- [ ] **Step 4: Wire the hook**

In `helpdesk/hooks.py`, inside the `doc_events` dict, add an `"HD Ticket"` entry alongside the existing ones. Place it immediately before the `"HD Article"` entry:

```python
    "HD Ticket": {
        "before_save": "helpdesk.overrides.ticket_product.stamp_support_status",
    },
    "HD Article": {
        "on_update": "helpdesk.integrations.embeddings.on_article_update",
    },
```

- [ ] **Step 5: Run the test to verify it passes**

Run:
```bash
cd /home/kushal/frappe-bench
bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_support_status_stamp
```

Expected: PASS, 8 tests.

- [ ] **Step 6: Commit**

```bash
git add helpdesk/overrides/ticket_product.py helpdesk/hooks.py helpdesk/tests/test_support_status_stamp.py
git commit -m "feat(product): stamp support_status when the product first becomes known

before_save runs for both inserts and updates, so one hook covers a ticket
created with a product already set and a WhatsApp ticket created
automatically on the first inbound message and tagged days later.

Stamping only at creation would have left support_status permanently
Unknown on the WhatsApp channel, which is where it matters most.

Once set it is frozen: it records what coverage WAS when the customer
asked, which is the figure renewal conversations need. Recomputing would
silently rewrite history the moment someone renewed.

The handler swallows its own errors. A badge is never worth failing a
ticket save over."
```

---

### Task 6: Scope the bot's knowledge base to the customer's product

**Files:**
- Modify: `helpdesk/integrations/bot.py:108` (`_combined_kb_search` signature and body)
- Modify: `helpdesk/integrations/bot.py:582` and `:689` (the two call sites)
- Create: `helpdesk/tests/test_bot_product_scoping.py`

**Interfaces:**
- Consumes: `helpdesk.entitlement.categories_for_product` and `resolve_product_for_ticket` from Task 4.
- Produces: `bot._scoped_categories(product: str | None) -> list[str]`; `bot._combined_kb_search(query: str, limit: int, product: str | None = None)`.

- [ ] **Step 1: Write the failing test**

Create `helpdesk/tests/test_bot_product_scoping.py`:

```python
"""The bot answers from the categories covering the customer's product.

The global allowlist in Helpdesk Bot Settings stays the ceiling: a product can
narrow what the bot reads, never widen it.
"""

import unittest
from unittest.mock import patch

from helpdesk.integrations import bot


class TestScopedCategories(unittest.TestCase):
    def test_no_product_falls_back_to_the_global_allowlist(self):
        with patch.object(bot, "_get_allowed_categories", return_value=["a", "b"]):
            self.assertEqual(bot._scoped_categories(None), ["a", "b"])

    def test_product_narrows_the_global_allowlist(self):
        with patch.object(bot, "_get_allowed_categories", return_value=["a", "b"]), \
             patch("helpdesk.entitlement.categories_for_product", return_value=["b", "c"]):
            self.assertEqual(bot._scoped_categories("eTIMS"), ["b"])

    def test_product_cannot_widen_the_global_allowlist(self):
        """The ceiling: a product mapped to a category the bot is not allowed to
        read must not gain access to it."""
        with patch.object(bot, "_get_allowed_categories", return_value=["a"]), \
             patch("helpdesk.entitlement.categories_for_product", return_value=["a", "secret"]):
            self.assertEqual(bot._scoped_categories("eTIMS"), ["a"])

    def test_empty_intersection_falls_back_to_the_global_allowlist(self):
        """Without this the bot searches nothing and goes silent for exactly the
        customers whose data is incomplete — worse than being slightly
        off-topic, and it presents as 'the bot is broken'."""
        with patch.object(bot, "_get_allowed_categories", return_value=["a"]), \
             patch("helpdesk.entitlement.categories_for_product", return_value=["z"]):
            self.assertEqual(bot._scoped_categories("eTIMS"), ["a"])

    def test_product_with_no_categories_falls_back(self):
        with patch.object(bot, "_get_allowed_categories", return_value=["a"]), \
             patch("helpdesk.entitlement.categories_for_product", return_value=[]):
            self.assertEqual(bot._scoped_categories("eTIMS"), ["a"])

    def test_unrestricted_global_allowlist_is_narrowed_by_product(self):
        """An empty global allowlist means 'no restriction'. A product should
        still scope it."""
        with patch.object(bot, "_get_allowed_categories", return_value=[]), \
             patch("helpdesk.entitlement.categories_for_product", return_value=["b"]):
            self.assertEqual(bot._scoped_categories("eTIMS"), ["b"])

    def test_unrestricted_and_no_product_stays_unrestricted(self):
        with patch.object(bot, "_get_allowed_categories", return_value=[]), \
             patch("helpdesk.entitlement.categories_for_product", return_value=[]):
            self.assertEqual(bot._scoped_categories(None), [])


class TestCombinedSearchPassesScope(unittest.TestCase):
    def test_search_uses_the_scoped_categories(self):
        with patch.object(bot, "_scoped_categories", return_value=["b"]) as scoped, \
             patch.object(bot, "_search_kb", return_value=[]) as search_kb:
            bot._combined_kb_search("query", 3, product="eTIMS")

        scoped.assert_called_once_with("eTIMS")
        self.assertEqual(search_kb.call_args.kwargs["allowed_categories"], ["b"])

    def test_search_without_a_product_still_works(self):
        """Existing callers pass no product; behaviour must not change."""
        with patch.object(bot, "_get_allowed_categories", return_value=["a"]), \
             patch.object(bot, "_search_kb", return_value=[]) as search_kb:
            bot._combined_kb_search("query", 3)

        self.assertEqual(search_kb.call_args.kwargs["allowed_categories"], ["a"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_bot_product_scoping`

Expected: FAIL — `AttributeError: module 'helpdesk.integrations.bot' has no attribute '_scoped_categories'`.

- [ ] **Step 3: Add `_scoped_categories` and thread the product through**

In `helpdesk/integrations/bot.py`, immediately after `_get_allowed_categories()` (which ends at line 43), insert:

```python
def _scoped_categories(product: str | None) -> list[str]:
	"""Narrow the bot's category allowlist to the customer's product.

	The global allowlist in Helpdesk Bot Settings is the ceiling — a product can
	only narrow it, never widen it. An empty global list means "no restriction",
	so a product still scopes it.

	Falling back to the global list when the intersection is empty is not
	optional. Searching an empty category set returns nothing, which would mute
	the bot for exactly the customers whose product mapping is incomplete. A
	slightly off-topic answer is a much better failure than silence that reads
	as a broken bot.
	"""
	global_allowed = _get_allowed_categories()

	from helpdesk import entitlement

	product_categories = entitlement.categories_for_product(product)
	if not product_categories:
		return global_allowed
	if not global_allowed:
		return product_categories

	scoped = [c for c in product_categories if c in global_allowed]
	return scoped or global_allowed
```

Then change `_combined_kb_search` (line 108). Its current signature and first body line are:

```python
def _combined_kb_search(query: str, limit: int) -> list[dict]:
	"""Query both local HD Articles and Outline directly, merge and deduplicate.

	Outline results take precedence for documents that exist in both (fresher content).
	Both paths respect the Allowed Categories list in Helpdesk Bot Settings.
	"""
	allowed_categories = _get_allowed_categories()
```

Replace those with:

```python
def _combined_kb_search(query: str, limit: int, product: str | None = None) -> list[dict]:
	"""Query both local HD Articles and Outline directly, merge and deduplicate.

	Outline results take precedence for documents that exist in both (fresher content).
	Both paths respect the Allowed Categories list in Helpdesk Bot Settings,
	narrowed to the ticket's product when one is known — see _scoped_categories.
	"""
	allowed_categories = _scoped_categories(product)
```

Leave the rest of the function unchanged — it already uses `allowed_categories` throughout.

- [ ] **Step 4: Pass the product at both call sites**

At `helpdesk/integrations/bot.py:582`, inside `process_message()`, `ticket_name` is already in scope. Change:

```python
	articles = _combined_kb_search(text, settings.kb_search_limit or 3)
```

to:

```python
	articles = _combined_kb_search(
		text,
		settings.kb_search_limit or 3,
		product=entitlement.resolve_product_for_ticket(ticket_name),
	)
```

At `helpdesk/integrations/bot.py:689`, inside `suggest_agent_reply(ticket, channel)`, change:

```python
	articles = _combined_kb_search(last_customer_msg, 3) if last_customer_msg else []
```

to:

```python
	articles = (
		_combined_kb_search(
			last_customer_msg, 3, product=entitlement.resolve_product_for_ticket(ticket)
		)
		if last_customer_msg
		else []
	)
```

Add the import at the top of `helpdesk/integrations/bot.py`, alongside the other `helpdesk` imports:

```python
from helpdesk import entitlement
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_bot_product_scoping`

Expected: PASS, 9 tests.

- [ ] **Step 6: Run the full suite**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk`

Expected: OK. `test_no_erpnext.py` must still pass — check it appears in the output.

- [ ] **Step 7: Commit**

```bash
git add helpdesk/integrations/bot.py helpdesk/tests/test_bot_product_scoping.py
git commit -m "feat(bot): scope knowledge base answers to the customer's product

_combined_kb_search took no customer context, so the global allowlist in
Helpdesk Bot Settings applied identically to every conversation and a
POS-only customer could be answered from eTIMS articles.

The global allowlist stays the ceiling: a product narrows it, never widens
it, so mapping a product to a category the bot is not allowed to read
cannot grant access to it.

When the intersection is empty the search falls back to the global list.
That fallback is load-bearing — searching an empty category set returns
nothing, which would mute the bot for exactly the customers whose product
mapping is incomplete, and silence reads as a broken bot."
```

---

### Task 7: Show coverage to the agent

**Files:**
- Create: `helpdesk/api/entitlement.py`
- Modify: `desk/src/components/ticket-agent/TicketContactTab.vue`
- Test: `helpdesk/tests/test_entitlement_api.py`

**Interfaces:**
- Consumes: `helpdesk.entitlement` from Task 4.
- Produces: whitelisted `helpdesk.api.entitlement.get_ticket_entitlement(ticket: str) -> dict` returning `{"customer", "product", "status", "support_expiry", "version", "entitlements": [...]}`.

- [ ] **Step 1: Write the failing test**

Create `helpdesk/tests/test_entitlement_api.py`:

```python
"""Agent-facing entitlement payload.

The badge is computed live and answers "is this customer covered right now",
which is a different question from HD Ticket.support_status — that is a frozen
record of coverage when the ticket was raised. Both exist on purpose.
"""

import unittest

import frappe
from frappe.utils import add_days, today

from helpdesk.api.entitlement import get_ticket_entitlement

PREFIX = "_test-entapi-"
CUSTOMER = PREFIX + "cust"
PRODUCT = PREFIX + "prod"


def cleanup():
    for name in frappe.get_all(
        "HD Ticket", filters={"subject": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Ticket", name, force=True, ignore_permissions=True,
                          delete_permanently=True)
    for name in frappe.get_all(
        "HD Customer Product", filters={"customer": CUSTOMER}, pluck="name"
    ):
        frappe.delete_doc("HD Customer Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Customer", name, force=True, ignore_permissions=True)
    frappe.db.commit()


class TestEntitlementApi(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        frappe.get_doc({
            "doctype": "HD Customer", "name": CUSTOMER, "customer_name": CUSTOMER
        }).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "HD Product", "product_name": PRODUCT}).insert(
            ignore_permissions=True
        )
        frappe.db.commit()

    def tearDown(self):
        cleanup()

    def ticket(self, **kwargs):
        payload = {
            "doctype": "HD Ticket",
            "subject": PREFIX + "t",
            "description": "x",
            "customer": CUSTOMER,
        }
        payload.update(kwargs)
        doc = frappe.get_doc(payload).insert(ignore_permissions=True)
        frappe.db.commit()
        return doc

    def test_returns_covered_for_an_entitled_product(self):
        frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": PRODUCT,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        doc = self.ticket(hd_product=PRODUCT)

        out = get_ticket_entitlement(doc.name)
        self.assertEqual(out["status"], "Covered")
        self.assertEqual(out["product"], PRODUCT)
        self.assertEqual(out["customer"], CUSTOMER)

    def test_badge_is_live_while_stamped_status_is_frozen(self):
        """Renewing changes the live badge but must not change the stamp."""
        frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": PRODUCT,
            "support_expiry": add_days(today(), -1),
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        doc = self.ticket(hd_product=PRODUCT)
        self.assertEqual(doc.support_status, "Expired")

        row = frappe.get_all(
            "HD Customer Product",
            filters={"customer": CUSTOMER, "product": PRODUCT},
            pluck="name",
        )[0]
        frappe.db.set_value("HD Customer Product", row, "support_expiry", add_days(today(), 30))
        frappe.db.commit()

        out = get_ticket_entitlement(doc.name)
        self.assertEqual(out["status"], "Covered", "badge must be live")
        doc.reload()
        self.assertEqual(doc.support_status, "Expired", "stamp must stay frozen")

    def test_lists_every_product_the_customer_holds(self):
        frappe.get_doc({
            "doctype": "HD Customer Product",
            "customer": CUSTOMER,
            "product": PRODUCT,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        doc = self.ticket()

        out = get_ticket_entitlement(doc.name)
        self.assertEqual([e["product"] for e in out["entitlements"]], [PRODUCT])

    def test_unknown_ticket_returns_an_empty_payload(self):
        out = get_ticket_entitlement("no-such-ticket")
        self.assertEqual(out["status"], "Unknown")
        self.assertEqual(out["entitlements"], [])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_entitlement_api`

Expected: FAIL — `ModuleNotFoundError: No module named 'helpdesk.api.entitlement'`.

- [ ] **Step 3: Write the API module**

Create `helpdesk/api/entitlement.py`:

```python
"""Agent-facing entitlement payload for the ticket sidebar."""

import frappe

from helpdesk import entitlement


@frappe.whitelist()
def get_ticket_entitlement(ticket: str) -> dict:
	"""Live coverage for a ticket, plus everything the customer holds.

	Live on purpose. HD Ticket.support_status is a frozen record of coverage
	when the ticket was raised; this answers the different question of whether
	the customer is covered right now. Both are useful and neither replaces the
	other.

	The hd_product read is wrapped in try/except because it is a Custom Field:
	on a site that has not migrated since the fixtures landed the column does
	not exist and Frappe raises OperationalError. Same hazard as baileys_jid.
	"""
	empty = {
		"customer": None,
		"product": None,
		"status": entitlement.STATUS_UNKNOWN,
		"support_expiry": None,
		"version": None,
		"entitlements": [],
	}
	if not ticket:
		return empty

	try:
		row = frappe.db.get_value(
			"HD Ticket", ticket, ["customer", "hd_product", "support_status"], as_dict=True
		)
	except Exception:
		return empty

	if not row:
		return empty

	product = entitlement.resolve_product_for_ticket(ticket)
	current = entitlement.get_entitlement(row.get("customer"), product)

	return {
		"customer": row.get("customer"),
		"product": product,
		"status": entitlement.compute_support_status(row.get("customer"), product),
		"stamped_status": row.get("support_status") or None,
		"support_expiry": current.get("support_expiry") if current else None,
		"version": current.get("version") if current else None,
		"entitlements": entitlement.get_entitlements(row.get("customer")),
	}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk --module helpdesk.tests.test_entitlement_api`

Expected: PASS, 4 tests.

- [ ] **Step 5: Render the badge in the ticket sidebar**

Open `desk/src/components/ticket-agent/TicketContactTab.vue`. This fork already modified it to show company, designation and mobile.

Add to the `<script setup>` block, alongside the existing resource declarations:

```ts
const entitlement = createResource({
  url: "helpdesk.api.entitlement.get_ticket_entitlement",
  makeParams: () => ({ ticket: props.ticket?.name }),
  auto: true,
});

const statusTone = computed(() => {
  switch (entitlement.data?.status) {
    case "Covered":
      return "bg-surface-green-2 text-ink-green-3";
    case "Expired":
      return "bg-surface-amber-2 text-ink-amber-3";
    case "Not Entitled":
      return "bg-surface-gray-3 text-ink-gray-7";
    default:
      return "bg-surface-gray-2 text-ink-gray-5";
  }
});
```

Ensure `computed` and `createResource` are imported at the top of the block:

```ts
import { computed } from "vue";
import { createResource } from "frappe-ui";
```

Add to the template, after the existing contact detail rows:

```vue
<div v-if="entitlement.data?.product" class="mt-3 border-t border-outline-gray-2 pt-3">
  <div class="mb-1.5 text-xs font-medium text-ink-gray-5">Support</div>
  <div class="flex items-center gap-2">
    <span class="truncate text-sm text-ink-gray-8">
      {{ entitlement.data.product }}
    </span>
    <span
      class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium"
      :class="statusTone"
    >
      {{ entitlement.data.status }}
    </span>
  </div>
  <div
    v-if="entitlement.data.support_expiry"
    class="mt-1 text-xs text-ink-gray-5"
  >
    Support until {{ entitlement.data.support_expiry }}
  </div>
</div>
```

Use only semantic Tailwind tokens as above — `AGENTS.md` requires it, and a raw colour such as `bg-green-100` would be unreadable in dark mode. This is the same class of bug already fixed in `WhatsAppAnalytics.vue`.

- [ ] **Step 6: Build and verify the classes compile**

Run:
```bash
cd /home/kushal/frappe-bench
bench build --app helpdesk
cd apps/helpdesk
for c in bg-surface-green-2 text-ink-green-3 bg-surface-amber-2 text-ink-amber-3; do
  n=$(grep -rl "\.${c}[{,: ]" helpdesk/public/desk/assets/*.css 2>/dev/null | wc -l)
  echo "$c -> $n files"
done
```

Expected: every class reports at least 1 file. **A class reporting 0 does not exist** — frappe-ui registers `surface` only for backgrounds and `ink` only for text, so a mismatched pair silently compiles to nothing. If any report 0, substitute a token that does compile and re-check before continuing.

- [ ] **Step 7: Run the full suite**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --app helpdesk`

Expected: OK, with `test_no_erpnext.py` still passing.

- [ ] **Step 8: Commit**

```bash
git add helpdesk/api/entitlement.py desk/src/components/ticket-agent/TicketContactTab.vue helpdesk/tests/test_entitlement_api.py
git commit -m "feat(product): show live support coverage on the ticket sidebar

The badge is computed live and answers whether the customer is covered
right now. HD Ticket.support_status answers the different question of what
coverage WAS when the ticket was raised. Both are useful; a test asserts
that renewing an entitlement moves the badge and leaves the stamp alone.

Uses semantic tokens only. A raw colour would be unreadable in dark mode,
and a mismatched surface/ink pair compiles to nothing at all — the same
class of bug already fixed in WhatsAppAnalytics.vue."
```

---

## Plan Self-Review

**Spec coverage** — every section of `2026-08-17-product-entitlement-design.md` maps to a task:

| Spec section | Task |
|---|---|
| `HD Product` + `HD Product Article Category` | 1 |
| `HD Customer Product`, hash naming, composite unique index | 2 |
| Standalone-not-child-table rationale, `source` ownership rule | 2 |
| `HD Ticket.hd_product` custom field via fixtures | 3 |
| `try/except` on unmigrated sites | 4, 7 |
| Selection constrained but overridable; server accepts any product | 5 (test), 7 (UI) |
| `support_status`, four states, stamped on first-known, frozen | 5 |
| Live badge distinct from the frozen stamp | 7 |
| Bot KB scoping, global ceiling, empty-intersection fallback | 6 |
| Single-entitlement inference | 4 |
| Upstream `product` Select left alone | 3 (test) |
| Standing invariant: no ERPNext | Global Constraints; 6 and 7 re-run the guard suite |
| Sync-readiness (`source`, unique index) | 2 |

Out-of-scope items in the spec (ERPNext calls, account standing, the contact guard, per-branch instances, automatic routing) correctly have no task.

**Placeholder scan:** no TBD/TODO, no "add error handling", no "similar to Task N". Every code step carries real code.

**Type consistency:** `compute_support_status(customer, product)` is called with that argument order in Tasks 5 and 7. `categories_for_product(product)` and `resolve_product_for_ticket(ticket)` keep their signatures across Tasks 4, 6 and 7. The four status strings — `Covered`, `Expired`, `Not Entitled`, `Unknown` — are identical in the Select options (Task 3), the module constants (Task 4), the stamping tests (Task 5) and the badge tones (Task 7). `_scoped_categories` and `_combined_kb_search(query, limit, product=None)` match between definition and call sites in Task 6.
