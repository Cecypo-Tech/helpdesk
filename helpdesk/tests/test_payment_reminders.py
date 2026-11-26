"""Payment reminders: who qualifies, when to send, and what a reply means."""

import unittest
from datetime import date, datetime

import frappe

from helpdesk.integrations import payment_reminders as pr

TODAY = date(2026, 11, 26)  # a Thursday


def S(**k):
	base = dict(min_invoice_age_days=7, min_gap_days=2, followup_days=7, max_unanswered=3)
	base.update(k)
	return frappe._dict(base)


class TestQualifying(unittest.TestCase):
	def inv(self, posted, due, outstanding=100):
		return {"name": "X", "posting_date": str(posted), "due_date": str(due), "outstanding_amount": outstanding}

	def test_cash_sale_qualifies_at_seven_days(self):
		rows = [self.inv(date(2026, 11, 19), date(2026, 11, 19))]
		self.assertEqual(len(pr.qualifying(rows, TODAY, 7)), 1)

	def test_cash_sale_six_days_old_does_not(self):
		rows = [self.inv(date(2026, 11, 20), date(2026, 11, 20))]
		self.assertEqual(pr.qualifying(rows, TODAY, 7), [])

	def test_credit_invoice_not_yet_due_does_not(self):
		rows = [self.inv(date(2026, 11, 1), date(2026, 12, 1))]
		self.assertEqual(pr.qualifying(rows, TODAY, 7), [])

	def test_due_today_is_not_past_due(self):
		rows = [self.inv(date(2026, 11, 1), TODAY)]
		self.assertEqual(pr.qualifying(rows, TODAY, 7), [])

	def test_paid_invoice_does_not(self):
		rows = [self.inv(date(2026, 11, 1), date(2026, 11, 8), outstanding=0)]
		self.assertEqual(pr.qualifying(rows, TODAY, 7), [])


class TestNextAction(unittest.TestCase):
	def c(self, **k):
		base = dict(status="Draft", reminders_sent=0, unanswered=0, last_sent_on=None, promise_date=None)
		base.update(k)
		return frappe._dict(base)

	def test_first_reminder_is_due(self):
		self.assertEqual(pr.next_action(self.c(), TODAY, S()), "due")

	def test_followup_waits_seven_days(self):
		c = self.c(status="Sent", reminders_sent=1, unanswered=1, last_sent_on=datetime(2026, 11, 20, 9))
		self.assertEqual(pr.next_action(c, TODAY, S()), "wait")
		c.last_sent_on = datetime(2026, 11, 19, 9)
		self.assertEqual(pr.next_action(c, TODAY, S()), "due")

	def test_promise_waits_until_two_days_after(self):
		c = self.c(status="Promised", reminders_sent=1, last_sent_on=datetime(2026, 11, 10, 9),
		           promise_date=date(2026, 11, 25))
		self.assertEqual(pr.next_action(c, TODAY, S()), "wait")
		c.promise_date = date(2026, 11, 24)
		self.assertEqual(pr.next_action(c, TODAY, S()), "due")

	def test_gap_holds_even_when_promise_has_passed(self):
		c = self.c(status="Promised", reminders_sent=1, last_sent_on=datetime(2026, 11, 25, 9),
		           promise_date=date(2026, 11, 20))
		self.assertEqual(pr.next_action(c, TODAY, S()), "wait")

	def test_escalates_after_max_unanswered(self):
		c = self.c(status="Sent", reminders_sent=3, unanswered=3, last_sent_on=datetime(2026, 11, 1, 9))
		self.assertEqual(pr.next_action(c, TODAY, S()), "escalate")


class TestBusinessHours(unittest.TestCase):
	def test_weekday_morning(self):
		self.assertTrue(pr.in_business_hours(datetime(2026, 11, 26, 8, 0)))

	def test_evening(self):
		self.assertFalse(pr.in_business_hours(datetime(2026, 11, 26, 18, 0)))

	def test_saturday_ok_sunday_not(self):
		self.assertTrue(pr.in_business_hours(datetime(2026, 11, 28, 10)))
		self.assertFalse(pr.in_business_hours(datetime(2026, 11, 29, 10)))


class TestParseClassification(unittest.TestCase):
	def test_promise_with_date(self):
		out = pr.parse_classification('{"class": "promise", "date": "2026-11-27"}', TODAY)
		self.assertEqual(out, {"class": "promise", "date": date(2026, 11, 27)})

	def test_json_inside_code_fence(self):
		out = pr.parse_classification('```json\n{"class": "paid", "date": null}\n```', TODAY)
		self.assertEqual(out["class"], "paid")

	def test_promise_in_the_past_is_other(self):
		out = pr.parse_classification('{"class": "promise", "date": "2026-11-01"}', TODAY)
		self.assertEqual(out["class"], "other")

	def test_promise_without_date_is_other(self):
		self.assertEqual(pr.parse_classification('{"class": "promise"}', TODAY)["class"], "other")

	def test_garbage_is_other(self):
		self.assertEqual(pr.parse_classification("sure, they will pay", TODAY)["class"], "other")

	def test_unknown_class_is_other(self):
		self.assertEqual(pr.parse_classification('{"class": "angry"}', TODAY)["class"], "other")
