# Compatibility shim — gateway instances registered to
# helpdesk.integrations.baileys.webhook (the old module path).
# Re-exports the handler from wa.py so existing gateway configs keep working
# until their webhook URLs are updated.

from helpdesk.integrations.wa import webhook  # noqa: F401
