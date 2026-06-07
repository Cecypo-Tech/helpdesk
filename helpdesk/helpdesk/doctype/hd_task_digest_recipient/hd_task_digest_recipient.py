import frappe
from frappe.model.document import Document


class HDTaskDigestRecipient(Document):
	def validate(self):
		if not self.agent and not self.phone:
			frappe.throw(frappe._("Each digest recipient must have an Agent or a Phone number."))
