"""Mirror ERPNext contacts, without destroying the ones Helpdesk owns.

Most contacts on this system did NOT come from ERPNext — 291 of them arrived
from WhatsApp, and plenty of others are sales staff deliberately kept out of
ERPNext. So the ownership boundary is the entire design:

  * a Contact carrying `erpnext_contact` is ERPNext-owned; the sync maintains it
  * a Contact WITHOUT it is Helpdesk-owned and is never touched, at all

Matching is by `erpnext_contact`, then by email scoped to the SAME customer.
Phone is deliberately not a match key: WhatsApp already claims contacts by
phone, and two systems matching on one key is how silent merges happen.
"""

import unittest
from unittest.mock import patch

import frappe

from helpdesk.integrations import erpnext_sync

PREFIX = "_test-erpcontact-"


def payload(customers):
    return {"ok": True, "error": None,
            "data": {"version": 1, "count": len(customers), "has_more": False,
                     "customers": customers}}


def customer_row(name, contacts):
    return {
        "customer": name, "customer_name": name, "tax_id": None, "territory": None,
        "customer_group": None, "currency": "KES", "modified": "2026-08-19 08:00:00",
        "contacts": contacts,
    }


def contact_row(name, full_name="Jane Doe", email=None, mobile=None, designation=None):
    return {
        "contact": name, "full_name": full_name, "email": email,
        "mobile_no": mobile, "phone": None, "designation": designation,
        "status": "Open",
        "emails": [{"email": email, "is_primary": 1}] if email else [],
        "phones": [{"phone": mobile, "is_primary_mobile": 1, "is_primary_phone": 0}] if mobile else [],
    }


def cleanup():
    for name in frappe.get_all(
        "Contact", filters={"first_name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("Contact", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "Contact", filters={"erpnext_contact": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("Contact", name, force=True, ignore_permissions=True)
    for name in frappe.get_all(
        "HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
    ):
        frappe.delete_doc("HD Customer", name, force=True, ignore_permissions=True)
    frappe.db.commit()


def links_for(contact):
    return frappe.get_all(
        "Dynamic Link",
        filters={"parent": contact, "parenttype": "Contact", "link_doctype": "HD Customer"},
        pluck="link_name",
    )


class _Base(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        cleanup()
        p = patch.object(erpnext_sync, "is_configured", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        cleanup()

    def run_sync(self, contacts, customer=PREFIX + "co"):
        row = customer_row(customer, contacts)
        with patch.object(erpnext_sync, "fetch_page", side_effect=[payload([row])]):
            return erpnext_sync.sync_customers()

    def local_contact(self, first_name, **kw):
        doc = frappe.get_doc(dict({"doctype": "Contact", "first_name": first_name}, **kw))
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc


class TestCreate(_Base):
    def test_creates_a_contact_and_links_it_to_the_customer(self):
        self.run_sync([contact_row(PREFIX + "c1", "Jane Doe", email="jane@acme.test")])

        name = frappe.db.get_value("Contact", {"erpnext_contact": PREFIX + "c1"}, "name")
        self.assertTrue(name)
        self.assertEqual(links_for(name), [PREFIX + "co"])

    def test_phone_is_written_through_the_child_table(self):
        """Contact.validate() overwrites a directly-set mobile_no, so the
        phone_nos row is the only thing that survives a save."""
        self.run_sync([contact_row(PREFIX + "c2", "Ann Smith", mobile="254700111222")])

        name = frappe.db.get_value("Contact", {"erpnext_contact": PREFIX + "c2"}, "name")
        phones = frappe.get_all("Contact Phone", filters={"parent": name}, pluck="phone")
        self.assertIn("254700111222", phones)


class TestMatching(_Base):
    def test_updates_a_contact_it_already_owns(self):
        self.run_sync([contact_row(PREFIX + "c3", "Old Name")])
        self.run_sync([contact_row(PREFIX + "c3", "New Name")])

        name = frappe.db.get_value("Contact", {"erpnext_contact": PREFIX + "c3"}, "name")
        self.assertEqual(frappe.db.get_value("Contact", name, "first_name"), "New")

    def test_does_not_hijack_a_contact_of_a_different_customer(self):
        """Same email, different company. Matching globally on email would
        silently reassign a real person to the wrong customer."""
        other = frappe.get_doc({
            "doctype": "HD Customer", "customer_name": PREFIX + "othercо"
        }).insert(ignore_permissions=True)
        stranger = self.local_contact(
            PREFIX + "stranger",
            erpnext_contact=PREFIX + "erp-stranger",
            email_ids=[{"email_id": "shared@acme.test", "is_primary": 1}],
            links=[{"link_doctype": "HD Customer", "link_name": other.name}],
        )

        self.run_sync([contact_row(PREFIX + "c4", "Someone Else", email="shared@acme.test")])

        self.assertEqual(
            frappe.db.get_value("Contact", stranger.name, "erpnext_contact"),
            PREFIX + "erp-stranger",
            "the other customer's contact must not be taken over",
        )


class TestOwnership(_Base):
    def test_never_touches_a_helpdesk_owned_contact(self):
        """A WhatsApp-created contact has no erpnext_contact marker. Even with a
        matching email under the same customer, the sync must leave it alone."""
        cust = frappe.get_doc({
            "doctype": "HD Customer", "customer_name": PREFIX + "co"
        }).insert(ignore_permissions=True)
        local = self.local_contact(
            PREFIX + "whatsapp-person",
            email_ids=[{"email_id": "local@acme.test", "is_primary": 1}],
            links=[{"link_doctype": "HD Customer", "link_name": cust.name}],
        )

        self.run_sync([contact_row(PREFIX + "c5", "Imposter Name", email="local@acme.test")])

        doc = frappe.get_doc("Contact", local.name)
        self.assertEqual(doc.first_name, PREFIX + "whatsapp-person")
        self.assertFalse(doc.get("erpnext_contact"))

    def test_existing_phone_survives_a_sync(self):
        """WhatsApp discovers numbers ERPNext does not have. Mirroring is
        additive on child tables so a sync cannot delete one."""
        self.run_sync([contact_row(PREFIX + "c6", "Ann Smith", mobile="254700111222")])
        name = frappe.db.get_value("Contact", {"erpnext_contact": PREFIX + "c6"}, "name")

        doc = frappe.get_doc("Contact", name)
        doc.append("phone_nos", {"phone": "254799888777"})
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        self.run_sync([contact_row(PREFIX + "c6", "Ann Smith", mobile="254700111222")])

        phones = frappe.get_all("Contact Phone", filters={"parent": name}, pluck="phone")
        self.assertIn("254799888777", phones)


class TestIdempotency(_Base):
    def test_second_run_creates_nothing_and_does_not_duplicate_the_link(self):
        row = contact_row(PREFIX + "c7", "Jane Doe", email="jane7@acme.test")
        self.run_sync([row])
        self.run_sync([row])

        matches = frappe.get_all("Contact", filters={"erpnext_contact": PREFIX + "c7"}, pluck="name")
        self.assertEqual(len(matches), 1)
        self.assertEqual(links_for(matches[0]), [PREFIX + "co"])
