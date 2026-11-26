"""Payment reminders: who qualifies, when to send, and what a reply means."""

import unittest
from datetime import date, datetime, timedelta

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


# ── Cycles: building, polling, sending ────────────────────────────────────────

from unittest.mock import patch

PFX = "_PR "
THURSDAY_10 = datetime(2026, 11, 26, 10, 0)
APPROVER = "administrator"


def snap_ok(invoices=None, advances=0):
	if invoices is None:
		invoices = [{"name": "SINV-1", "posting_date": "2026-11-01", "due_date": "2026-11-08",
		             "outstanding_amount": 12000}]
	return {"status": "ok", "snapshot": {"invoices": invoices, "advances_total": advances}}


class _CycleBase(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.cleanup()
		self.addCleanup(self.cleanup)
		s = frappe.get_single(pr.SETTINGS)
		self._saved = s.as_dict()
		s.update(dict(enabled=1, auto_send=0, min_invoice_age_days=7, min_gap_days=2, followup_days=7,
		              max_unanswered=3, template_reminder=None, template_nudge=None, template_thanks=None,
		              raven_channel="pr-approvals", approver="Administrator"))
		s.save(ignore_permissions=True)
		self.addCleanup(self.restore_settings)

		self.hd = frappe.get_doc({"doctype": "HD Customer", "customer_name": PFX + "Cust"}).insert(
			ignore_permissions=True).name
		frappe.db.set_value("HD Customer", self.hd, "erpnext_customer", PFX + "ERP")
		self.contact = self.make_contact("billing", "254711000111", billing=1)

		self.sent, self.polls, self.posts, self.vote = [], [], [], {}
		for target, fn in [
			("_snapshot", lambda erp: self.snapshot),
			("_send_template", lambda ticket, tpl, params: self.sent.append(("template", ticket, params))),
			("_send_text", lambda ticket, text: self.sent.append(("text", ticket, text))),
		]:
			p = patch.object(pr, target, side_effect=fn)
			p.start()
			self.addCleanup(p.stop)
		self.snapshot = snap_ok()
		for target, fn in [
			("create_poll", lambda q: (self.polls.append(q) or ("poll-1", "msg-1"))),
			("votes", lambda m: self.vote),
			("close_poll", lambda p: None),
			("post", lambda t: self.posts.append(t)),
		]:
			p = patch.object(pr.raven_bridge, target, side_effect=fn)
			p.start()
			self.addCleanup(p.stop)

	def restore_settings(self):
		s = frappe.get_single(pr.SETTINGS)
		s.update({k: v for k, v in self._saved.items() if k in s.as_dict() and not k.startswith("_")
		          and k not in ("name", "doctype", "modified", "creation")})
		s.save(ignore_permissions=True)

	def make_contact(self, first, phone, billing=0):
		c = frappe.get_doc({
			"doctype": "Contact", "first_name": PFX + first, "is_billing_contact": billing,
			"phone_nos": [{"phone": phone, "is_primary_mobile_no": 1}],
			"links": [{"link_doctype": "HD Customer", "link_name": self.hd}],
		}).insert(ignore_permissions=True)
		return c.name

	def cleanup(self):
		for r in frappe.get_all(pr.DOCTYPE, filters={"hd_customer": ["like", PFX + "%"]}, pluck="name"):
			frappe.delete_doc(pr.DOCTYPE, r, force=True, ignore_permissions=True)
		for t in frappe.get_all("HD Ticket", filters={"subject": ["like", "%" + PFX + "%"]}, pluck="name"):
			frappe.delete_doc("HD Ticket", t, force=True, ignore_permissions=True, delete_permanently=True)
		for c in frappe.get_all("Contact", filters={"first_name": ["like", PFX + "%"]}, pluck="name"):
			frappe.delete_doc("Contact", c, force=True, ignore_permissions=True, delete_permanently=True)
		for c in frappe.get_all("HD Customer", filters={"name": ["like", PFX + "%"]}, pluck="name"):
			frappe.delete_doc("HD Customer", c, force=True, ignore_permissions=True)
		frappe.db.commit()

	def cycle(self):
		name = frappe.db.get_value(pr.DOCTYPE, {"hd_customer": self.hd}, "name", order_by="creation desc")
		return frappe.get_doc(pr.DOCTYPE, name) if name else None


class TestCycles(_CycleBase):
	def test_no_billing_contact_no_cycle(self):
		frappe.db.set_value("Contact", self.contact, "is_billing_contact", 0)
		pr.run(THURSDAY_10)
		self.assertIsNone(self.cycle())

	def test_advances_hold_cycle(self):
		self.snapshot = snap_ok(advances=500)
		pr.run(THURSDAY_10)
		self.assertEqual(self.cycle().status, "Held")
		self.assertEqual(self.polls, [])

	def test_poll_then_send_on_approval(self):
		pr.run(THURSDAY_10)
		c = self.cycle()
		self.assertEqual(c.status, "Awaiting Approval")
		self.assertEqual(len(self.polls), 1)
		self.assertIn("12,000", self.polls[0])
		self.vote = {"Send": [APPROVER]}
		pr.run(THURSDAY_10 + timedelta(hours=1))
		c.reload()
		self.assertEqual(c.status, "Sent")
		self.assertEqual(c.reminders_sent, 1)
		self.assertTrue(c.ticket)
		self.assertEqual(self.sent[0][0], "template")

	def test_vote_from_someone_else_is_ignored(self):
		pr.run(THURSDAY_10)
		self.vote = {"Send": ["someone@example.com"]}
		pr.run(THURSDAY_10 + timedelta(hours=1))
		self.assertEqual(self.cycle().status, "Awaiting Approval")
		self.assertEqual(self.sent, [])

	def test_skip_vote_does_not_send(self):
		pr.run(THURSDAY_10)
		self.vote = {"Skip": [APPROVER]}
		pr.run(THURSDAY_10 + timedelta(hours=1))
		pr.run(THURSDAY_10 + timedelta(hours=2))
		self.assertEqual(self.sent, [])
		self.assertEqual(len(self.polls), 1)  # not re-polled the same day
		self.assertEqual(self.cycle().status, "Draft")

	def test_paid_after_poll_sends_nothing_and_closes(self):
		pr.run(THURSDAY_10)
		self.snapshot = snap_ok(invoices=[])
		self.vote = {"Send": [APPROVER]}
		pr.run(THURSDAY_10 + timedelta(hours=1))
		self.assertEqual(self.sent, [])
		self.assertEqual(self.cycle().status, "Paid")

	def test_paid_after_reminder_sends_thanks(self):
		pr.run(THURSDAY_10)
		self.vote = {"Send": [APPROVER]}
		pr.run(THURSDAY_10 + timedelta(hours=1))
		self.snapshot = snap_ok(invoices=[])
		pr.run(THURSDAY_10 + timedelta(hours=2))
		self.assertEqual(self.cycle().status, "Paid")
		self.assertEqual(len(self.sent), 2)  # reminder, then thanks

	def test_unavailable_snapshot_changes_nothing(self):
		pr.run(THURSDAY_10)
		self.snapshot = {"status": "unavailable", "snapshot": None}
		self.vote = {"Send": [APPROVER]}
		pr.run(THURSDAY_10 + timedelta(hours=1))
		self.assertEqual(self.sent, [])
		self.assertNotIn(self.cycle().status, ("Paid", "Closed"))

	def test_escalates_after_max_unanswered(self):
		pr.run(THURSDAY_10)
		frappe.db.set_value(pr.DOCTYPE, self.cycle().name, {
			"status": "Sent", "reminders_sent": 3, "unanswered": 3,
			"last_sent_on": datetime(2026, 11, 1, 9), "poll_id": None})
		pr.run(THURSDAY_10 + timedelta(hours=1))
		self.assertEqual(self.cycle().status, "Escalated")
		self.assertEqual(len(self.posts), 1)

	def test_auto_send_skips_poll(self):
		frappe.db.set_single_value(pr.SETTINGS, "auto_send", 1)
		pr.run(THURSDAY_10)
		self.assertEqual(self.polls, [])
		self.assertEqual(self.cycle().status, "Sent")

	def test_outside_hours_does_nothing(self):
		pr.run(datetime(2026, 11, 29, 10))  # Sunday
		self.assertIsNone(self.cycle())

	def test_disabled_does_nothing(self):
		frappe.db.set_single_value(pr.SETTINGS, "enabled", 0)
		pr.run(THURSDAY_10)
		self.assertIsNone(self.cycle())


# ── Replies ───────────────────────────────────────────────────────────────────


class TestReplies(_CycleBase):
	def setUp(self):
		super().setUp()
		frappe.db.set_single_value(pr.SETTINGS, "auto_send", 1)
		pr.run(THURSDAY_10)
		self.c = self.cycle()
		self.assertEqual(self.c.status, "Sent")
		self.sent.clear()
		self.llm = '{"class": "other"}'
		p = patch.object(pr, "_classify", side_effect=lambda text, today: self.llm)
		p.start()
		self.addCleanup(p.stop)

	def test_promise_reply_records_date_and_acks(self):
		self.llm = '{"class": "promise", "date": "2026-11-27"}'
		pr.handle_reply(self.c.ticket, "nitalipa kesho", THURSDAY_10)
		self.c.reload()
		self.assertEqual(self.c.status, "Promised")
		self.assertEqual(str(self.c.promise_date), "2026-11-27")
		self.assertEqual(self.c.unanswered, 0)
		self.assertEqual(self.sent[0][0], "text")
		self.assertIn("27", self.sent[0][2])

	def test_paid_reply_confirmed_thanks_and_closes(self):
		self.llm = '{"class": "paid"}'
		self.snapshot = snap_ok(invoices=[])
		pr.handle_reply(self.c.ticket, "I paid", THURSDAY_10)
		self.assertEqual(self.cycle().status, "Paid")
		self.assertEqual(len(self.sent), 1)

	def test_paid_reply_unconfirmed_goes_to_team(self):
		self.llm = '{"class": "paid"}'
		pr.handle_reply(self.c.ticket, "I paid", THURSDAY_10)
		self.assertEqual(self.cycle().status, "With Team")
		self.assertIn("confirm", self.sent[0][2])

	def test_dispute_goes_to_team_no_reply(self):
		self.llm = '{"class": "dispute_or_question"}'
		pr.handle_reply(self.c.ticket, "this invoice is wrong", THURSDAY_10)
		self.assertEqual(self.cycle().status, "With Team")
		self.assertEqual(self.sent, [])

	def test_with_team_pauses_reminders(self):
		self.llm = '{"class": "other"}'
		pr.handle_reply(self.c.ticket, "call me", THURSDAY_10)
		pr.run(THURSDAY_10 + timedelta(days=9))
		self.assertEqual(self.sent, [])

	def test_ack_ignores_business_hours(self):
		self.llm = '{"class": "promise", "date": "2026-12-01"}'
		pr.handle_reply(self.c.ticket, "Monday", datetime(2026, 11, 29, 22))  # Sunday night
		self.assertEqual(len(self.sent), 1)

	def test_reply_routes_to_reminder_ticket(self):
		self.assertEqual(pr.live_ticket_for_phone("+254 711 000111", None), self.c.ticket)
		self.assertIsNone(pr.live_ticket_for_phone("254711000111", datetime(2026, 11, 26, 11)))
		self.assertIsNone(pr.live_ticket_for_phone("254799999999", None))

	def test_bot_skips_reminder_ticket(self):
		from helpdesk.integrations import bot

		self.assertTrue(pr.is_reminder_ticket(self.c.ticket))
		doc = frappe._dict(type="Incoming", reference_doctype="HD Ticket", reference_name=self.c.ticket,
		                   whatsapp_account=None, name="X")
		with patch.object(bot, "_bot_settings", return_value=frappe._dict(is_enabled=1)), \
		     patch.object(frappe, "enqueue") as enq:
			bot.handle_whatsapp_message(doc)
		enq.assert_not_called()

	def test_resolved_with_team_ticket_closes_cycle(self):
		pr.handle_reply(self.c.ticket, "call me", THURSDAY_10)
		pr._resolve(self.c.ticket)
		pr.run(THURSDAY_10 + timedelta(hours=1))
		self.assertEqual(frappe.db.get_value(pr.DOCTYPE, self.c.name, "status"), "Closed")
