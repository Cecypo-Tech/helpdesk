"""support_status is stamped once and then frozen.

Stamping only at creation would be wrong: WhatsApp tickets are created
automatically on the first inbound message, long before an agent tags a
product, so a creation-only rule would leave support_status = Unknown
permanently on the channel this work started from.
"""

import unittest

import frappe
from frappe.utils import add_days, today

PREFIX = "_test-stamp-"
CUSTOMER = PREFIX + "cust"
PRODUCT = PREFIX + "prod"
OTHER = PREFIX + "other"


def cleanup():
	for name in frappe.get_all(
		"HD Ticket", filters={"subject": ["like", PREFIX + "%"]}, pluck="name"
	):
		frappe.delete_doc("HD Ticket", name, force=True, ignore_permissions=True,
						  delete_permanently=True)
	for name in frappe.get_all(
		"HD Customer Product", filters={"customer": CUSTOMER}, pluck="name"
	):
		frappe.delete_doc("HD Customer Product", name, force=True, ignore_permissions=True)
	for name in frappe.get_all(
		"HD Product", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
	):
		frappe.delete_doc("HD Product", name, force=True, ignore_permissions=True)
	for name in frappe.get_all(
		"HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
	):
		frappe.delete_doc("HD Customer", name, force=True, ignore_permissions=True)
	frappe.db.commit()


class TestSupportStatusStamp(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		cleanup()
		frappe.get_doc({
			"doctype": "HD Customer", "name": CUSTOMER, "customer_name": CUSTOMER
		}).insert(ignore_permissions=True)
		for p in (PRODUCT, OTHER):
			frappe.get_doc({"doctype": "HD Product", "product_name": p}).insert(
				ignore_permissions=True
			)
		frappe.db.commit()

	def tearDown(self):
		cleanup()

	def entitle(self, product, expiry=None):
		frappe.get_doc({
			"doctype": "HD Customer Product",
			"customer": CUSTOMER,
			"product": product,
			"support_expiry": expiry,
		}).insert(ignore_permissions=True)
		frappe.db.commit()

	def ticket(self, **kwargs):
		payload = {
			"doctype": "HD Ticket",
			"subject": PREFIX + "t",
			"description": "x",
			"customer": CUSTOMER,
		}
		payload.update(kwargs)
		doc = frappe.get_doc(payload).insert(ignore_permissions=True)
		frappe.db.commit()
		return doc

	def test_stamped_at_creation_when_product_is_known(self):
		self.entitle(PRODUCT)
		doc = self.ticket(hd_product=PRODUCT)
		self.assertEqual(doc.support_status, "Covered")

	def test_expired_entitlement_stamps_expired(self):
		self.entitle(PRODUCT, add_days(today(), -1))
		doc = self.ticket(hd_product=PRODUCT)
		self.assertEqual(doc.support_status, "Expired")

	def test_unentitled_product_stamps_not_entitled(self):
		self.entitle(PRODUCT)
		doc = self.ticket(hd_product=OTHER)
		self.assertEqual(doc.support_status, "Not Entitled")

	def test_not_stamped_while_no_product_is_known(self):
		"""The WhatsApp case: the ticket exists before anyone tags a product."""
		doc = self.ticket()
		self.assertFalse(doc.support_status)

	def test_stamped_when_product_is_set_later(self):
		"""The empty -> set transition. Without this the WhatsApp channel would
		never get a status at all."""
		self.entitle(PRODUCT)
		doc = self.ticket()
		self.assertFalse(doc.support_status)

		doc.reload()
		doc.hd_product = PRODUCT
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		doc.reload()
		self.assertEqual(doc.support_status, "Covered")

	def test_frozen_when_entitlement_is_renewed(self):
		"""The whole point of storing it: renewing must not rewrite what the
		status was when the customer asked."""
		self.entitle(PRODUCT, add_days(today(), -1))
		doc = self.ticket(hd_product=PRODUCT)
		self.assertEqual(doc.support_status, "Expired")

		row = frappe.get_all(
			"HD Customer Product",
			filters={"customer": CUSTOMER, "product": PRODUCT},
			pluck="name",
		)[0]
		frappe.db.set_value("HD Customer Product", row, "support_expiry", add_days(today(), 30))
		frappe.db.commit()

		doc.reload()
		doc.subject = PREFIX + "t edited"
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		doc.reload()
		self.assertEqual(doc.support_status, "Expired")

	def test_frozen_when_the_product_is_changed(self):
		self.entitle(PRODUCT)
		doc = self.ticket(hd_product=OTHER)
		self.assertEqual(doc.support_status, "Not Entitled")

		doc.reload()
		doc.hd_product = PRODUCT
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		doc.reload()
		self.assertEqual(doc.support_status, "Not Entitled")

	def test_unentitled_product_is_accepted_not_rejected(self):
		"""Pre-sales, evaluations and stale mirror data are all legitimate
		reasons to pick a product the customer does not own. The constraint is
		a UI default, never server-side validation."""
		doc = self.ticket(hd_product=OTHER)
		self.assertTrue(doc.name)
		self.assertEqual(doc.support_status, "Not Entitled")
