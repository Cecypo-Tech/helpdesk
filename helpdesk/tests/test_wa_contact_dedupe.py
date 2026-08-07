import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.integrations import wa_contact_dedupe as dedupe

INTL = "254799"
LOCAL = "0799"


class TestWAContactDedupe(FrappeTestCase):
	"""Cover the duplicate-contact report and merge.

	Every scenario is constructed: this dev site has no contacts the WhatsApp
	automation created, so there is no real data to validate against. The rules
	are what these tests pin — especially the ones that must NOT merge.
	"""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		self.tag = frappe.generate_hash(length=6)
		# Distinctive digits so this run's numbers cannot collide with fixtures
		# or with another test's.
		self.digits = f"{int(frappe.generate_hash(length=8), 16) % 10**6:06d}"

	def _number(self, intl=True, seq=0):
		body = f"{self.digits}{seq}{seq}{seq}"
		return f"254{body}" if intl else f"0{body}"

	def _contact(self, phone, first_name=None, email=None, customer=None):
		data = {
			"doctype": "Contact",
			"first_name": first_name or f"Auto {self.tag}",
			"phone_nos": [{"doctype": "Contact Phone", "phone": phone, "is_primary_mobile_no": 1}],
		}
		if email:
			data["email_ids"] = [{"doctype": "Contact Email", "email_id": email, "is_primary": 1}]
		if customer:
			data["links"] = [{
				"doctype": "Dynamic Link", "link_doctype": "HD Customer", "link_name": customer,
			}]
		doc = frappe.get_doc(data).insert(ignore_permissions=True)
		self.addCleanup(self._drop_contact, doc.name)
		return doc

	def _drop_contact(self, name):
		if frappe.db.exists("Contact", name):
			frappe.delete_doc("Contact", name, ignore_permissions=True, force=True)

	def _customer(self, label=""):
		# HD Customer autonames from customer_name, so each one needs a distinct
		# label when a test makes more than one.
		doc = frappe.get_doc({
			"doctype": "HD Customer",
			"customer_name": f"Cust {self.tag}{label}",
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "HD Customer", doc.name, ignore_permissions=True, force=True
		)
		return doc

	def _ticket(self, contact):
		doc = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": f"Dedupe {self.tag}",
			"raised_by": f"dedupe.{self.tag}@example.com",
			"contact": contact,
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", doc.name, ignore_permissions=True, force=True
		)
		return doc

	def _mine(self, result, key="candidates"):
		"""Only the rows for this test's numbers — the site has unrelated data."""
		return [r for r in result[key] if self.digits in (r.get("suffix") or "")]

	def _bug_shaped_pair(self):
		"""The real scenario: a rich contact plus one the automation minted."""
		customer = self._customer()
		keeper = self._contact(
			self._number(intl=False),
			first_name=f"Real {self.tag}",
			email=f"real.{self.tag}@example.com",
			customer=customer.name,
		)
		dupe = self._contact(self._number(intl=True), first_name=f"WA Profile {self.tag}")
		return customer, keeper, dupe

	# ── report ────────────────────────────────────────────────────────────

	def test_bug_shaped_pair_is_reported_with_the_right_duplicate(self):
		_customer, keeper, dupe = self._bug_shaped_pair()

		found = self._mine(dedupe.report_duplicate_contacts(verbose=0))

		self.assertEqual(len(found), 1)
		self.assertEqual(found[0]["duplicate"], dupe.name)
		self.assertEqual(found[0]["keeper"], keeper.name)

	def test_report_changes_nothing(self):
		_customer, _keeper, dupe = self._bug_shaped_pair()
		ticket = self._ticket(dupe.name)

		dedupe.report_duplicate_contacts(verbose=0)

		self.assertTrue(frappe.db.exists("Contact", dupe.name))
		self.assertEqual(frappe.db.get_value("HD Ticket", ticket.name, "contact"), dupe.name)

	def test_group_of_three_is_skipped(self):
		for i in range(3):
			self._contact(self._number(intl=True), first_name=f"C{i} {self.tag}")
		result = dedupe.report_duplicate_contacts(verbose=0)

		self.assertEqual(self._mine(result), [])
		reasons = [s["reason"] for s in self._mine(result, "skipped")]
		self.assertIn("group_too_large", reasons)

	def test_pair_where_both_look_real_is_skipped(self):
		customer = self._customer()
		self._contact(
			self._number(intl=False), first_name=f"A {self.tag}",
			email=f"a.{self.tag}@example.com", customer=customer.name,
		)
		self._contact(
			self._number(intl=True), first_name=f"B {self.tag}",
			email=f"b.{self.tag}@example.com", customer=customer.name,
		)
		result = dedupe.report_duplicate_contacts(verbose=0)

		self.assertEqual(self._mine(result), [])
		self.assertIn("no_duplicate_shape", [s["reason"] for s in self._mine(result, "skipped")])

	def test_pair_of_two_bare_contacts_is_skipped_as_ambiguous(self):
		self._contact(self._number(intl=False), first_name=f"A {self.tag}")
		self._contact(self._number(intl=True), first_name=f"B {self.tag}")
		result = dedupe.report_duplicate_contacts(verbose=0)

		self.assertEqual(self._mine(result), [])
		self.assertIn("ambiguous_no_keeper", [s["reason"] for s in self._mine(result, "skipped")])

	def test_placeholder_numbers_are_never_candidates(self):
		# The shape that would otherwise collapse 162 unrelated fixtures.
		for i in range(3):
			self._contact("+91 0000000000", first_name=f"Placeholder {i} {self.tag}")
		result = dedupe.report_duplicate_contacts(verbose=0)

		placeholder = [c for c in result["candidates"] if len(set(c["suffix"])) < 4]
		self.assertEqual(placeholder, [])
		self.assertIn(
			"placeholder_number",
			[s["reason"] for s in result["skipped"] if s["suffix"] == "000000000"],
		)

	# ── merge ─────────────────────────────────────────────────────────────

	def test_dry_run_writes_nothing(self):
		_customer, keeper, dupe = self._bug_shaped_pair()
		ticket = self._ticket(dupe.name)

		result = dedupe.merge_duplicate_contacts(dry_run=1)

		mine = [r for r in result["applied"] if r["duplicate"] == dupe.name]
		self.assertEqual(len(mine), 1)
		self.assertTrue(mine[0]["dry_run"])
		self.assertTrue(frappe.db.exists("Contact", dupe.name))
		self.assertEqual(frappe.db.get_value("HD Ticket", ticket.name, "contact"), dupe.name)
		self.assertNotEqual(frappe.db.get_value("HD Ticket", ticket.name, "contact"), keeper.name)

	def test_merge_repoints_the_ticket_fills_customer_and_deletes_the_duplicate(self):
		customer, keeper, dupe = self._bug_shaped_pair()
		ticket = self._ticket(dupe.name)
		# The bug's signature: a ticket on the duplicate with no customer.
		frappe.db.set_value("HD Ticket", ticket.name, "customer", None, update_modified=False)

		dedupe.merge_duplicate_contacts(dry_run=0)

		row = frappe.db.get_value("HD Ticket", ticket.name, ["contact", "customer"], as_dict=True)
		self.assertEqual(row.contact, keeper.name)
		self.assertEqual(row.customer, customer.name)
		self.assertFalse(frappe.db.exists("Contact", dupe.name))

	def test_merge_leaves_an_existing_customer_alone(self):
		customer, keeper, dupe = self._bug_shaped_pair()
		other = self._customer(label=" Other")
		ticket = self._ticket(dupe.name)
		frappe.db.set_value("HD Ticket", ticket.name, "customer", other.name, update_modified=False)

		dedupe.merge_duplicate_contacts(dry_run=0)

		row = frappe.db.get_value("HD Ticket", ticket.name, ["contact", "customer"], as_dict=True)
		self.assertEqual(row.contact, keeper.name)
		self.assertEqual(row.customer, other.name, "merge overwrote a customer it did not set")
		self.assertNotEqual(row.customer, customer.name)

	# ── access ────────────────────────────────────────────────────────────

	def test_both_entry_points_require_system_manager(self):
		# Both are whitelisted so they can be called over HTTP on hosts with no
		# shell. merge deletes Contacts, and report enumerates every contact's
		# phone number, so neither may be reachable by an ordinary agent.
		user = f"dedupe.agent.{self.tag}@example.com"
		frappe.get_doc({
			"doctype": "User", "email": user, "first_name": "Dedupe Agent",
			"send_welcome_email": 0, "roles": [{"role": "Agent"}],
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "User", user, ignore_permissions=True, force=True)

		frappe.set_user(user)
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			dedupe.report_duplicate_contacts(verbose=0)
		with self.assertRaises(frappe.PermissionError):
			dedupe.merge_duplicate_contacts(dry_run=1)

	def test_merge_is_a_no_op_when_there_is_nothing_to_do(self):
		result = dedupe.merge_duplicate_contacts(dry_run=0)
		mine = [r for r in result["applied"] if self.digits in (r.get("suffix") or "")]
		self.assertEqual(mine, [])
