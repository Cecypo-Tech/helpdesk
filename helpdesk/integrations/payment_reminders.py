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


# ── I/O seams (tests patch these) ─────────────────────────────────────────────

from helpdesk.integrations import raven_bridge  # noqa: E402


def _snapshot(erp_customer: str) -> dict:
	"""A fresh ERP snapshot. Bypasses the 5-minute cache: a send must see today's money."""
	from helpdesk.integrations import erpnext_standing

	frappe.cache.delete_value(erpnext_standing.snapshot_cache_key(erp_customer))
	return erpnext_standing.fetch_snapshot(erp_customer)


def _send_template(ticket: str, template: str, params: list[str]) -> None:
	"""Send an approved WABA template with explicit {{n}} values."""
	from helpdesk.integrations import wa

	if not template:
		raise ValueError("No WhatsApp template configured for this payment reminder step")
	phone = wa.get_contact_phone(ticket)
	if not phone:
		raise ValueError(f"No phone on ticket {ticket}")
	body = frappe.db.get_value("WhatsApp Templates", template, "template") or ""
	values = {str(i): str(v) for i, v in enumerate(params, 1)}
	for k, v in values.items():
		body = body.replace("{{" + k + "}}", v)
	frappe.get_doc({
		"doctype": "WhatsApp Message", "type": "Outgoing", "to": phone, "template": template,
		"body_param": json.dumps(values), "message": body,
		"reference_doctype": "HD Ticket", "reference_name": ticket,
	}).insert(ignore_permissions=True)


def _send_text(ticket: str, text: str) -> None:
	from helpdesk.integrations import wa

	wa._send_fw_reply(ticket, text, system=True)


def _window_open(ticket: str) -> bool:
	from helpdesk.integrations import wa

	return bool(ticket) and wa._fw_reply_window_open(ticket)


# ── Cycles ────────────────────────────────────────────────────────────────────


def _settings():
	return frappe.get_single(SETTINGS)


def _billing_contacts() -> dict[str, list]:
	"""{hd_customer: [contact rows]} for ticked billing contacts with a number."""
	rows = frappe.db.sql(
		"""
		select dl.link_name hd_customer, c.name contact, c.first_name,
		       coalesce(nullif(c.mobile_no, ''), c.phone) phone
		from `tabContact` c
		join `tabDynamic Link` dl on dl.parent = c.name and dl.parenttype = 'Contact'
		     and dl.link_doctype = 'HD Customer'
		join `tabHD Customer` hc on hc.name = dl.link_name
		where c.is_billing_contact = 1 and ifnull(hc.erpnext_customer, '') != ''
		  and ifnull(coalesce(nullif(c.mobile_no, ''), c.phone), '') != ''
		order by c.name
		""",
		as_dict=True,
	)
	out: dict[str, list] = {}
	for r in rows:
		out.setdefault(r.hd_customer, []).append(r)
	return out


def _digits(phone: str) -> str:
	return re.sub(r"\D", "", phone or "")[-9:]


def _pick_contact(contacts: list):
	"""The ticked contact whose number wrote to us most recently; else the first by name."""
	if len(contacts) == 1:
		return contacts[0]
	best, best_at = contacts[0], None
	for c in contacts:
		at = frappe.db.sql(
			"select max(creation) from `tabWhatsApp Message` where type='Incoming' and `from` like %s",
			(f"%{_digits(c.phone)}",),
		)[0][0] if frappe.db.exists("DocType", "WhatsApp Message") else None
		if at and (best_at is None or at > best_at):
			best, best_at = c, at
	return best


def _open_cycle(hd_customer: str):
	name = frappe.db.get_value(DOCTYPE, {"hd_customer": hd_customer, "status": ["in", OPEN]}, "name")
	return frappe.get_doc(DOCTYPE, name) if name else None


def _close_poll(cycle) -> None:
	if cycle.poll_id:
		try:
			raven_bridge.close_poll(cycle.poll_id)
		except Exception:
			frappe.log_error(title="Payment reminder: closing Raven poll failed")
	cycle.poll_id = cycle.poll_message = None


def _fmt(amount) -> str:
	return f"{float(amount or 0):,.0f}"


def run(now: datetime | None = None) -> None:
	"""Hourly. Does nothing unless enabled, and nothing outside business hours."""
	s = _settings()
	if not s.enabled:
		return
	now = now or frappe.utils.now_datetime()
	if not in_business_hours(now):
		return
	for hd_customer, contacts in _billing_contacts().items():
		try:
			refresh_customer(hd_customer, contacts, now, s)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Payment reminder failed for {hd_customer}")
	_close_resolved()
	frappe.db.commit()


def _close_resolved() -> None:
	"""With Team / Escalated cycles close once an agent resolves their ticket."""
	for c in frappe.get_all(DOCTYPE, filters={"status": ["in", ("With Team", "Escalated")], "ticket": ["is", "set"]},
	                        fields=["name", "ticket"]):
		if frappe.db.get_value("HD Ticket", c.ticket, "status_category") == "Resolved":
			frappe.db.set_value(DOCTYPE, c.name, "status", "Closed")


def refresh_customer(hd_customer: str, contacts: list, now: datetime, s) -> str | None:
	today = now.date()
	erp = frappe.db.get_value("HD Customer", hd_customer, "erpnext_customer")
	snap = _snapshot(erp)
	data = snap.get("snapshot") if snap.get("status") == "ok" else None
	if not data:
		return None  # unreachable or PowerPack switched off: never read as "paid"

	cycle = _open_cycle(hd_customer)
	due = qualifying(data.get("invoices"), today, int(s.min_invoice_age_days or 7))
	if not due:
		if cycle:
			_close_poll(cycle)
			if cycle.reminders_sent:
				send_thanks(cycle, now)
			cycle.status = "Paid"
			cycle.save(ignore_permissions=True)
			return cycle.name
		return None

	if not cycle:
		cycle = frappe.get_doc({"doctype": DOCTYPE, "hd_customer": hd_customer, "erp_customer": erp, "status": "Draft"})
	if not cycle.ticket:  # the contact is fixed once a conversation exists
		c = _pick_contact(contacts)
		cycle.contact, cycle.phone = c.contact, c.phone
	cycle.total = sum(float(i.get("outstanding_amount") or 0) for i in due)
	cycle.invoice_count = len(due)
	cycle.oldest_due = min(getdate(i.get("due_date") or i.get("posting_date")) for i in due)
	cycle.invoices = json.dumps([{k: i.get(k) for k in ("name", "posting_date", "due_date", "outstanding_amount")}
	                             for i in due], default=str)

	if float(data.get("advances_total") or 0) > 0:
		_close_poll(cycle)
		cycle.status, cycle.hold_reason = "Held", "Unallocated payment on ERPNext – check before chasing"
		cycle.save(ignore_permissions=True)
		return cycle.name
	if cycle.status == "Held":
		cycle.status, cycle.hold_reason = ("Sent" if cycle.reminders_sent else "Draft"), None

	if cycle.status == "Awaiting Approval":
		_check_votes(cycle, now, s)
	elif cycle.status in ACTIVE:
		action = next_action(cycle, today, s)
		if action == "escalate":
			cycle.status = "Escalated"
			raven_bridge.post(
				f"📞 Please call {hd_customer}: {cycle.reminders_sent} payment reminders, no reply. "
				f"KES {_fmt(cycle.total)} overdue. Ticket #{cycle.ticket}")
		elif action == "due" and not (cycle.skipped_on and getdate(cycle.skipped_on) == today):
			if s.auto_send:
				send_reminder(cycle, now, s)
			else:
				_post_poll(cycle, today)
	cycle.save(ignore_permissions=True)
	return cycle.name


def _post_poll(cycle, today: date) -> None:
	kind = "Promise nudge" if cycle.status == "Promised" else (
		"Follow-up" if cycle.reminders_sent else "First reminder")
	q = (f"{kind} to {cycle.hd_customer} ({cycle.phone}): {cycle.invoice_count} invoice(s), "
	     f"KES {_fmt(cycle.total)}, oldest due {cycle.oldest_due}. Send?")
	cycle.poll_id, cycle.poll_message = raven_bridge.create_poll(q)
	cycle.poll_posted_on = today
	cycle.status = "Awaiting Approval"


def _check_votes(cycle, now: datetime, s) -> None:
	if not cycle.poll_message:
		return
	approver = (s.approver or "").lower()
	v = raven_bridge.votes(cycle.poll_message)
	if approver and approver in v.get("Send", []):
		_close_poll(cycle)
		send_reminder(cycle, now, s)
	elif approver and approver in v.get("Skip", []):
		_close_poll(cycle)
		cycle.status = "Promised" if cycle.promise_date and cycle.reminders_sent else (
			"Sent" if cycle.reminders_sent else "Draft")
		cycle.skipped_on = now.date()


def _ensure_ticket(cycle) -> str:
	if cycle.ticket and frappe.db.get_value("HD Ticket", cycle.ticket, "status_category") != "Resolved":
		return cycle.ticket
	email = frappe.db.get_value("Contact", cycle.contact, "email_id") or (
		f"whatsapp+{re.sub(r'[^0-9]', '', cycle.phone or '')}@"
		f"{frappe.db.get_single_value('WhatsApp Helpdesk Settings', 'placeholder_email_domain') or 'whatsapp.placeholder.local'}")
	previous = frappe.session.user
	frappe.set_user("Administrator")
	try:
		t = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": f"Payment reminder – {cycle.hd_customer}",
			"description": f"Automated payment reminder cycle {cycle.name}.",
			"raised_by": email, "contact": cycle.contact, "customer": cycle.hd_customer,
			"ticket_channel": "WhatsApp",
		}).insert(ignore_permissions=True)
	finally:
		frappe.set_user(previous)
	# Waiting on the customer: keeps it out of the Open queue until they write.
	if frappe.db.exists("HD Ticket Status", "Replied"):
		frappe.db.set_value("HD Ticket", t.name, "status", "Replied")
	cycle.ticket = t.name
	return t.name


def _first_name(cycle) -> str:
	return frappe.db.get_value("Contact", cycle.contact, "first_name") or "there"


def send_reminder(cycle, now: datetime, s) -> bool:
	if not cycle.name:
		cycle.insert(ignore_permissions=True)
	ticket = _ensure_ticket(cycle)
	if cycle.status == "Promised" or (cycle.promise_date and getdate(cycle.promise_date) < now.date()):
		_send_template(ticket, s.template_nudge, [_first_name(cycle), cycle.promise_date, _fmt(cycle.total)])
	else:
		_send_template(ticket, s.template_reminder,
		               [_first_name(cycle), cycle.invoice_count, _fmt(cycle.total), cycle.oldest_due])
	cycle.reminders_sent = (cycle.reminders_sent or 0) + 1
	cycle.unanswered = (cycle.unanswered or 0) + 1
	cycle.last_sent_on = now
	cycle.promise_date = None
	cycle.status = "Sent"
	return True


def send_thanks(cycle, now: datetime | None = None) -> None:
	"""Thank the customer and resolve the ticket. Free-form inside the 24h window, else the template."""
	if not cycle.ticket:
		return
	try:
		if _window_open(cycle.ticket):
			_send_text(cycle.ticket, f"Thank you {_first_name(cycle)}, we have received your payment 🙏")
		else:
			_send_template(cycle.ticket, _settings().template_thanks, [_first_name(cycle)])
	except Exception:
		frappe.log_error(title=f"Payment reminder thank-you failed for {cycle.name}")
	_resolve(cycle.ticket)


def _resolve(ticket: str) -> None:
	try:
		t = frappe.get_doc("HD Ticket", ticket)
		t.status = "Resolved"
		t.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(title=f"Payment reminder: resolving ticket {ticket} failed")
