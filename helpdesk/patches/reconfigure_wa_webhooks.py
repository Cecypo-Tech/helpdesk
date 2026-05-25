import frappe


def execute():
	"""Re-register webhooks for all WA Lines to point to the new wa.webhook endpoint.

	Previous versions sent Evolution server webhooks to
	helpdesk.integrations.evolution.webhook which no longer exists.
	"""
	if not frappe.db.exists("DocType", "WA Line"):
		return
	if not frappe.db.exists("DocType", "WA API Settings"):
		return

	try:
		settings = frappe.get_cached_doc("WA API Settings")
		if not settings.enabled or not settings.server_url:
			return
	except Exception:
		return

	lines = frappe.get_all("WA Line", pluck="name")
	for line_name in lines:
		try:
			from helpdesk.integrations.wa import configure_wa_webhook
			configure_wa_webhook(line_name)
			frappe.logger().info(f"reconfigure_wa_webhooks: updated {line_name}")
		except Exception as e:
			frappe.log_error(f"reconfigure_wa_webhooks: failed for {line_name}: {e}", "WA Webhook")
