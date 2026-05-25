import frappe
from frappe.model.document import Document


class WaLine(Document):
	def after_insert(self):
		self._configure_webhook()

	def on_update(self):
		self._configure_webhook()

	def _configure_webhook(self):
		try:
			from helpdesk.integrations.wa import configure_wa_webhook
			configure_wa_webhook(self.name)
		except Exception as e:
			frappe.log_error(f"WA webhook auto-configure failed for {self.name}: {e}", "WA Webhook")
