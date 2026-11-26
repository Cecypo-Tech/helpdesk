"""Remember who a contact is once an agent says so on a ticket.

Only a contact with no customer link is linked: an office handset can serve
several customers, so an existing link is never added to or replaced.

Agents only. A portal customer can write `customer` on their own ticket, and
a link made from that would grant them every ticket of the customer they named.
"""

from helpdesk.utils import get_customer, is_agent


def link_contact_on_customer_set(doc, method=None) -> None:
	if not is_agent():
		return
	if not (doc.customer and doc.contact and doc.has_value_changed("customer")):
		return
	if get_customer(doc.contact):
		return

	from helpdesk.api.verification import link_contact_to_customer

	link_contact_to_customer(doc.contact, doc.customer)
