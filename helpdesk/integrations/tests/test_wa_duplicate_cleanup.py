"""Cleanup for the WhatsApp Messages Meta delivered twice before the guard landed."""

import unittest

import frappe

from helpdesk.integrations.wa_duplicate_cleanup import (
	merge_duplicate_messages,
	report_duplicate_messages,
)

PREFIX = "_test-cleanup-"


def _cleanup():
	frappe.db.sql(
		"DELETE FROM `tabWhatsApp Message` WHERE message_id LIKE %s", (PREFIX + "%",)
	)
	frappe.db.commit()


def _raw_insert(message_id, **kwargs):
	"""Insert past the hooks.

	The duplicate guard is installed on before_insert, so a normal insert can no
	longer create the rows this module exists to clean up.
	"""
	name = frappe.generate_hash(length=10)
	frappe.db.sql(
		"""INSERT INTO `tabWhatsApp Message`
		(`name`, `creation`, `modified`, `owner`, `modified_by`,
		 `message_id`, `type`, `from`, `message`, `content_type`,
		 `reference_doctype`, `reference_name`)
		VALUES (%(name)s, %(creation)s, %(creation)s, 'Administrator', 'Administrator',
		        %(message_id)s, 'Incoming', '254700111222', 'hi', 'text',
		        %(reference_doctype)s, %(reference_name)s)""",
		{
			"name": name,
			"creation": kwargs.get("creation", "2026-08-01 10:00:00"),
			"message_id": message_id,
			"reference_doctype": kwargs.get("reference_doctype"),
			"reference_name": kwargs.get("reference_name"),
		},
	)
	frappe.db.commit()
	return name


def _names(message_id):
	return frappe.get_all(
		"WhatsApp Message", filters={"message_id": message_id}, pluck="name"
	)


class TestWaDuplicateCleanup(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		_cleanup()

	@classmethod
	def tearDownClass(cls):
		_cleanup()

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		_cleanup()

	def test_report_counts_groups_without_writing(self):
		wamid = PREFIX + "report"
		_raw_insert(wamid, creation="2026-08-01 10:00:00")
		_raw_insert(wamid, creation="2026-08-01 10:00:01")

		result = report_duplicate_messages()

		self.assertGreaterEqual(result["groups"], 1)
		self.assertGreaterEqual(result["rows_to_remove"], 1)
		self.assertEqual(len(_names(wamid)), 2)

	def test_dry_run_writes_nothing(self):
		wamid = PREFIX + "dry"
		_raw_insert(wamid, creation="2026-08-01 10:00:00")
		_raw_insert(wamid, creation="2026-08-01 10:00:01")

		result = merge_duplicate_messages()

		self.assertTrue(result["dry_run"])
		self.assertGreaterEqual(result["removed"], 1)
		self.assertEqual(len(_names(wamid)), 2)

	def test_merge_keeps_the_earliest_row(self):
		wamid = PREFIX + "earliest"
		first = _raw_insert(wamid, creation="2026-08-01 10:00:00")
		_raw_insert(wamid, creation="2026-08-01 10:00:01")

		merge_duplicate_messages(dry_run=0)

		self.assertEqual(_names(wamid), [first])

	def test_linked_row_survives_an_older_unlinked_one(self):
		# on_whatsapp_message_insert returns early for a duplicate, so whichever
		# delivery won the race is the one carrying the ticket. Dropping it in
		# favour of an older orphan would unlink the message from its ticket.
		wamid = PREFIX + "linked"
		_raw_insert(wamid, creation="2026-08-01 10:00:00")
		linked = _raw_insert(
			wamid,
			creation="2026-08-01 10:00:01",
			reference_doctype="HD Ticket",
			reference_name="99999",
		)

		merge_duplicate_messages(dry_run=0)

		self.assertEqual(_names(wamid), [linked])

	def test_unique_message_is_untouched(self):
		wamid = PREFIX + "unique"
		only = _raw_insert(wamid)

		merge_duplicate_messages(dry_run=0)

		self.assertEqual(_names(wamid), [only])

	def test_limit_caps_the_removals(self):
		for suffix in ("l1", "l2", "l3"):
			wamid = PREFIX + suffix
			_raw_insert(wamid, creation="2026-08-01 10:00:00")
			_raw_insert(wamid, creation="2026-08-01 10:00:01")

		result = merge_duplicate_messages(dry_run=0, limit=1)

		self.assertEqual(result["removed"], 1)
