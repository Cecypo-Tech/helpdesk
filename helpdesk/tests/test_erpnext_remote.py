"""HTTP client for the ERPNext bench.

Helpdesk and ERPNext are separate benches, so this is a network call, not an
app import. Everything here must fail OPEN: an unreachable or misconfigured
ERPNext leaves the helpdesk working, because nothing in this feature is allowed
to block a ticket, a reply, or a bot answer.

Deliberately a sibling of helpdesk/integrations/erpnext/ rather than inside it.
That package is upstream's SAME-bench integration, gated on erpnext being
installed locally; mixing the two would invite exactly the confusion this
separation avoids.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from helpdesk.integrations import erpnext_remote

DOCTYPE = "ERPNext Sync Settings"


def configured(**overrides):
    base = frappe._dict(
        enabled=1,
        server_url="https://erp.example.com",
        api_key="KEY",
        sync_interval_hours=6,
    )
    base.update(overrides)
    return base


def response(payload, ok=True, status=200):
    r = MagicMock()
    r.ok = ok
    r.status_code = status
    r.json.return_value = payload
    r.text = str(payload)
    return r


class TestIsConfigured(unittest.TestCase):
    def test_disabled_is_not_configured(self):
        with patch.object(erpnext_remote, "settings", return_value=configured(enabled=0)):
            self.assertFalse(erpnext_remote.is_configured())

    def test_missing_url_is_not_configured(self):
        with patch.object(erpnext_remote, "settings", return_value=configured(server_url="")):
            self.assertFalse(erpnext_remote.is_configured())

    def test_missing_key_is_not_configured(self):
        with patch.object(erpnext_remote, "settings", return_value=configured(api_key="")):
            self.assertFalse(erpnext_remote.is_configured())

    def test_fully_populated_is_configured(self):
        with patch.object(erpnext_remote, "settings", return_value=configured()), \
             patch.object(erpnext_remote, "api_secret", return_value="SECRET"):
            self.assertTrue(erpnext_remote.is_configured())


class TestCall(unittest.TestCase):
    def setUp(self):
        p = patch.object(erpnext_remote, "settings", return_value=configured())
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(erpnext_remote, "api_secret", return_value="SECRET")
        p2.start()
        self.addCleanup(p2.stop)

    def test_unwraps_the_frappe_message_envelope(self):
        """Frappe wraps whitelisted return values in {"message": ...}; callers
        should never have to know that."""
        with patch.object(erpnext_remote, "session") as s:
            s.get.return_value = response({"message": {"customers": [1, 2]}})
            out = erpnext_remote.call("helpdesk_customer_sync", {"limit": 2})

        self.assertTrue(out["ok"])
        self.assertEqual(out["data"], {"customers": [1, 2]})

    def test_sends_token_auth_and_hits_the_method_path(self):
        with patch.object(erpnext_remote, "session") as s:
            s.get.return_value = response({"message": {}})
            erpnext_remote.call("helpdesk_customer_standing", {"customer": "X"})

        url = s.get.call_args[0][0]
        headers = s.get.call_args.kwargs["headers"]
        self.assertEqual(url, "https://erp.example.com/api/method/helpdesk_customer_standing")
        self.assertEqual(headers["Authorization"], "token KEY:SECRET")
        self.assertEqual(s.get.call_args.kwargs["params"], {"customer": "X"})

    def test_http_error_fails_open_with_a_reason(self):
        with patch.object(erpnext_remote, "session") as s:
            s.get.return_value = response({}, ok=False, status=403)
            out = erpnext_remote.call("helpdesk_customer_sync")

        self.assertFalse(out["ok"])
        self.assertIn("403", out["error"])
        self.assertIsNone(out["data"])

    def test_network_failure_fails_open_and_does_not_raise(self):
        """The whole point: ERPNext being down must never surface as an
        exception in a ticket save or a bot reply."""
        with patch.object(erpnext_remote, "session") as s:
            s.get.side_effect = OSError("connection refused")
            out = erpnext_remote.call("helpdesk_customer_sync")

        self.assertFalse(out["ok"])
        self.assertIn("connection refused", out["error"])

    def test_unconfigured_call_short_circuits_without_touching_the_network(self):
        with patch.object(erpnext_remote, "settings", return_value=configured(enabled=0)), \
             patch.object(erpnext_remote, "session") as s:
            out = erpnext_remote.call("helpdesk_customer_sync")

        self.assertFalse(out["ok"])
        s.get.assert_not_called()

    def test_trailing_slash_on_server_url_does_not_double_up(self):
        with patch.object(
            erpnext_remote, "settings", return_value=configured(server_url="https://erp.example.com/")
        ), patch.object(erpnext_remote, "session") as s:
            s.get.return_value = response({"message": {}})
            erpnext_remote.call("helpdesk_customer_sync")

        self.assertEqual(
            s.get.call_args[0][0],
            "https://erp.example.com/api/method/helpdesk_customer_sync",
        )


class TestTestConnection(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")

    def test_reports_success_with_a_customer_count(self):
        with patch.object(erpnext_remote, "settings", return_value=configured()), \
             patch.object(erpnext_remote, "api_secret", return_value="SECRET"), \
             patch.object(erpnext_remote, "session") as s:
            s.get.return_value = response({"message": {"count": 1, "customers": [{"customer": "A"}]}})
            out = erpnext_remote.test_connection()

        self.assertTrue(out["ok"])

    def test_reports_the_reason_on_failure_rather_than_raising(self):
        with patch.object(erpnext_remote, "settings", return_value=configured()), \
             patch.object(erpnext_remote, "api_secret", return_value="SECRET"), \
             patch.object(erpnext_remote, "session") as s:
            s.get.return_value = response({}, ok=False, status=401)
            out = erpnext_remote.test_connection()

        self.assertFalse(out["ok"])
        self.assertIn("401", out["error"])
