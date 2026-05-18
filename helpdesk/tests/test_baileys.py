import frappe
import unittest
from unittest.mock import patch, MagicMock


class TestBaileysInitials(unittest.TestCase):

	def _get_baileys_ticket(self):
		name = frappe.db.get_value("HD Ticket", {"baileys_jid": ["!=", ""]}, "name")
		if not name:
			self.skipTest("No Baileys ticket in DB")
		return str(name)

	def _mock_requests_post(self, mock_req):
		mock_resp = MagicMock()
		mock_resp.json.return_value = {"messageId": "test-id"}
		mock_resp.raise_for_status.return_value = None
		mock_req.post.return_value = mock_resp

	def test_initials_appended_when_enabled(self):
		"""send_baileys_reply must append ^XX when append_agent_initials=1."""
		from helpdesk.integrations import baileys as b

		frappe.db.set_value("Baileys Gateway Settings", None, "append_agent_initials", 1)
		frappe.clear_cache()

		ticket = self._get_baileys_ticket()
		with patch("helpdesk.integrations.baileys._requests") as mock_req:
			self._mock_requests_post(mock_req)
			b.send_baileys_reply(ticket=ticket, message="hello")
			payload = mock_req.post.call_args.kwargs.get("json") or mock_req.post.call_args.args[1]
		self.assertIn("^", payload["message"])

	def test_initials_omitted_when_disabled(self):
		"""send_baileys_reply must NOT append ^XX when append_agent_initials=0."""
		from helpdesk.integrations import baileys as b

		frappe.db.set_value("Baileys Gateway Settings", None, "append_agent_initials", 0)
		frappe.clear_cache()

		ticket = self._get_baileys_ticket()
		with patch("helpdesk.integrations.baileys._requests") as mock_req:
			self._mock_requests_post(mock_req)
			b.send_baileys_reply(ticket=ticket, message="hello")
			payload = mock_req.post.call_args.kwargs.get("json") or mock_req.post.call_args.args[1]

		# Restore
		frappe.db.set_value("Baileys Gateway Settings", None, "append_agent_initials", 1)
		frappe.clear_cache()
		self.assertNotIn("^", payload["message"])
