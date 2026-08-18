import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Add HD Article.products, a table of HD Article Product rows.

	A Custom Field rather than an edit to hd_article.json, for the same reason
	as hd_product on HD Ticket: that file is upstream's and every edit conflicts
	on `git merge upstream/develop`.

	Leaving an article untagged is meaningful — it marks the article as generic,
	eligible for every product — so there is nothing to backfill here.

	create_custom_fields is idempotent, so re-running is safe.
	"""
	create_custom_fields(
		{
			"HD Article": [
				{
					"fieldname": "products",
					"label": "Products",
					"fieldtype": "Table",
					"options": "HD Article Product",
					"insert_after": "category",
					"description": "Which products this article covers. Leave empty for an article that applies to every product.",
				},
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()
