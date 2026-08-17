"""Evolution-backed contact name resolution.

Covers the two passes added to sync_wa_contacts(): the cheap bulk contact-store
pull (chat/findContacts) and the per-number profile lookup (chat/fetchProfile)
that is the only thing able to surface a WhatsApp Business account's verified
name — the customer never sends one as pushName, so message history alone can
never produce it.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from helpdesk.integrations.wa import (
    _is_placeholder_name,
    _resolve_wa_names_via_profile,
    _sync_contacts_from_evolution_store,
    sync_wa_contact_names_from_evolution,
    sync_wa_contacts,
)

TEST_LINE = "_test-evosync-line"
TEST_LINE_B = "_test-evosync-line-b"
JID_A = "254700000001@s.whatsapp.net"
JID_B = "254700000002@s.whatsapp.net"
JID_GROUP = "254700000003-1234@g.us"

# The real payload from Evolution v2.3.7 that proved fetchProfile resolves a
# business name the message stream never carried.
REAL_PROFILE = {
    "wuid": "254785344671@s.whatsapp.net",
    "name": "Jenga Taifa Hardware",
    "numberExists": True,
    "isBusiness": True,
    "description": "",
}


def _resp(payload, ok=True, status=200):
    r = MagicMock()
    r.ok = ok
    r.status_code = status
    r.json.return_value = payload
    r.text = str(payload)
    return r


def _enabled_settings():
    s = MagicMock()
    s.enabled = True
    s.server_url = "http://evo.test"
    s.global_api_key = "k"
    return s


class _Base(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self._purge()
        for line in (TEST_LINE, TEST_LINE_B):
            if not frappe.db.exists("WA Line", line):
                frappe.get_doc({
                    "doctype": "WA Line",
                    "instance_name": line,
                    "instance_token": "_test-token",
                }).insert(ignore_permissions=True)
        frappe.db.commit()
        self.line = frappe.get_doc("WA Line", TEST_LINE)

    def tearDown(self):
        self._purge()

    def _purge(self):
        for jid in (JID_A, JID_B, JID_GROUP):
            frappe.db.delete("WA Message", {"jid": jid})
            frappe.db.delete("WA Contact", {"jid": jid})
        frappe.cache().delete_value("wa:sync_evo_contacts:lock")
        frappe.db.commit()

    def _contact(self, jid, custom_name="", phone=None):
        frappe.get_doc({
            "doctype": "WA Contact",
            "jid": jid,
            "phone": phone if phone is not None else jid.split("@")[0],
            "custom_name": custom_name,
            "company": "",
            "assigned_team": "",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def _message(self, jid, line=TEST_LINE):
        frappe.get_doc({
            "doctype": "WA Message",
            "direction": "Incoming",
            "jid": jid,
            "sender_jid": jid,
            "message": "hi",
            "content_type": "text",
            "status": "Pending",
            "line": line,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def _name_of(self, jid):
        return frappe.db.get_value("WA Contact", {"jid": jid}, "custom_name")


class TestPlaceholderName(unittest.TestCase):
    """The predicate that decides what counts as 'not really a name'."""

    def test_blank_and_digit_forms_are_placeholders(self):
        for value in ("", "   ", None, "254785344671", "+254 785 344671", "(254)785-344671"):
            self.assertTrue(_is_placeholder_name(value), f"{value!r} should be a placeholder")

    def test_real_names_are_not_placeholders(self):
        for value in ("Jenga Taifa Hardware", "Andrew", "A1 Motors", "254 Cafe"):
            self.assertFalse(_is_placeholder_name(value), f"{value!r} should be kept")


class TestProfileLookup(_Base):
    def test_real_business_payload_resolves_the_name(self):
        self._contact(JID_A)
        self._message(JID_A)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp(REAL_PROFILE)
            resolved = _resolve_wa_names_via_profile(self.line)

        self.assertEqual(resolved, 1)
        self.assertEqual(self._name_of(JID_A), "Jenga Taifa Hardware")

    def test_digits_only_name_is_replaced(self):
        self._contact(JID_A, custom_name="254700000001")
        self._message(JID_A)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp(REAL_PROFILE)
            _resolve_wa_names_via_profile(self.line)

        self.assertEqual(self._name_of(JID_A), "Jenga Taifa Hardware")

    def test_existing_real_name_is_never_overwritten(self):
        """An agent's manually typed name must survive every sync."""
        self._contact(JID_A, custom_name="Andrew (site foreman)")
        self._message(JID_A)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp(REAL_PROFILE)
            resolved = _resolve_wa_names_via_profile(self.line)

        self.assertEqual(resolved, 0)
        self.assertEqual(self._name_of(JID_A), "Andrew (site foreman)")
        self.assertFalse(
            sess.post.called,
            "a contact that already has a real name must not be queried at all — "
            "this is the rate-limit guarantee",
        )

    def test_number_that_does_not_exist_writes_nothing(self):
        self._contact(JID_A)
        self._message(JID_A)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp({"numberExists": False})
            resolved = _resolve_wa_names_via_profile(self.line)

        self.assertEqual(resolved, 0)
        self.assertFalse(self._name_of(JID_A))

    def test_nameless_contact_is_left_alone(self):
        """numberExists with an empty name is a real WhatsApp answer, not a failure."""
        self._contact(JID_A)
        self._message(JID_A)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp({"numberExists": True, "name": ""})
            resolved = _resolve_wa_names_via_profile(self.line)

        self.assertEqual(resolved, 0)
        self.assertFalse(self._name_of(JID_A))

    def test_http_error_on_one_contact_does_not_abort_the_rest(self):
        self._contact(JID_A)
        self._message(JID_A)
        self._contact(JID_B)
        self._message(JID_B)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.side_effect = [_resp({}, ok=False, status=500), _resp(REAL_PROFILE)]
            resolved = _resolve_wa_names_via_profile(self.line)

        self.assertEqual(resolved, 1)
        self.assertEqual(sess.post.call_count, 2)

    def test_limit_caps_the_number_of_lookups(self):
        self._contact(JID_A)
        self._message(JID_A)
        self._contact(JID_B)
        self._message(JID_B)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp(REAL_PROFILE)
            _resolve_wa_names_via_profile(self.line, limit=1)

        self.assertEqual(sess.post.call_count, 1, "the per-run cap must bound live lookups")

    def test_group_jids_are_never_queried(self):
        self._contact(JID_GROUP, phone="")
        self._message(JID_GROUP)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp(REAL_PROFILE)
            _resolve_wa_names_via_profile(self.line)

        self.assertFalse(sess.post.called)

    def test_contacts_from_another_line_are_not_queried(self):
        self._contact(JID_A)
        self._message(JID_A, line=TEST_LINE_B)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp(REAL_PROFILE)
            _resolve_wa_names_via_profile(self.line)

        self.assertFalse(sess.post.called)


class TestContactStorePass(_Base):
    def test_bare_list_envelope_is_parsed(self):
        self._contact(JID_A)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp([{"id": JID_A, "pushName": "Mary W"}])
            filled = _sync_contacts_from_evolution_store(self.line)

        self.assertEqual(filled, 1)
        self.assertEqual(self._name_of(JID_A), "Mary W")

    def test_records_envelope_is_parsed(self):
        self._contact(JID_A)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp(
                {"records": [{"remoteJid": JID_A, "pushName": "Mary W"}], "pages": 1}
            )
            filled = _sync_contacts_from_evolution_store(self.line)

        self.assertEqual(filled, 1)
        self.assertEqual(self._name_of(JID_A), "Mary W")

    def test_saved_name_beats_pushname(self):
        self._contact(JID_A)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp(
                [{"id": JID_A, "name": "Mary Wanjiku", "pushName": "mary✨"}]
            )
            _sync_contacts_from_evolution_store(self.line)

        self.assertEqual(self._name_of(JID_A), "Mary Wanjiku")

    def test_digit_pushname_is_not_written(self):
        self._contact(JID_A)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp([{"id": JID_A, "pushName": "254700000001"}])
            filled = _sync_contacts_from_evolution_store(self.line)

        self.assertEqual(filled, 0)
        self.assertFalse(self._name_of(JID_A))

    def test_groups_broadcasts_and_own_number_are_skipped(self):
        self.line.connected_user = JID_B
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp([
                {"id": JID_GROUP, "pushName": "Some Group"},
                {"id": "status@broadcast", "pushName": "Status"},
                {"id": JID_B, "pushName": "My Own Line"},
            ])
            filled = _sync_contacts_from_evolution_store(self.line)

        self.assertEqual(filled, 0)

    def test_http_failure_returns_empty_without_raising(self):
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp({}, ok=False, status=404)
            self.assertEqual(_sync_contacts_from_evolution_store(self.line), 0)


class TestOrchestration(_Base):
    def test_lock_reports_cooldown_rather_than_zero_names(self):
        """A skipped pass must be distinguishable from 'nothing to find'.

        Reporting the cooldown as 0 resolved names is what makes the button look
        broken — the exact failure this work exists to remove.
        """
        frappe.cache().set_value("wa:sync_evo_contacts:lock", 1, expires_in_sec=300)
        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            result = sync_wa_contact_names_from_evolution()

        self.assertEqual(result["resolved"], 0)
        self.assertTrue(result["cooldown"])
        self.assertFalse(sess.post.called)

    def test_disabled_settings_makes_no_requests(self):
        disabled = MagicMock()
        disabled.enabled = False
        disabled.server_url = ""
        with patch("helpdesk.integrations.wa._settings", return_value=disabled), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            result = sync_wa_contact_names_from_evolution()

        self.assertEqual(result["resolved"], 0)
        self.assertFalse(result["cooldown"], "disabled is not a cooldown")
        self.assertFalse(sess.post.called)

    def test_sync_wa_contacts_still_returns_its_original_keys(self):
        """The new counters are additive — existing callers must not break."""
        with patch(
            "helpdesk.integrations.wa._enrich_wa_contacts_from_frappe_contacts", return_value=3
        ), patch(
            "helpdesk.integrations.wa.sync_wa_contact_names_from_evolution",
            return_value={"resolved": 5, "cooldown": False},
        ), patch(
            "helpdesk.integrations.wa.frappe.db.sql", return_value=[]
        ):
            result = sync_wa_contacts()

        for key in ("created", "updated", "total"):
            self.assertIn(key, result)
        self.assertEqual(result["enriched"], 3)
        self.assertEqual(result["from_evolution"], 5)
        self.assertFalse(result["names_cooldown"])

    def test_one_broken_line_does_not_abort_the_others(self):
        self._contact(JID_A)
        self._message(JID_A)

        real_get_doc = frappe.get_doc

        def flaky_get_doc(doctype, name=None, *a, **kw):
            if doctype == "WA Line" and name == TEST_LINE_B:
                raise Exception("instance unreachable")
            return real_get_doc(doctype, name, *a, **kw)

        with patch("helpdesk.integrations.wa._settings", return_value=_enabled_settings()), \
             patch("helpdesk.integrations.wa.frappe.get_doc", side_effect=flaky_get_doc), \
             patch("helpdesk.integrations.wa.frappe.get_all", return_value=[TEST_LINE_B, TEST_LINE]), \
             patch("helpdesk.integrations.wa._evo_session") as sess:
            sess.post.return_value = _resp(REAL_PROFILE)
            result = sync_wa_contact_names_from_evolution()

        self.assertGreaterEqual(result["resolved"], 1, "the healthy line must still sync")
        self.assertEqual(self._name_of(JID_A), "Jenga Taifa Hardware")
