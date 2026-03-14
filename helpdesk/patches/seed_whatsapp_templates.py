"""Seed WhatsApp Helpdesk template records without triggering Meta API submission.

Uses db_insert() to bypass WhatsAppTemplates.after_insert(), which would POST to
Meta immediately. The admin should edit the body with real product names, set the
WhatsApp account, then submit to Meta manually from the frappe_whatsapp UI.
"""

import frappe


TEMPLATES = [
	{
		"name": "hd_tech_support_welcome-en",
		"template_name": "hd_tech_support_welcome",
		"actual_name": "hd_tech_support_welcome",
		"template": (
			"Hello {{1}},\n\n"
			"Thank you for reaching out! Your support ticket #{{2}} has been created "
			"and our team will get back to you shortly.\n\n"
			"Which of our products does this issue relate to?\n\n"
			"1. Product A\n"
			"2. Product B\n"
			"3. Product C\n"
			"4. Product D\n"
			"5. Product E\n"
			"6. Product F\n\n"
			"Reply with the number or name of your product."
		),
		"category": "UTILITY",
		"language": "English",
		"language_code": "en",
		"header_type": "TEXT",
		"header": "Tech Support",
		"footer": "Reply STOP to unsubscribe",
		"sample_values": "Customer Name,TICKET-0001",
		"buttons": [
			{"button_type": "Quick Reply", "button_label": "Product A"},
			{"button_type": "Quick Reply", "button_label": "Product B"},
			{"button_type": "Quick Reply", "button_label": "Other"},
		],
	},
	{
		"name": "hd_service_rating-en",
		"template_name": "hd_service_rating",
		"actual_name": "hd_service_rating",
		"template": (
			"Hello {{1}},\n\n"
			"Your support ticket #{{2}} has been resolved. "
			"We'd love to hear your feedback!\n\n"
			"How would you rate our support?"
		),
		"category": "UTILITY",
		"language": "English",
		"language_code": "en",
		"header_type": "TEXT",
		"header": "Rate Our Service",
		"sample_values": "Customer Name,TICKET-0001",
		"buttons": [
			{"button_type": "Quick Reply", "button_label": "Excellent"},
			{"button_type": "Quick Reply", "button_label": "Good"},
			{"button_type": "Quick Reply", "button_label": "Poor"},
		],
	},
	{
		"name": "hd_tech_support_followup-en",
		"template_name": "hd_tech_support_followup",
		"actual_name": "hd_tech_support_followup",
		"template": (
			"Hello {{1}},\n\n"
			"This is a follow-up on your support ticket #{{2}}. "
			"Is your issue resolved?\n\n"
			"Please let us know and we'll be happy to help further."
		),
		"category": "UTILITY",
		"language": "English",
		"language_code": "en",
		"header_type": "TEXT",
		"header": "Ticket Follow-Up",
		"sample_values": "Customer Name,TICKET-0001",
		"buttons": [
			{"button_type": "Quick Reply", "button_label": "Yes, resolved"},
			{"button_type": "Quick Reply", "button_label": "No, still open"},
		],
	},
]


def execute():
	if not frappe.db.exists("DocType", "WhatsApp Templates"):
		return

	for tmpl in TEMPLATES:
		if frappe.db.exists("WhatsApp Templates", tmpl["name"]):
			continue

		buttons = tmpl.pop("buttons", [])

		doc = frappe.new_doc("WhatsApp Templates")
		doc.update(tmpl)
		doc.db_insert()

		for btn in buttons:
			child = frappe.new_doc("WhatsApp Button")
			child.update(btn)
			child.parent = doc.name
			child.parenttype = "WhatsApp Templates"
			child.parentfield = "buttons"
			child.db_insert()

		tmpl["buttons"] = buttons  # restore for safety
