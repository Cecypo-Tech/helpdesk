"""Monday Raven digest for the owner, plus the two AI sweeps that feed it.

- guess_work_types (hourly): fills ticket_type on open tickets still Unspecified.
- send (Mon 08:00): flags last week's unhappy closes, then posts one digest message.

Sweeps, not hooks: WhatsApp status changes go through db.set_value and auto-close
skips validate, so a hook would miss them.
"""

import json
import re
from datetime import datetime, timedelta

import frappe
from frappe.utils import add_to_date, get_url, getdate, now_datetime

from helpdesk.consts import WORK_TYPES
from helpdesk.integrations import erpnext_remote, raven_bridge

SETTINGS = "HD Weekly Digest Settings"
REMINDER = "HD Payment Reminder"
GUESS_BATCH = 20
UNHAPPY_CAP = 200
STALE_CAP = 30

GUESS_PROMPT = (
	"You sort IT support tickets for a software company into one work type.\n"
	"Software: our ERP/POS software, accounts, reports, users, errors in the system.\n"
	"Network: internet, Wi-Fi, routers, cabling, VPN.\n"
	"Printer: printers, receipt printers, scanners, toner.\n"
	"Template change: changing a print format, invoice or receipt layout.\n"
	"Other: anything else.\n"
	"Answer with the work type name only."
)
UNHAPPY_PROMPT = (
	"Read this support conversation. Was the customer clearly unhappy: complaining, frustrated by a "
	"repeated problem, or threatening to leave? Ordinary questions and polite follow-ups are not unhappy.\n"
	'Answer with JSON only: {"unhappy": true|false, "reason": "<one short line, empty if not unhappy>"}'
)


def _llm(messages: list[dict]) -> str:
	from helpdesk.integrations.llm import chat

	return chat(messages, max_tokens=100)


def _reminder_tickets() -> set[str]:
	return {str(t) for t in frappe.get_all(REMINDER, filters={"ticket": ["is", "set"]}, pluck="ticket")}


def _transcript(ticket: str, limit: int = 30) -> str:
	t = frappe.db.get_value("HD Ticket", ticket, ["subject", "description"], as_dict=True) or {}
	rows = []
	for doctype, who in (("WhatsApp Message", "type"), ("WA Message", "direction")):
		try:
			rows += [
				(r.creation, "Customer" if r[who] == "Incoming" else "Agent", r.message)
				for r in frappe.get_all(
					doctype,
					filters={"reference_doctype": "HD Ticket", "reference_name": ticket},
					fields=["creation", who, "message"],
					order_by="creation desc",
					limit=limit,
				)
			]
		except Exception:  # doctype not installed on this site
			pass
	rows += [
		(r.creation, "Customer" if r.sent_or_received == "Received" else "Agent", r.content)
		for r in frappe.get_all(
			"Communication",
			filters={"reference_doctype": "HD Ticket", "reference_name": ticket},
			fields=["creation", "sent_or_received", "content"],
			order_by="creation desc",
			limit=limit,
		)
	]
	lines = [f"{who}: {frappe.utils.strip_html(text or '')[:500]}" for _, who, text in sorted(rows)[-limit:]]
	head = f"Ticket #{ticket}: {t.get('subject') or ''}\n{frappe.utils.strip_html(t.get('description') or '')[:800]}"
	return (head + "\n\n" + "\n".join(lines))[:6000]


def parse_work_type(text: str) -> str | None:
	clean = (text or "").strip().strip(".\"'").lower()
	return next((w for w in WORK_TYPES if w.lower() == clean), None)


def parse_unhappy(text: str) -> tuple[bool, str]:
	m = re.search(r"\{.*\}", text or "", re.S)
	try:
		data = json.loads(m.group(0)) if m else {}
	except ValueError:
		return False, ""
	if data.get("unhappy") is True:
		return True, str(data.get("reason") or "")[:200]
	return False, ""


def stamp_closed_on() -> None:
	"""Record when each ticket closed, so later edits don't move it into another week.

	Hourly, not a hook: WhatsApp closes and reopens go through db.set_value.
	"""
	frappe.db.sql("""update `tabHD Ticket` set closed_on = coalesce(resolution_date, modified)
		where status_category = 'Resolved' and closed_on is null""")
	frappe.db.sql("""update `tabHD Ticket` set closed_on = null
		where status_category != 'Resolved' and closed_on is not null""")


def guess_work_types(now: datetime | None = None) -> int:
	now = now or now_datetime()
	stamp_closed_on()
	skip = _reminder_tickets()
	names = frappe.get_all(
		"HD Ticket",
		filters={
			"status_category": "Open",
			"ticket_type": ["not in", WORK_TYPES],
			"work_type_guessed": 0,
			"creation": ["<", add_to_date(now, hours=-1)],
		},
		pluck="name",
		order_by="creation desc",
		limit=GUESS_BATCH + len(skip),
	)
	typed = 0
	for name in [n for n in names if str(n) not in skip][:GUESS_BATCH]:
		guess = None
		try:
			guess = parse_work_type(_llm([
				{"role": "system", "content": GUESS_PROMPT},
				{"role": "user", "content": _transcript(name)},
			]))
		except Exception:
			frappe.log_error(title="Work type guess failed")
			continue  # retry next hour
		values = {"work_type_guessed": 1}
		if guess:
			values["ticket_type"] = guess
			typed += 1
		frappe.db.set_value("HD Ticket", name, values, update_modified=False)
		frappe.db.commit()  # nosemgrep: one ticket per LLM call, keep progress if the job dies
	return typed


def _closed_between(start: datetime, end: datetime) -> list[str]:
	return [str(n) for n in frappe.db.sql_list(
		"""select name from `tabHD Ticket`
		where status_category = 'Resolved' and closed_on >= %s and closed_on < %s""",
		(start, end),
	)]


def check_unhappy(start: datetime, end: datetime) -> list[str]:
	skip = _reminder_tickets()
	flagged = []
	for name in [n for n in _closed_between(start, end) if n not in skip][:UNHAPPY_CAP]:
		try:
			unhappy, reason = parse_unhappy(_llm([
				{"role": "system", "content": UNHAPPY_PROMPT},
				{"role": "user", "content": _transcript(name)},
			]))
		except Exception:
			frappe.log_error(title="Unhappy check failed")
			continue
		frappe.db.set_value("HD Ticket", name, {"unhappy": int(unhappy), "unhappy_reason": reason},
		                    update_modified=False)
		frappe.db.commit()  # nosemgrep: keep progress if the job dies mid-week
		if unhappy:
			flagged.append(name)
	return flagged


def overdue_total() -> float | None:
	res = erpnext_remote.call("frappe.client.get_list", {
		"doctype": "Sales Invoice",
		"filters": json.dumps([["docstatus", "=", 1], ["outstanding_amount", ">", 0],
		                       ["due_date", "<", str(getdate())]]),
		"fields": json.dumps(["base_outstanding_amount"]),
		"limit_page_length": 0,
	})
	if not res.get("ok") or not isinstance(res.get("data"), list):
		return None
	return sum(float(r.get("base_outstanding_amount") or 0) for r in res["data"])


def _kes(v) -> str:
	return f"KES {float(v or 0):,.0f}"


def _week(now: datetime) -> tuple[datetime, datetime]:
	"""Last Monday 00:00 to this Monday 00:00."""
	this_monday = datetime.combine(getdate(now) - timedelta(days=getdate(now).weekday()), datetime.min.time())
	return this_monday - timedelta(days=7), this_monday


def build(now: datetime) -> str:
	base = get_url()
	tlink = lambda n, s: f"[#{n}]({base}/helpdesk/tickets/{n}) {s or ''}".rstrip()  # noqa: E731
	rlink = lambda n, c: f"[{c}]({base}/app/hd-payment-reminder/{n})"  # noqa: E731
	start, end = _week(now)
	skip = _reminder_tickets()
	sections = []

	total = overdue_total()
	if total is None:
		sections.append("**Overdue**\nCouldn't read ERPNext.")
	elif total > 0:
		sections.append(f"**Overdue**\n{_kes(total)} past due.")

	week_end = getdate(end) + timedelta(days=6)
	promises = frappe.get_all(REMINDER, filters={"status": "Promised", "promise_date": ["between", [getdate(end), week_end]]},
	                          fields=["name", "hd_customer", "total", "promise_date"], order_by="promise_date")
	if promises:
		sections.append("**Promises due this week**\n" + "\n".join(
			f"- {rlink(p.name, p.hd_customer)}: {_kes(p.total)} on {p.promise_date:%a %d %b}" for p in promises))

	held = frappe.get_all(REMINDER, filters={"status": "Held", "hold_reason": ["like", "Unallocated payment%"]},
	                      fields=["name", "hd_customer", "advance_amount"])
	if held:
		sections.append("**Unallocated money**\n" + "\n".join(
			f"- {rlink(h.name, h.hd_customer)}: {_kes(h.advance_amount)}" for h in held))

	closed = _closed_between(start, end)
	charged = frappe.get_all("HD Ticket", filters={"name": ["in", closed or [""]], "charge": "Charge",
	                                  "ticket_type": ["!=", "Software"]},
	                         fields=["name", "subject", "customer", "ticket_type"], order_by="name")
	if charged:
		sections.append("**Chargeable jobs last week**\n" + "\n".join(
			f"- {tlink(t.name, t.subject)} · {t.customer or 'no customer'} · {t.ticket_type}" for t in charged))

	s = frappe.get_cached_doc(SETTINGS)
	cutoff = getdate(now) - timedelta(days=s.open_days or 3)
	stale = [t for t in frappe.get_all("HD Ticket", filters={"status_category": "Open", "opening_date": ["<", cutoff]},
	                                   fields=["name", "subject", "opening_date"], order_by="opening_date")
	         if str(t.name) not in skip]
	if stale:
		more = f"\n…and {len(stale) - STALE_CAP} more" if len(stale) > STALE_CAP else ""
		sections.append(f"**Open more than {s.open_days or 3} days**\n" + "\n".join(
			f"- {tlink(t.name, t.subject)} (since {t.opening_date:%d %b})" for t in stale[:STALE_CAP]) + more)

	sad = frappe.get_all("HD Ticket", filters={"name": ["in", closed or [""]], "unhappy": 1},
	                     fields=["name", "subject", "unhappy_reason"], order_by="name")
	sad = [t for t in sad if str(t.name) not in skip]
	if sad:
		sections.append("**Unhappy customers**\n" + "\n".join(
			f"- {tlink(t.name, t.subject)}: {t.unhappy_reason}" for t in sad))

	waiting = frappe.db.count("HD Learned Note", {"status": "Pending"})
	if waiting:
		sections.append(f"**Bot notes**\n[{waiting} note{'s' if waiting > 1 else ''} waiting for approval]"
		                f"({base}/app/hd-learned-note?status=Pending)")

	head = f"📋 Monday digest, week of {start:%d %b}"
	return head + "\n\n" + ("\n\n".join(sections) if sections else "Quiet week — nothing to flag.")


def send() -> None:
	"""Cron entry: the unhappy check can take minutes, so run it on the long queue."""
	frappe.enqueue("helpdesk.integrations.weekly_digest.send_now", queue="long", timeout=3600)


def send_now(now: datetime | None = None) -> None:
	s = frappe.get_cached_doc(SETTINGS)
	if not (s.enabled and s.raven_channel):
		return
	now = now or now_datetime()
	stamp_closed_on()
	check_unhappy(*_week(now))
	raven_bridge.post(build(now), channel=s.raven_channel, user=s.raven_user)
