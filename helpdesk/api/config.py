import frappe


@frappe.whitelist(allow_guest=True)
def get_config():
    fields = [
        "brand_name",
        "brand_logo",
        "favicon",
        "prefer_knowledge_base",
        "setup_complete",
        "skip_email_workflow",
        "is_feedback_mandatory",
        "restrict_tickets_by_agent_group",
        "assign_within_team",
        "disable_saved_replies_global_scope",
        "enable_comment_reactions",
    ]
    res = frappe.get_value(doctype="HD Settings", fieldname=fields, as_dict=True)

    res.favicon = (
        res.favicon
        or frappe.db.get_single_value("Website Settings", "favicon")
        or "/assets/helpdesk/desk/favicon.svg"
    )

    # Drives the "Backup" sidebar link, which points at the Imara Backup portal at
    # /backup. Presence of the app is the only gate — the link is shown to every
    # user, and /backup does its own permission handling.
    res.backup_portal_enabled = "imara_backup" in frappe.get_installed_apps()

    return res
