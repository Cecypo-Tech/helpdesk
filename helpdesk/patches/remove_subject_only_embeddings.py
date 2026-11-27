"""Drop ticket embeddings that carry only a subject line.

Agents never filled resolution_details, so these rows matched questions on subject
alone and crowded out real fixes. Approved learned notes replace them.
"""

import frappe


def execute():
	from helpdesk.integrations.embeddings import _strip_html

	for r in frappe.get_all("HD Ticket Embedding", fields=["name", "resolution_details"]):
		if not _strip_html(r.resolution_details or ""):
			frappe.db.delete("HD Ticket Embedding", r.name)
