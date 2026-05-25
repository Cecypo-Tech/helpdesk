# Compatibility shim — Evolution server webhooks are registered to
# helpdesk.integrations.evolution.webhook (the old module path).
# This re-exports the handler from wa.py so existing instances don't break
# until their webhook URLs are updated via configure_wa_webhook().

from helpdesk.integrations.wa import webhook  # noqa: F401
