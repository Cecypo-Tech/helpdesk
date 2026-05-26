import frappe
from frappe.model.document import Document


class WALine(Document):
	def after_insert(self):
		self._configure_webhook()

	def on_update(self):
		self._configure_webhook()

	def on_trash(self):
		"""Cascade-delete all WA Messages linked to this line before Frappe's link check runs."""
		msgs = frappe.get_all("WA Message", filters={"line": self.name}, pluck="name", limit=0)
		for msg_name in msgs:
			frappe.delete_doc("WA Message", msg_name, force=True, ignore_permissions=True)
		if msgs:
			frappe.db.commit()

	def _configure_webhook(self):
		try:
			from helpdesk.integrations.wa import configure_wa_webhook
			configure_wa_webhook(self.name)
		except Exception as e:
			frappe.log_error(f"WA webhook auto-configure failed for {self.name}: {e}", "WA Webhook")
