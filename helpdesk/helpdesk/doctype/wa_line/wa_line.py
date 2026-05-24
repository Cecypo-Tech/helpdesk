import frappe
from frappe.model.document import Document


class EvolutionLine(Document):
	def after_insert(self):
		self._configure_webhook()

	def on_update(self):
		self._configure_webhook()

	def _configure_webhook(self):
		try:
			from helpdesk.integrations.evolution import configure_evolution_webhook
			configure_evolution_webhook(self.name)
		except Exception as e:
			frappe.log_error(f"Evolution webhook auto-configure failed for {self.name}: {e}", "Evolution Webhook")
