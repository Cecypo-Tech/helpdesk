import frappe
import unittest

from helpdesk.integrations.wa import get_whatsapp_messages

TEST_JID = "_test-pagination@g.us"
BASE_TS = "2026-01-01 10:00:"


class TestWaMessagesPagination(unittest.TestCase):
    """Pagination of the WA Line (jid) path of get_whatsapp_messages.

    Builds its own conversation rather than leaning on whatever the site
    happens to contain, so the awkward shapes (a timestamp tie, a reaction
    outside the page, a reply to an ancient message, a cross-line duplicate)
    are actually present and asserted on every run.
    """

    def setUp(self):
        frappe.set_user("Administrator")
        self._purge()
        # 10 real messages, oldest → newest, one second apart.
        # msg 5 and 6 deliberately share a creation timestamp: `creation` is not
        # unique in practice (bulk-imported messages land in the same second),
        # and a cursor that ignores that silently drops the tied sibling.
        self.msgs = []
        for i in range(1, 11):
            second = i if i < 6 else i - 1  # → 5 and 6 both at :05
            name = self._msg(
                message_id=f"_test-pg-{i:02d}",
                text=f"message {i}",
                creation=f"{BASE_TS}{second:02d}",
            )
            self.msgs.append(name)

        # A cross-line duplicate: same message_id as #3, stored again by another
        # line. Must never surface twice.
        self._msg(message_id="_test-pg-03", text="message 3", creation=f"{BASE_TS}03")

        # A reaction on the newest message. Reactions are separate rows, so they
        # must not eat a page slot, and must still be found for the page.
        self._msg(
            message_id="_test-pg-react",
            text="👍",
            creation=f"{BASE_TS}30",
            content_type="reaction",
            reply_to_message_id="_test-pg-10",
        )

        # The newest message quotes the oldest — its target falls outside any
        # small page, so it has to come back via reply_targets.
        frappe.db.set_value(
            "WA Message",
            self.msgs[-1],
            "reply_to_message_id",
            "_test-pg-01",
            update_modified=False,
        )
        frappe.db.commit()

    def tearDown(self):
        self._purge()

    def _purge(self):
        frappe.db.delete("WA Message", {"jid": TEST_JID})
        frappe.db.commit()

    def _msg(self, message_id, text, creation, content_type="text", reply_to_message_id=""):
        doc = frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Incoming",
            "jid": TEST_JID,
            "message": text,
            "content_type": content_type,
            "message_id": message_id,
            "reply_to_message_id": reply_to_message_id,
            "status": "Delivered",
        }).insert(ignore_permissions=True)
        # creation is set by the framework on insert; force it so the ordering
        # (and the tie) is deterministic rather than dependent on wall clock.
        frappe.db.set_value("WA Message", doc.name, "creation", creation, update_modified=False)
        return doc.name

    @staticmethod
    def _bubbles(payload):
        return [m for m in payload["messages"] if m["content_type"] != "reaction"]

    # ── backward compatibility ───────────────────────────────────────────────

    def test_without_limit_returns_bare_list(self):
        """Callers that pass no limit (e.g. the WABA ticket tab) must keep the list shape."""
        out = get_whatsapp_messages(jid=TEST_JID)
        self.assertIsInstance(out, list)

    def test_with_limit_returns_paginated_dict(self):
        out = get_whatsapp_messages(jid=TEST_JID, limit=3)
        self.assertIsInstance(out, dict)
        self.assertEqual({"messages", "has_more", "reply_targets"}, set(out))

    # ── paging ───────────────────────────────────────────────────────────────

    def test_page_is_newest_first_and_ordered_oldest_to_newest(self):
        page = get_whatsapp_messages(jid=TEST_JID, limit=3)
        bubbles = self._bubbles(page)
        self.assertEqual(["message 8", "message 9", "message 10"], [m["message"] for m in bubbles])
        self.assertTrue(page["has_more"])

    def test_reactions_do_not_consume_page_slots(self):
        """A reaction row must not count against `limit` — it isn't a bubble."""
        page = get_whatsapp_messages(jid=TEST_JID, limit=3)
        self.assertEqual(3, len(self._bubbles(page)))
        reactions = [m for m in page["messages"] if m["content_type"] == "reaction"]
        self.assertEqual(1, len(reactions), "reaction for a message in the page should be returned")

    def test_reaction_is_returned_even_though_it_sits_outside_the_page_window(self):
        # The reaction's own creation (:30) is newer than every message, so it is
        # only found by targeting the page's messages, not by timestamp range.
        page = get_whatsapp_messages(jid=TEST_JID, limit=3)
        reaction = next(m for m in page["messages"] if m["content_type"] == "reaction")
        self.assertEqual("_test-pg-10", reaction["reply_to_message_id"])

    # ── the bug that nearly shipped ──────────────────────────────────────────

    def _walk(self, limit):
        """Page back through the whole conversation, returning every row seen."""
        rows = []
        cursor = cursor_name = None
        for _ in range(30):  # generous bound for 10 messages
            page = get_whatsapp_messages(
                jid=TEST_JID, limit=limit, before=cursor, before_name=cursor_name
            )
            bubbles = self._bubbles(page)
            if not bubbles:
                break
            rows.extend(bubbles)
            if not page["has_more"]:
                break
            cursor, cursor_name = bubbles[0]["creation"], bubbles[0]["name"]
        return rows

    def test_full_walk_recovers_every_message(self):
        """Paging back through history must lose nothing, at any page size.

        Swept across page sizes on purpose: whether the tied pair straddles a
        page boundary depends on where `limit` happens to land, so a single size
        can miss the tied-cursor bug entirely and pass against broken code.

        Deduped by message_id because that is the actual contract — a message
        stored by two WA Lines is two rows, and the server can only dedupe within
        a page (a stateless cursor can't know what earlier pages returned). The
        client folds them by message_id on merge; this mirrors that.
        """
        expected = {f"_test-pg-{i:02d}" for i in range(1, 11)}
        for limit in range(1, 7):
            with self.subTest(limit=limit):
                rows = self._walk(limit)

                names = [m["name"] for m in rows]
                self.assertEqual(
                    len(names),
                    len(set(names)),
                    "the same row came back on two pages — the cursor is not advancing cleanly",
                )
                self.assertEqual(
                    expected,
                    {m["message_id"] for m in rows},
                    "paging back did not recover exactly the full conversation",
                )

    def test_cross_line_duplicate_may_straddle_pages_and_is_client_deduped(self):
        """Documents the known limit of per-page dedup.

        At limit=1 the two copies of the same message land on separate pages.
        That's expected and safe (the client keys by message_id), but if this
        ever changes, the client-side merge assumption needs revisiting.
        """
        rows = self._walk(1)
        ids = [m["message_id"] for m in rows]
        self.assertEqual(2, ids.count("_test-pg-03"), "fixture should split the duplicate across pages")
        # …and the two copies are genuinely distinct rows, not one row twice.
        dupes = [m["name"] for m in rows if m["message_id"] == "_test-pg-03"]
        self.assertEqual(2, len(set(dupes)))

    def test_tied_creation_timestamps_page_losslessly(self):
        """Two messages sharing a creation timestamp must both survive a boundary."""
        tied = frappe.db.get_all(
            "WA Message",
            filters={"jid": TEST_JID, "creation": f"{BASE_TS}05"},
            fields=["name", "message_id"],
            order_by="name desc",
        )
        self.assertEqual(2, len(tied), "fixture should produce exactly one tied pair")

        # Page starting at the newer sibling; the older one must come back.
        page = get_whatsapp_messages(
            jid=TEST_JID, limit=3, before=f"{BASE_TS}05", before_name=tied[0]["name"]
        )
        returned = {m["message_id"] for m in page["messages"]}
        self.assertIn(
            tied[1]["message_id"],
            returned,
            "the tied sibling was skipped — cursor is not breaking ties on name",
        )

    # ── replies and dedup ────────────────────────────────────────────────────

    def test_reply_target_outside_page_is_returned_separately(self):
        page = get_whatsapp_messages(jid=TEST_JID, limit=3)
        target_ids = {m["message_id"] for m in page["reply_targets"]}
        self.assertIn("_test-pg-01", target_ids, "quoted message older than the page must be fetched")

        # …and must not be rendered as a bubble in its own right.
        self.assertNotIn("_test-pg-01", [m["message_id"] for m in self._bubbles(page)])

    def test_reply_target_not_refetched_when_already_in_page(self):
        """A quote resolvable inside the page shouldn't cost an extra row."""
        page = get_whatsapp_messages(jid=TEST_JID, limit=10)
        in_page = {m["message_id"] for m in page["messages"]}
        for target in page["reply_targets"]:
            self.assertNotIn(target["message_id"], in_page)

    def test_cross_line_duplicate_appears_once(self):
        """The same message stored by two WA Lines must render once."""
        page = get_whatsapp_messages(jid=TEST_JID, limit=10)
        ids = [m["message_id"] for m in self._bubbles(page)]
        self.assertEqual(1, ids.count("_test-pg-03"))

        legacy = get_whatsapp_messages(jid=TEST_JID)
        legacy_ids = [m["message_id"] for m in legacy if m.get("message_id")]
        self.assertEqual(len(legacy_ids), len(set(legacy_ids)))

    def test_paginated_page_matches_tail_of_unpaginated_list(self):
        """Pagination must not change which messages are shown, only how many."""
        legacy = [m for m in get_whatsapp_messages(jid=TEST_JID) if m["content_type"] != "reaction"]
        page = self._bubbles(get_whatsapp_messages(jid=TEST_JID, limit=4))
        self.assertEqual([m["name"] for m in legacy[-4:]], [m["name"] for m in page])
