"""The bot learns from the team: closed tickets become notes a person approves on Raven.

Hourly: draft notes for newly closed tickets, post an Approve / Skip poll for each,
and act on votes. Only approved notes become HD Ticket Embedding rows, which the bot
already searches (embeddings.search_resolved_tickets).
"""

import json
import re
from datetime import datetime, timedelta

import frappe
from frappe.utils import escape_html, get_url, now_datetime

from helpdesk import entitlement
from helpdesk.integrations import raven_bridge
from helpdesk.integrations.weekly_digest import _reminder_tickets, _transcript

NOTE = "HD Learned Note"
BOT_SETTINGS = "Helpdesk Bot Settings"
DRAFT_BATCH = 10
POLL_BATCH = 10
LOOKBACK_DAYS = 7

PROMPT = (
	"You read a closed IT support conversation and decide whether it teaches a reusable fix that would help "
	"answer a future customer with the same problem.\n"
	"Not useful: nonsense, greetings, spam, wrong numbers, one-off account matters (balances, a specific invoice, "
	"passwords, a user's own data), or no clear fix.\n"
	"If useful, write a short Problem (1-2 lines) and Fix (the steps the team gave). Never include customer or "
	"company names, phone numbers, amounts, invoice numbers, passwords, IP addresses or site URLs; use generic "
	"words instead.\n"
	'Answer with JSON only: {"useful": true|false, "problem": "...", "fix": "..."}'
)


def _llm(messages: list[dict]) -> str:
	from helpdesk.integrations.llm import chat

	return chat(messages, max_tokens=400)


def _embed(text: str) -> list[float]:
	from helpdesk.integrations.embeddings import embed

	return embed(text, task_type="RETRIEVAL_DOCUMENT")


def _settings():
	return frappe.get_cached_doc(BOT_SETTINGS)


def parse_note(text: str) -> dict | None:
	m = re.search(r"\{.*\}", text or "", re.S)
	try:
		data = json.loads(m.group(0)) if m else None
	except ValueError:
		return None
	if not isinstance(data, dict) or not isinstance(data.get("useful"), bool):
		return None
	if not data["useful"]:
		return {"useful": False, "problem": "", "fix": ""}
	problem, fix = str(data.get("problem") or "").strip(), str(data.get("fix") or "").strip()
	return {"useful": True, "problem": problem, "fix": fix} if problem and fix else None


def draft(now: datetime) -> int:
	"""Ask the LLM about recently closed tickets that have no note yet."""
	skip = _reminder_tickets() | {str(t) for t in frappe.get_all(NOTE, pluck="ticket")}
	names = frappe.get_all(
		"HD Ticket",
		filters={"status_category": "Resolved", "is_merged": 0,
		         "closed_on": [">=", now - timedelta(days=LOOKBACK_DAYS)]},
		pluck="name",
		order_by="closed_on desc",
		limit=DRAFT_BATCH + len(skip),
	)
	made = 0
	for name in [n for n in names if str(n) not in skip][:DRAFT_BATCH]:
		try:
			note = parse_note(_llm([{"role": "system", "content": PROMPT},
			                        {"role": "user", "content": _transcript(name)}]))
		except Exception:
			frappe.log_error(title="Learned note draft failed")
			continue
		if note is None:
			continue  # bad output: retry next hour
		if frappe.db.exists(NOTE, {"ticket": name}):
			continue  # another run got here first
		frappe.get_doc({
			"doctype": NOTE,
			"ticket": name,
			"subject": frappe.db.get_value("HD Ticket", name, "subject"),
			"product": entitlement.resolve_product_for_ticket(name),
			"status": "Pending" if note["useful"] else "Not useful",
			"problem": note["problem"],
			"fix": note["fix"],
		}).insert(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep: one LLM call per ticket, keep progress
		made += 1
	return made


def post_polls() -> int:
	s = _settings()
	base = get_url()
	posted = 0
	for n in frappe.get_all(NOTE, filters={"status": "Pending", "poll_id": ["is", "not set"]},
	                        fields=["name", "ticket", "product", "problem", "fix"], order_by="creation", limit=POLL_BATCH):
		# Raven renders message HTML but not poll text: the note and its links go in a
		# message, the poll underneath just asks.
		note_html = (
			f"<p>📘 <strong>Teach the bot this?</strong> "
			f"<strong>{escape_html(n.product or 'No product')}</strong> · "
			f'<a href="{base}/helpdesk/tickets/{n.ticket}">Ticket #{n.ticket}</a> · '
			f'<a href="{base}/app/hd-learned-note/{n.name}">Edit</a><br>'
			f"<strong>Problem:</strong> {escape_html(n.problem or '')}<br>"
			f"<strong>Fix:</strong> {escape_html(n.fix or '')}</p>"
		)
		try:
			raven_bridge.post(note_html, channel=s.learn_raven_channel, user=s.learn_raven_user)
			poll_id, message_id = raven_bridge.create_poll(
				f"📘 Teach the bot the note above? (ticket #{n.ticket})",
				channel=s.learn_raven_channel, user=s.learn_raven_user, options=("Approve", "Skip"))
		except Exception:
			frappe.log_error(title="Learned note poll failed")
			break  # Raven down: try again next hour
		if not message_id:  # can't read votes without it: drop this poll, retry next hour
			frappe.log_error(title="Learned note poll has no message id")
			close_poll(poll_id)
			continue
		frappe.db.set_value(NOTE, n.name, {"poll_id": poll_id, "poll_message": message_id})
		frappe.db.commit()  # nosemgrep: a posted poll must not be posted twice
		posted += 1
	return posted


def unlearn(ticket: str) -> None:
	if frappe.db.exists("HD Ticket Embedding", {"ticket": ticket}):
		frappe.db.delete("HD Ticket Embedding", {"ticket": ticket})
		from helpdesk.integrations.embeddings import invalidate_cache

		invalidate_cache()


def close_poll(poll_id: str) -> None:
	try:
		raven_bridge.close_poll(poll_id, user=_settings().learn_raven_user)
	except Exception:
		frappe.log_error(title="Learned note close poll failed")


def approve(note_name: str) -> None:
	n = frappe.get_doc(NOTE, note_name)  # re-read: it may have been edited since the poll
	text = f"Problem: {n.problem}\nFix: {n.fix}"
	vec = _embed(f"{n.subject or ''}\n{text}")
	frappe.db.delete("HD Ticket Embedding", {"ticket": n.ticket})
	frappe.get_doc({
		"doctype": "HD Ticket Embedding",
		"ticket": n.ticket,
		"subject": n.subject,
		"resolution_details": text,
		"product": n.product or frappe.db.get_value("HD Ticket", n.ticket, "hd_product"),
		"embedding": json.dumps(vec),
	}).insert(ignore_permissions=True)  # after_insert invalidates the bot's cache


def check_votes() -> int:
	s = _settings()
	decided = 0
	for n in frappe.get_all(NOTE, filters={"status": "Pending", "poll_message": ["is", "set"]},
	                        fields=["name", "poll_id", "poll_message"]):
		try:
			v = raven_bridge.votes(n.poll_message, user=s.learn_raven_user)
		except Exception:
			frappe.log_error(title="Learned note votes failed")
			continue
		yes, no = v.get("Approve") or [], v.get("Skip") or []
		if len(yes) == len(no):
			continue  # no votes or a tie: wait
		status, voters = ("Approved", yes) if len(yes) > len(no) else ("Skipped", no)
		try:
			if status == "Approved":
				approve(n.name)
		except Exception:
			frappe.log_error(title="Learned note approve failed")
			continue
		frappe.db.set_value(NOTE, n.name, {"status": status, "decided_by": voters[0]})
		close_poll(n.poll_id)
		frappe.db.commit()  # nosemgrep
		decided += 1
	return decided


def run(now: datetime | None = None) -> None:
	s = _settings()
	if not (s.learn_from_tickets and s.learn_raven_channel and s.is_enabled):
		return
	now = now or now_datetime()
	from helpdesk.integrations.weekly_digest import stamp_closed_on

	stamp_closed_on()
	draft(now)
	post_polls()
	check_votes()
