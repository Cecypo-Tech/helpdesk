import frappe
from frappe.model.document import Document


class HDArticleEmbedding(Document):
	def after_insert(self):
		# Invalidate the in-memory embedding cache so the next bot query picks this up.
		try:
			from helpdesk.integrations.embeddings import invalidate_article_cache
			invalidate_article_cache()
		except Exception:
			pass

	def on_trash(self):
		try:
			from helpdesk.integrations.embeddings import invalidate_article_cache
			invalidate_article_cache()
		except Exception:
			pass
