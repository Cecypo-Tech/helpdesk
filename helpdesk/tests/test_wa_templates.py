import re
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.integrations import wa

TEMPLATES_POST = (
	"frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_templates"
	".whatsapp_templates.make_post_request"
)
TEMPLATES_REQUEST = (
	"frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_templates"
	".whatsapp_templates.make_request"
)
MESSAGE_POST = (
	"frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message"
	".whatsapp_message.make_post_request"
)


class TestWATemplates(FrappeTestCase):
	"""Cover the WABA template render/preview/send path.

	Nothing here touches the network: frappe_whatsapp calls Meta from
	WhatsAppTemplates.after_insert and WhatsAppMessage.notify, and both are
	patched at the make_post_request boundary.
	"""

	@classmethod
	def tearDownClass(cls):
		# Safety net. These templates are created with status APPROVED, so any
		# that survive a failed cleanup show up in the agent's real template
		# picker. Sweep the whole fixture namespace rather than trusting that
		# every per-test cleanup ran.
		if frappe.db.exists("DocType", "WhatsApp Templates"):
			leftovers = [
				row.name
				for row in frappe.get_all(
					"WhatsApp Templates", fields=["name", "template_name"]
				)
				if re.match(r"^tpl_[0-9a-f]{8}$", row.template_name or "")
			]
			if leftovers:
				with patch(
					TEMPLATES_POST, return_value={"id": "1", "status": "APPROVED"}
				), patch(TEMPLATES_REQUEST, return_value={"success": True}):
					for name in leftovers:
						frappe.delete_doc(
							"WhatsApp Templates", name, ignore_permissions=True, force=True
						)
				frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("DocType", "WhatsApp Templates"):
			self.skipTest("frappe_whatsapp is not installed")

		self.ticket = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": "Template render test ticket",
			"raised_by": "wa-template-test@example.com",
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", self.ticket.name, ignore_permissions=True, force=True
		)

	def _make_template(self, body, sample_values=None, field_names=None):
		with patch(TEMPLATES_POST, return_value={"id": "1", "status": "APPROVED"}):
			doc = frappe.get_doc({
				"doctype": "WhatsApp Templates",
				"template_name": f"tpl_{frappe.generate_hash(length=8)}",
				"template": body,
				"language_code": "en",
				"category": "UTILITY",
				"sample_values": sample_values or "",
				"field_names": field_names or "",
			}).insert(ignore_permissions=True)
		# WhatsAppTemplates.on_trash calls Meta to delete the template, so the
		# cleanup has to stay behind the patch too.
		self.addCleanup(self._delete_template, doc.name)
		return doc

	def _delete_template(self, name):
		with patch(TEMPLATES_POST, return_value={"id": "1", "status": "APPROVED"}), patch(
			TEMPLATES_REQUEST, return_value={"success": True}
		):
			frappe.delete_doc(
				"WhatsApp Templates", name, ignore_permissions=True, force=True
			)

	def _make_incoming(self, age_hours=0):
		doc = frappe.get_doc({
			"doctype": "WhatsApp Message",
			"type": "Incoming",
			"from": "254700000000",
			"message": "hello",
			"content_type": "text",
			"message_id": f"wamid.tpl.{frappe.generate_hash(length=10)}",
			"status": "received",
			"reference_doctype": "HD Ticket",
			"reference_name": self.ticket.name,
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "WhatsApp Message", doc.name, ignore_permissions=True, force=True
		)
		if age_hours:
			frappe.db.set_value(
				"WhatsApp Message", doc.name, "creation",
				frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-age_hours),
				update_modified=False,
			)
		return doc

	# ── rendering ─────────────────────────────────────────────────────────

	def test_template_with_variables_renders_without_name_error(self):
		# Regression: json.dumps was called with no `json` import in wa.py, so
		# every template carrying sample_values raised NameError.
		tpl = self._make_template(
			"Hello, your ticket {{1}} is open.",
			sample_values="Ticket",
			field_names="name",
		)

		message, body_param = wa._render_template_for_ticket(self.ticket.name, tpl.name)

		self.assertIn(str(self.ticket.name), message)
		self.assertNotIn("{{1}}", message)
		self.assertIn(str(self.ticket.name), body_param)

	def test_template_without_variables_renders_body_unchanged(self):
		tpl = self._make_template("We have received your request.")

		message, body_param = wa._render_template_for_ticket(self.ticket.name, tpl.name)

		self.assertEqual(message, "We have received your request.")
		self.assertIsNone(body_param)

	def test_preview_returns_exactly_what_render_produces(self):
		tpl = self._make_template(
			"Ticket {{1}} update.", sample_values="Ticket", field_names="name"
		)

		rendered, _body = wa._render_template_for_ticket(self.ticket.name, tpl.name)
		preview = wa.preview_template_for_ticket(self.ticket.name, tpl.name)

		self.assertEqual(preview["message"], rendered)

	def test_variables_without_field_names_raise_a_configuration_error(self):
		tpl = self._make_template("Hi {{1}}", sample_values="Name", field_names="")

		with self.assertRaises(frappe.ValidationError):
			wa._render_template_for_ticket(self.ticket.name, tpl.name)

	# ── window detection ──────────────────────────────────────────────────

	def test_window_is_closed_when_no_incoming_message_exists(self):
		# Outgoing-only is a business-initiated conversation, which Meta allows
		# only via a template. Defaulting this open handed the agent a free-form
		# box that could never succeed.
		self.assertFalse(wa._fw_reply_window_open(self.ticket.name))

	def test_window_is_open_within_24h_of_the_last_incoming(self):
		self._make_incoming(age_hours=2)
		self.assertTrue(wa._fw_reply_window_open(self.ticket.name))

	def test_window_is_closed_beyond_24h(self):
		self._make_incoming(age_hours=30)
		self.assertFalse(wa._fw_reply_window_open(self.ticket.name))

	def test_ticket_info_no_longer_exposes_the_retired_setting(self):
		self._make_incoming(age_hours=1)
		info = wa.get_whatsapp_ticket_info(self.ticket.name)
		self.assertNotIn("allow_template_outside_window", info)
		self.assertTrue(info["reply_window_open"])

	# ── server-side enforcement ───────────────────────────────────────────

	def test_free_form_reply_is_refused_past_the_window(self):
		self._make_incoming(age_hours=30)

		with patch(MESSAGE_POST) as post:
			with self.assertRaises(frappe.ValidationError):
				wa._send_fw_reply(self.ticket.name, "too late")

		post.assert_not_called()

	def test_free_form_reply_is_allowed_inside_the_window(self):
		self._make_incoming(age_hours=1)

		with patch(MESSAGE_POST, return_value={"messages": [{"id": "wamid.test"}]}) as post:
			result = wa._send_fw_reply(self.ticket.name, "still open")

		self.assertTrue(post.called)
		self.assertTrue(result["name"])
		self.addCleanup(
			frappe.delete_doc, "WhatsApp Message", result["name"],
			ignore_permissions=True, force=True,
		)
