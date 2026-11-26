"""Chase overdue invoices on WhatsApp Business, with the owner's approval.

Spec: docs/superpowers/specs/2026-11-26-payment-reminders-design.md

One HD Payment Reminder is one chasing cycle for one customer. `run()` (hourly)
refreshes figures from ERPNext, decides what each cycle needs, posts a Raven
poll per reminder (unless auto_send), sends the ones the owner approved, and
thanks customers who have paid. Replies arrive through `on_incoming`.
"""

import json
import re
from datetime import date, datetime, timedelta

import frappe
from frappe.utils import getdate

SETTINGS = "HD Payment Reminder Settings"
DOCTYPE = "HD Payment Reminder"

OPEN = ("Draft", "Awaiting Approval", "Approved", "Sent", "Promised", "With Team", "Escalated", "Held")
# Cycles the schedule still drives. With Team / Escalated / Held wait on people.
ACTIVE = ("Draft", "Awaiting Approval", "Approved", "Sent", "Promised")
CLASSES = ("promise", "paid", "dispute_or_question", "other")


# ── Pure decisions ────────────────────────────────────────────────────────────


def qualifying(invoices: list[dict], today: date, min_age: int) -> list[dict]:
	"""Invoices past due AND at least `min_age` days old, with money owing."""
	cutoff = today - timedelta(days=min_age)
	return [
		i for i in invoices or []
		if (i.get("outstanding_amount") or 0) > 0
		and getdate(i.get("due_date") or i.get("posting_date")) < today
		and getdate(i.get("posting_date")) <= cutoff
	]


def next_action(cycle, today: date, s) -> str:
	"""'due', 'wait' or 'escalate' for an active cycle."""
	if (cycle.unanswered or 0) >= int(s.max_unanswered or 3):
		return "escalate"
	if not cycle.reminders_sent:
		return "due"
	since = (today - getdate(cycle.last_sent_on)).days
	if since < int(s.min_gap_days or 2):
		return "wait"
	if cycle.status == "Promised" and cycle.promise_date:
		return "due" if today >= getdate(cycle.promise_date) + timedelta(days=2) else "wait"
	return "due" if since >= int(s.followup_days or 7) else "wait"


def in_business_hours(now: datetime) -> bool:
	"""Mon–Sat, 08:00–18:00."""
	return now.weekday() != 6 and 8 <= now.hour < 18


def parse_classification(text: str, today: date) -> dict:
	"""The classifier's JSON answer, made safe. Anything doubtful is 'other'."""
	other = {"class": "other", "date": None}
	m = re.search(r"\{.*\}", text or "", re.S)
	if not m:
		return other
	try:
		data = json.loads(m.group(0))
	except ValueError:
		return other
	cls = data.get("class")
	if cls not in CLASSES:
		return other
	when = None
	if data.get("date"):
		try:
			when = getdate(data["date"])
		except Exception:
			when = None
	if cls == "promise" and (not when or when < today):
		return other
	return {"class": cls, "date": when}
