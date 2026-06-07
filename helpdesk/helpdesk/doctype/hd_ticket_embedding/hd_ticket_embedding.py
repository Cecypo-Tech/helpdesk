import frappe
from frappe.model.document import Document


class HDTicketEmbedding(Document):
	def after_insert(self):
		# Invalidate the in-memory embedding cache so the next bot query picks this up.
		try:
			from helpdesk.integrations.embeddings import invalidate_cache
			invalidate_cache()
		except Exception:
			pass
