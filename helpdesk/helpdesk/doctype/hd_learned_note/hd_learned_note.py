import frappe
from frappe.model.document import Document


class HDLearnedNote(Document):
	def on_update(self):
		"""Saving on the form is a person's decision, the same as a vote.

		Hourly vote handling writes with db.set_value, so it never comes through here.
		"""
		before = self.get_doc_before_save()
		was = before.status if before else None
		from helpdesk.integrations import learn

		if was == "Approved" and self.status != "Approved":
			learn.unlearn(self.ticket)
		elif self.status == "Approved" and (
			was != "Approved" or before.problem != self.problem or before.fix != self.fix
		):
			learn.approve(self.name)
		if was == "Pending" and self.status != "Pending" and self.poll_id:
			learn.close_poll(self.poll_id)
