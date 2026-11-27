import frappe
from frappe.model.document import Document


class HDLearnedNote(Document):
	def on_update(self):
		# Undo: an approved note set back to Skipped stops feeding the bot.
		if self.status == "Skipped" and self.has_value_changed("status"):
			if frappe.db.exists("HD Ticket Embedding", {"ticket": self.ticket}):
				frappe.db.delete("HD Ticket Embedding", {"ticket": self.ticket})
				from helpdesk.integrations.embeddings import invalidate_cache

				invalidate_cache()
