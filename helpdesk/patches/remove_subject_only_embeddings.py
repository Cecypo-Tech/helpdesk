"""Drop ticket embeddings that carry only a subject line.

Agents never filled resolution_details, so these rows matched questions on subject
alone and crowded out real fixes. Approved learned notes replace them.
"""

import frappe


def execute():
	frappe.db.sql(
		"""delete from `tabHD Ticket Embedding`
		where resolution_details is null or trim(resolution_details) = ''"""
	)
