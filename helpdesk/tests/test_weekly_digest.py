"""Monday digest, the work-type guess sweep and the unhappy check."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import frappe

from helpdesk.integrations import weekly_digest as wd
from helpdesk.patches import add_work_types
from helpdesk.test_utils import make_ticket

MONDAY = datetime(2026, 11, 30, 8, 0)
LAST_WED = datetime(2026, 11, 25, 12, 0)


class TestParsers(unittest.TestCase):
	def test_parse_work_type(self):
		self.assertEqual(wd.parse_work_type(" printer "), "Printer")
		self.assertEqual(wd.parse_work_type("Template change."), "Template change")
		self.assertIsNone(wd.parse_work_type("Hardware"))
		self.assertIsNone(wd.parse_work_type(""))

	def test_parse_unhappy_bad_json(self):
		self.assertEqual(wd.parse_unhappy("not json"), (False, ""))
		self.assertEqual(wd.parse_unhappy('{"unhappy": true, "reason": "Third outage"}'), (True, "Third outage"))
		self.assertEqual(wd.parse_unhappy('```json\n{"unhappy": false, "reason": ""}\n```'), (False, ""))


class _DB(unittest.TestCase):
	def setUp(self):
		p = patch.object(frappe.db, "commit")  # keep test rows inside the rolled-back transaction
		p.start()
		self.addCleanup(p.stop)
		add_work_types.execute()
		frappe.db.delete("HD Ticket")  # rolled back in tearDown
		frappe.db.delete("HD Payment Reminder")

	def tearDown(self):
		frappe.db.rollback()

	def ticket(self, subject="Printer offline", **db):
		t = make_ticket(subject=subject)
		if db.get("status"):
			db.setdefault("status_category", frappe.db.get_value("HD Ticket Status", db["status"], "category"))
		if db:
			frappe.db.set_value("HD Ticket", t.name, db, update_modified=False)
		return t.name

	def cust(self):
		name = "ACME Digest Test"
		if not frappe.db.exists("HD Customer", name):
			frappe.get_doc({"doctype": "HD Customer", "customer_name": name}).insert(ignore_permissions=True)
		return name

	def reminder(self, **k):
		return frappe.get_doc({"doctype": "HD Payment Reminder", "hd_customer": self.cust(), **k}).insert(
			ignore_permissions=True
		).name


class TestGuess(_DB):
	def test_guess_sets_type_once(self):
		old = datetime.now() - timedelta(hours=2)
		t = self.ticket(creation=old)
		with patch.object(wd, "_llm", return_value="Printer") as llm:
			self.assertEqual(wd.guess_work_types(), 1)
			wd.guess_work_types()
		self.assertEqual(llm.call_count, 1)
		self.assertEqual(frappe.db.get_value("HD Ticket", t, ["ticket_type", "work_type_guessed"]), ("Printer", 1))

	def test_guess_skips_set_and_reminder_tickets(self):
		old = datetime.now() - timedelta(hours=2)
		typed = self.ticket(creation=old, ticket_type="Network")
		rem = self.ticket(creation=old)
		self.reminder(status="Sent", ticket=rem)
		fresh = self.ticket()  # under an hour old
		with patch.object(wd, "_llm", return_value="Printer") as llm:
			self.assertEqual(wd.guess_work_types(), 0)
		llm.assert_not_called()
		self.assertEqual(frappe.db.get_value("HD Ticket", typed, "ticket_type"), "Network")
		self.assertNotEqual(frappe.db.get_value("HD Ticket", rem, "ticket_type"), "Printer")
		self.assertNotEqual(frappe.db.get_value("HD Ticket", fresh, "ticket_type"), "Printer")

	def test_bad_guess_marks_tried(self):
		t = self.ticket(creation=datetime.now() - timedelta(hours=2))
		with patch.object(wd, "_llm", return_value="no idea"):
			wd.guess_work_types()
		self.assertEqual(frappe.db.get_value("HD Ticket", t, "work_type_guessed"), 1)
		self.assertNotIn(frappe.db.get_value("HD Ticket", t, "ticket_type"), wd.WORK_TYPES)


class TestReviewFixes(_DB):
	def test_guess_llm_error_retries_later(self):
		t = self.ticket(creation=datetime.now() - timedelta(hours=2))
		with patch.object(wd, "_llm", side_effect=RuntimeError("provider down")):
			wd.guess_work_types()
		self.assertEqual(frappe.db.get_value("HD Ticket", t, "work_type_guessed"), 0)

	def test_closed_on_window_ignores_later_edits(self):
		old = self.ticket(status="Closed", modified=LAST_WED - timedelta(days=21))
		wd.stamp_closed_on()
		frappe.db.set_value("HD Ticket", old, "modified", LAST_WED, update_modified=False)  # touched this week
		new = self.ticket(status="Resolved", modified=LAST_WED)
		wd.stamp_closed_on()
		self.assertEqual(wd._closed_between(MONDAY - timedelta(days=7), MONDAY), [str(new)])

	def test_reopen_clears_closed_on(self):
		t = self.ticket(status="Closed", modified=LAST_WED)
		wd.stamp_closed_on()
		frappe.db.set_value("HD Ticket", t, {"status": "Open", "status_category": "Open"})
		wd.stamp_closed_on()
		self.assertIsNone(frappe.db.get_value("HD Ticket", t, "closed_on"))

	def test_send_runs_on_long_queue(self):
		with patch.object(wd.frappe, "enqueue") as enq:
			wd.send()
		self.assertEqual(enq.call_args.kwargs["queue"], "long")


class TestUnhappy(_DB):
	def test_unhappy_flags_and_writes_reason(self):
		t = self.ticket(status="Resolved", resolution_date=LAST_WED)
		ok = self.ticket(subject="Template tweak", status="Resolved", resolution_date=LAST_WED)
		replies = {t: '{"unhappy": true, "reason": "Third time this month"}', ok: '{"unhappy": false}'}
		with patch.object(wd, "_llm", side_effect=lambda msgs: next(v for k, v in replies.items() if f"#{k}" in msgs[-1]["content"])):
			wd.stamp_closed_on()
			flagged = wd.check_unhappy(MONDAY - timedelta(days=7), MONDAY)
		self.assertEqual(flagged, [str(t)])
		self.assertEqual(frappe.db.get_value("HD Ticket", t, ["unhappy", "unhappy_reason"]), (1, "Third time this month"))
		self.assertEqual(frappe.db.get_value("HD Ticket", ok, "unhappy"), 0)

	def test_unhappy_covers_db_set_value_close(self):
		t = self.ticket(status="Closed", modified=LAST_WED)  # no resolution_date, like wa._set_ticket_status
		wd.stamp_closed_on()
		with patch.object(wd, "_llm", return_value='{"unhappy": true, "reason": "Angry"}'):
			self.assertEqual(wd.check_unhappy(MONDAY - timedelta(days=7), MONDAY), [str(t)])


class TestDigest(_DB):
	def build(self, overdue=150000.0):
		wd.stamp_closed_on()
		with patch.object(wd, "overdue_total", return_value=overdue):
			return wd.build(MONDAY)

	def test_digest_sections(self):
		charged = self.ticket(subject="Router setup", status="Resolved", resolution_date=LAST_WED,
		                      ticket_type="Network", charge="Charge")
		waived = self.ticket(subject="Toner swap", status="Resolved", resolution_date=LAST_WED,
		                     ticket_type="Printer", charge="Waive")
		old = self.ticket(subject="Slow POS", opening_date=MONDAY.date() - timedelta(days=5))
		sad = self.ticket(subject="Outage again", status="Closed", resolution_date=LAST_WED, unhappy=1,
		                  unhappy_reason="Third outage")
		promised = self.reminder(status="Promised", promise_date=MONDAY.date() + timedelta(days=3), total=8000)
		held = self.reminder(status="Held", hold_reason="Unallocated payment on ERPNext – check before chasing",
		                     advance_amount=2500)
		text = self.build()
		self.assertIn("150,000", text)
		for name in (charged, old, sad):
			self.assertIn(f"/helpdesk/tickets/{name}", text)
		self.assertNotIn(f"/helpdesk/tickets/{waived})", text)
		self.assertIn(f"/app/hd-payment-reminder/{promised}", text)
		self.assertIn("8,000", text)
		self.assertIn(f"/app/hd-payment-reminder/{held}", text)
		self.assertIn("2,500", text)
		self.assertIn("Third outage", text)
		order = [text.index(h) for h in ("Overdue", "Promises", "Unallocated", "Chargeable", "Open", "Unhappy")]
		self.assertEqual(order, sorted(order))

	def test_digest_excludes_paused_and_reminder_tickets_from_open(self):
		long_ago = MONDAY.date() - timedelta(days=10)
		paused = self.ticket(subject="Waiting on parts", opening_date=long_ago, status="Paused")
		rem = self.ticket(subject="Payment reminder", opening_date=long_ago)
		self.reminder(status="Sent", ticket=rem)
		text = self.build()
		self.assertNotIn(f"/helpdesk/tickets/{paused})", text)
		self.assertNotIn(f"/helpdesk/tickets/{rem})", text)

	def test_overdue_unreachable_says_so(self):
		with patch.object(wd.erpnext_remote, "call", return_value={"ok": False, "data": None, "error": "down"}):
			self.assertIsNone(wd.overdue_total())
		self.assertIn("Couldn't read ERPNext", self.build(overdue=None))
		self.assertNotIn("KES 0", self.build(overdue=None))

	def test_overdue_sums_rows(self):
		rows = [{"base_outstanding_amount": 100.5}, {"base_outstanding_amount": 200}]
		with patch.object(wd.erpnext_remote, "call", return_value={"ok": True, "data": rows, "error": None}) as c:
			self.assertEqual(wd.overdue_total(), 300.5)
		self.assertEqual(c.call_args[0][0], "frappe.client.get_list")

	def test_quiet_week_line(self):
		self.assertIn("Quiet week", self.build(overdue=0.0))

	def test_send_disabled_does_nothing(self):
		frappe.db.set_single_value("HD Weekly Digest Settings", {"enabled": 0, "raven_channel": "erp-support-actions"})
		with patch.object(wd.raven_bridge, "post") as post:
			wd.send_now(MONDAY)
		post.assert_not_called()

	def test_send_posts_to_digest_channel(self):
		frappe.db.set_single_value("HD Weekly Digest Settings",
		                           {"enabled": 1, "raven_channel": "erp-support-actions", "raven_user": "Administrator"})
		frappe.clear_document_cache("HD Weekly Digest Settings")
		with patch.object(wd.raven_bridge, "post") as post, patch.object(wd, "overdue_total", return_value=0.0), \
				patch.object(wd, "_llm", return_value='{"unhappy": false}'):
			wd.send_now(MONDAY)
		post.assert_called_once()
		self.assertEqual(post.call_args.kwargs, {"channel": "erp-support-actions", "user": "Administrator"})


class TestRavenChannel(unittest.TestCase):
	def test_raven_post_uses_given_channel(self):
		from helpdesk.integrations import raven_bridge

		with patch.object(raven_bridge, "available", return_value=True), \
				patch.object(raven_bridge.frappe, "call") as call:
			raven_bridge.post("hi", channel="erp-support-actions", user="Administrator")
		self.assertEqual(call.call_args.kwargs["channel_id"], "erp-support-actions")
