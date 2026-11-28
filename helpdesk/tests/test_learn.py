"""Bot learns from team replies: drafts, Raven approval, and what reaches the bot."""

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import frappe

from helpdesk.integrations import learn
from helpdesk.test_utils import make_ticket

NOW = datetime(2026, 11, 27, 10, 0)
GOOD = json.dumps({"useful": True, "problem": "Receipt printer prints blank", "fix": "Reinstall the driver, restart POS"})


class TestParse(unittest.TestCase):
	def test_parse_note(self):
		self.assertEqual(learn.parse_note(GOOD)["problem"], "Receipt printer prints blank")
		self.assertEqual(learn.parse_note('{"useful": false}'), {"useful": False, "problem": "", "fix": ""})
		self.assertIsNone(learn.parse_note("nope"))
		self.assertIsNone(learn.parse_note('{"useful": true, "problem": "", "fix": "x"}'))


class _Base(unittest.TestCase):
	def setUp(self):
		frappe.db.delete("HD Learned Note")
		frappe.db.delete("HD Ticket Embedding")
		frappe.db.delete("HD Payment Reminder")
		s = frappe.get_doc("Helpdesk Bot Settings")
		s.learn_from_tickets, s.learn_raven_channel, s.learn_raven_user = 1, "erp-support-actions", "Administrator"
		s.is_enabled = 1
		s.flags.ignore_mandatory = True
		s.save(ignore_permissions=True)
		self.polls, self.closed, self.vote = [], [], {}
		patches = [
			patch.object(learn.raven_bridge, "create_poll", side_effect=self._poll),
			patch.object(learn.raven_bridge, "votes", side_effect=lambda m, **k: self.vote),
			patch.object(learn.raven_bridge, "close_poll", side_effect=lambda p, **k: self.closed.append(p)),
			patch.object(learn.raven_bridge, "post"),
			patch.object(learn, "_embed", return_value=[0.1, 0.2]),
			patch.object(frappe.db, "commit"),  # keep test rows inside the rolled-back transaction
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def tearDown(self):
		frappe.db.rollback()

	def _poll(self, question, **k):
		self.polls.append((question, k))
		return f"POLL{len(self.polls)}", f"MSG{len(self.polls)}"

	def closed_ticket(self, subject="Printer blank", closed=NOW - timedelta(days=1), **db):
		t = make_ticket(subject=subject)
		frappe.db.set_value("HD Ticket", t.name, {"status": "Closed", "status_category": "Resolved",
		                                          "closed_on": closed, **db}, update_modified=False)
		return t.name

	def note(self, ticket):
		return frappe.get_doc("HD Learned Note", {"ticket": ticket})


class TestDraft(_Base):
	def test_draft_creates_pending_and_polls(self):
		t = self.closed_ticket()
		with patch.object(learn, "_llm", return_value=GOOD):
			learn.run(NOW)
		n = self.note(t)
		self.assertEqual(n.status, "Pending")
		self.assertEqual(n.poll_id, "POLL1")

	def test_not_useful_recorded_no_poll(self):
		t = self.closed_ticket()
		with patch.object(learn, "_llm", return_value='{"useful": false}') as llm:
			learn.run(NOW)
			learn.run(NOW)
		self.assertEqual(self.note(t).status, "Not useful")
		self.assertEqual(llm.call_count, 1)
		self.assertEqual(self.polls, [])

	def test_bad_json_retries(self):
		t = self.closed_ticket()
		with patch.object(learn, "_llm", return_value="garbage"):
			learn.run(NOW)
		self.assertFalse(frappe.db.exists("HD Learned Note", {"ticket": t}))

	def test_draft_skips_reminder_merged_old(self):
		rem = self.closed_ticket()
		cust = frappe.db.get_value("HD Customer", {}, "name") or frappe.get_doc(
			{"doctype": "HD Customer", "customer_name": "ACME Learn Test"}).insert(ignore_permissions=True).name
		frappe.get_doc({"doctype": "HD Payment Reminder", "hd_customer": cust, "status": "Paid", "ticket": rem}).insert(
			ignore_permissions=True)
		self.closed_ticket(is_merged=1)
		self.closed_ticket(closed=NOW - timedelta(days=9))
		with patch.object(learn, "_llm", return_value=GOOD) as llm:
			self.assertEqual(learn.draft(NOW), 0)
		llm.assert_not_called()

	def test_draft_batch_cap(self):
		for i in range(learn.DRAFT_BATCH + 2):
			self.closed_ticket(subject=f"T{i}")
		with patch.object(learn, "_llm", return_value=GOOD):
			self.assertEqual(learn.draft(NOW), learn.DRAFT_BATCH)

	def test_poll_question_has_links(self):
		t = self.closed_ticket()
		with patch.object(learn, "_llm", return_value=GOOD), \
				patch.object(learn.raven_bridge, "post") as post:
			learn.run(NOW)
		html, k = post.call_args.args[0], post.call_args.kwargs
		name = self.note(t).name
		self.assertIn(f'<a href="http://', html)
		self.assertIn(f'/app/hd-learned-note/{name}">Edit</a>', html)
		self.assertIn(f'/helpdesk/tickets/{t}">Ticket #{t}</a>', html)
		self.assertIn("Receipt printer prints blank", html)
		self.assertEqual(k, {"channel": "erp-support-actions", "user": "Administrator"})
		q, pk = self.polls[0]
		self.assertNotIn("http", q)  # the poll is just the question; links live in the message above
		self.assertEqual(pk, {"channel": "erp-support-actions", "user": "Administrator", "options": ("Approve", "Skip")})

	def test_note_text_is_escaped(self):
		t = self.closed_ticket()
		evil = json.dumps({"useful": True, "problem": "<script>x</script>", "fix": "a & b"})
		with patch.object(learn, "_llm", return_value=evil), \
				patch.object(learn.raven_bridge, "post") as post:
			learn.run(NOW)
		self.assertNotIn("<script>", post.call_args.args[0])
		self.assertIn("a &amp; b", post.call_args.args[0])

	def test_disabled_does_nothing(self):
		frappe.db.set_single_value("Helpdesk Bot Settings", "learn_from_tickets", 0)
		frappe.clear_document_cache("Helpdesk Bot Settings")
		self.closed_ticket()
		with patch.object(learn, "_llm", return_value=GOOD) as llm:
			learn.run(NOW)
		llm.assert_not_called()


class TestVotes(_Base):
	def pending(self):
		t = self.closed_ticket()
		with patch.object(learn, "_llm", return_value=GOOD):
			learn.run(NOW)
		return t

	def test_approve_writes_embedding(self):
		t = self.pending()
		self.vote = {"Approve": ["someone@example.com"]}
		learn.run(NOW)
		n = self.note(t)
		self.assertEqual((n.status, n.decided_by), ("Approved", "someone@example.com"))
		row = frappe.get_doc("HD Ticket Embedding", {"ticket": t})
		self.assertIn("Reinstall the driver", row.resolution_details)
		self.assertEqual(self.closed, ["POLL1"])

	def test_approve_uses_edited_note(self):
		t = self.pending()
		frappe.db.set_value("HD Learned Note", self.note(t).name, "fix", "Edited: swap the USB cable")
		self.vote = {"Approve": ["someone@example.com"]}
		learn.run(NOW)
		self.assertIn("swap the USB cable", frappe.db.get_value("HD Ticket Embedding", {"ticket": t}, "resolution_details"))

	def test_skip_closes_poll_no_embedding(self):
		t = self.pending()
		self.vote = {"Skip": ["someone@example.com"]}
		learn.run(NOW)
		self.assertEqual(self.note(t).status, "Skipped")
		self.assertFalse(frappe.db.exists("HD Ticket Embedding", {"ticket": t}))
		self.assertEqual(self.closed, ["POLL1"])

	def test_tie_waits(self):
		t = self.pending()
		self.vote = {"Approve": ["a@example.com"], "Skip": ["b@example.com"]}
		learn.run(NOW)
		self.assertEqual(self.note(t).status, "Pending")
		self.vote = {}
		learn.run(NOW)
		self.assertEqual(self.note(t).status, "Pending")

	def test_approve_replaces_existing_embedding(self):
		t = self.pending()
		frappe.get_doc({"doctype": "HD Ticket Embedding", "ticket": t, "subject": "old", "embedding": "[1]"}).insert(
			ignore_permissions=True)
		self.vote = {"Approve": ["a@example.com"]}
		learn.run(NOW)
		self.assertEqual(frappe.db.count("HD Ticket Embedding", {"ticket": t}), 1)

	def test_unapprove_removes_embedding(self):
		t = self.pending()
		self.vote = {"Approve": ["a@example.com"]}
		learn.run(NOW)
		n = self.note(t)
		n.status = "Skipped"
		n.save(ignore_permissions=True)
		self.assertFalse(frappe.db.exists("HD Ticket Embedding", {"ticket": t}))


class TestOthers(_Base):
	def test_embed_skips_empty_resolution(self):
		from helpdesk.integrations import embeddings

		t = make_ticket(subject="Subject only")
		frappe.db.set_value("HD Ticket", t.name, "status", "Resolved")
		with patch.object(embeddings, "_settings", return_value=frappe._dict(is_enabled=1)), \
				patch.object(embeddings, "embed", return_value=[0.1]) as emb:
			embeddings.embed_resolved_tickets()
		emb.assert_not_called()

	def test_digest_mentions_waiting_notes(self):
		from helpdesk.integrations import weekly_digest as wd

		self.closed_ticket()
		with patch.object(learn, "_llm", return_value=GOOD):
			learn.run(NOW)
		with patch.object(wd, "overdue_total", return_value=0.0):
			text = wd.build(NOW)
		self.assertIn("1 note waiting for approval", text)
		self.assertIn("/app/hd-learned-note?status=Pending", text)


class TestReviewFixes(TestVotes):
	def approved(self):
		t = self.pending()
		self.vote = {"Approve": ["a@example.com"]}
		learn.run(NOW)
		return t

	def test_undo_survives_daily_embed(self):
		from helpdesk.integrations import embeddings

		t = self.approved()
		n = self.note(t)
		n.status = "Skipped"
		n.save(ignore_permissions=True)
		frappe.db.set_value("HD Ticket", t, {"status": "Resolved", "resolution_details": "agent text"})
		with patch.object(embeddings, "_settings", return_value=frappe._dict(is_enabled=1)), \
				patch.object(embeddings, "embed", return_value=[0.1]):
			embeddings.embed_resolved_tickets()
		self.assertFalse(frappe.db.exists("HD Ticket Embedding", {"ticket": t}))

	def test_approved_to_not_useful_unlearns(self):
		t = self.approved()
		n = self.note(t)
		n.status = "Not useful"
		n.save(ignore_permissions=True)
		self.assertFalse(frappe.db.exists("HD Ticket Embedding", {"ticket": t}))

	def test_edit_of_approved_note_reembeds(self):
		t = self.approved()
		n = self.note(t)
		n.fix = "Use the new driver from the vendor site"
		n.save(ignore_permissions=True)
		self.assertIn("new driver", frappe.db.get_value("HD Ticket Embedding", {"ticket": t}, "resolution_details"))

	def test_form_decision_closes_poll(self):
		t = self.pending()
		n = self.note(t)
		n.status = "Skipped"
		n.save(ignore_permissions=True)
		self.assertEqual(self.closed, ["POLL1"])

	def test_missing_message_id_not_stuck(self):
		self.closed_ticket()
		with patch.object(learn, "_llm", return_value=GOOD), \
				patch.object(learn.raven_bridge, "create_poll", return_value=("POLLX", None)):
			learn.run(NOW)
		self.assertFalse(frappe.db.get_value("HD Learned Note", {"status": "Pending"}, "poll_id"))


class TestPatch(unittest.TestCase):
	def test_patch_removes_html_empty_rows(self):
		from helpdesk.patches import remove_subject_only_embeddings

		frappe.db.delete("HD Ticket Embedding")
		a, b = make_ticket(subject="A").name, make_ticket(subject="B").name
		for t, rd in ((a, "<p><br></p>"), (b, "Problem: x\nFix: y")):
			frappe.get_doc({"doctype": "HD Ticket Embedding", "ticket": t, "subject": "s", "resolution_details": rd,
			                "embedding": "[1]"}).insert(ignore_permissions=True)
		remove_subject_only_embeddings.execute()
		self.assertEqual(frappe.get_all("HD Ticket Embedding", pluck="ticket"), [str(b)])
		frappe.db.rollback()


class TestProductScope(TestVotes):
	def test_note_takes_ticket_product_and_shows_it(self):
		if not frappe.db.exists("HD Product", "Raminian QA"):
			frappe.get_doc({"doctype": "HD Product", "product_name": "Raminian QA"}).insert(ignore_permissions=True)
		t = self.closed_ticket(hd_product="Raminian QA")
		with patch.object(learn, "_llm", return_value=GOOD), patch.object(learn.raven_bridge, "post") as post:
			learn.run(NOW)
		self.assertEqual(self.note(t).product, "Raminian QA")
		self.assertIn("Raminian QA", post.call_args.args[0])
		self.vote = {"Approve": ["a@example.com"]}
		learn.run(NOW)
		self.assertEqual(frappe.db.get_value("HD Ticket Embedding", {"ticket": t}, "product"), "Raminian QA")

	def test_product_edit_on_approved_note_reembeds(self):
		for p in ("Raminian QA", "Imara QA"):
			if not frappe.db.exists("HD Product", p):
				frappe.get_doc({"doctype": "HD Product", "product_name": p}).insert(ignore_permissions=True)
		t = self.closed_ticket(hd_product="Raminian QA")
		with patch.object(learn, "_llm", return_value=GOOD):
			learn.run(NOW)
		self.vote = {"Approve": ["a@example.com"]}
		learn.run(NOW)
		n = self.note(t)
		n.product = "Imara QA"
		n.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("HD Ticket Embedding", {"ticket": t}, "product"), "Imara QA")


class TestSearchScope(unittest.TestCase):
	def test_search_returns_only_same_product(self):
		from helpdesk.integrations import embeddings

		rows = [
			{"subject": "R", "resolution_details": "raminian fix", "embedding": [1.0, 0.0], "product": "Raminian"},
			{"subject": "E", "resolution_details": "erpnext fix", "embedding": [1.0, 0.0], "product": "ERPNext Support"},
			{"subject": "N", "resolution_details": "generic fix", "embedding": [1.0, 0.0], "product": None},
		]
		with patch.object(embeddings, "embed", return_value=[1.0, 0.0]), \
				patch.object(embeddings, "_load_embeddings", return_value=rows):
			got = [r["resolution_details"] for r in embeddings.search_resolved_tickets("q", top_k=5, product="Raminian")]
			self.assertEqual(got, ["raminian fix"])
			got = [r["resolution_details"] for r in embeddings.search_resolved_tickets("q", top_k=5)]
			self.assertEqual(got, ["generic fix"])
