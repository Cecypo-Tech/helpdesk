app_name = "helpdesk"
app_title = "Helpdesk"
app_publisher = "Frappe Technologies"
app_description = "Customer Service Software"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "hello@frappe.io"
app_license = "AGPLv3"
required_apps = ["telephony"]
require_type_annotated_api_methods = True

add_to_apps_screen = [
    {
        "name": "helpdesk",
        "logo": "/assets/helpdesk/desk/favicon.svg",
        "title": "Helpdesk",
        "route": "/helpdesk",
        "has_permission": "helpdesk.api.permission.has_app_permission",
    }
]

get_site_info = "helpdesk.activation.get_site_info"

after_install = "helpdesk.setup.install.after_install"
after_migrate = [
    "helpdesk.search.build_index_in_background",
    "helpdesk.search.download_corpus",
]


# Full Text Search
# ------------------

sqlite_search = ["helpdesk.search_sqlite.HelpdeskSearch"]

scheduler_events = {
    "all": [
        "helpdesk.search.build_index_if_not_exists",
        "helpdesk.search.download_corpus",
    ],
    "hourly": [
        "helpdesk.helpdesk.doctype.hd_task.hd_task.send_due_task_wpa_notifications",
        "helpdesk.integrations.outline.sync_outline_docs",
    ],
    "daily": [
        "helpdesk.helpdesk.doctype.hd_ticket.hd_ticket.close_tickets_after_n_days",
        "helpdesk.integrations.wa.enqueue_wa_sync",
        "helpdesk.integrations.embeddings.embed_resolved_tickets",
        "helpdesk.integrations.embeddings.embed_articles",
        "helpdesk.integrations.kb_autofill.promote_gaps_to_draft_articles",
        "helpdesk.helpdesk.doctype.hd_task.hd_task.send_manager_task_digest",
    ],
}


website_route_rules = [
    {
        "from_route": "/helpdesk/<path:app_path>",
        "to_route": "helpdesk",
    },
]

# Serves the PWA service worker at /helpdesk/sw.js so its scope covers the app.
# Registered from the asset path it is built into, the worker controlled nothing
# under /helpdesk/ — see helpdesk/service_worker.py. Custom renderers are tried
# before Frappe's own, so this claims the route ahead of the SPA rule above.
page_renderer = ["helpdesk.service_worker.ServiceWorkerPage"]

user_invitation = {
    "allowed_roles": {
        "Agent Manager": ["Agent", "Agent Manager"],
        "System Manager": ["Agent", "Agent Manager", "System Manager"],
    },
    "after_accept": "helpdesk.helpdesk.hooks.user_invitation.after_accept",
}

doc_events = {
    "Contact": {
        "before_insert": "helpdesk.overrides.contact.before_insert",
        # before_save, not validate: Contact.validate() is what fills mobile_no
        # and phone from the phone_nos child rows, so reading them any earlier
        # would store a stale suffix.
        "before_save": "helpdesk.integrations.wa.set_contact_phone_suffix",
    },
    "Assignment Rule": {
        "on_trash": "helpdesk.extends.assignment_rule.on_assignment_rule_trash",
        "validate": "helpdesk.extends.assignment_rule.on_assignment_rule_validate",
    },
    "WhatsApp Message": {
        "before_insert": [
            # Must run before anything else reads the row: it decides whether
            # this delivery is a duplicate Meta already sent us.
            "helpdesk.integrations.wa.flag_duplicate_whatsapp_message",
            "helpdesk.integrations.wa.set_wa_message_normalized_phone",
        ],
        "after_insert": [
            "helpdesk.integrations.wa.on_whatsapp_message_insert",
            "helpdesk.integrations.bot.handle_whatsapp_message",
        ],
        "on_update": "helpdesk.integrations.wa.on_whatsapp_message_update",
    },
    "WA Message": {
        "after_insert": "helpdesk.integrations.bot.handle_wa_message",
    },
    "HD Article": {
        "on_update": "helpdesk.integrations.embeddings.on_article_update",
    },
    "Notification Log": {
        "before_insert": "helpdesk.extends.notification_log.before_insert",
    },
    "Customer": {
        "after_insert": "helpdesk.integrations.erpnext.customer.after_insert",
        "on_update": "helpdesk.integrations.erpnext.customer.on_update",
        "before_rename": "helpdesk.integrations.erpnext.customer.before_rename",
        "after_rename": "helpdesk.integrations.erpnext.customer.after_rename",
        "on_trash": "helpdesk.integrations.erpnext.customer.on_trash",
    },
    "User Permission": {
        "before_validate": "helpdesk.integrations.erpnext.user_permission.before_validate",
        "after_insert": "helpdesk.integrations.erpnext.user_permission.after_insert",
        "on_update": "helpdesk.integrations.erpnext.user_permission.on_update",
        "on_trash": "helpdesk.integrations.erpnext.user_permission.on_trash",
    },
    "DocShare": {
        "before_validate": "helpdesk.integrations.erpnext.doc_share.before_validate",
        "after_insert": "helpdesk.integrations.erpnext.doc_share.after_insert",
        "on_update": "helpdesk.integrations.erpnext.doc_share.on_update",
        "on_trash": "helpdesk.integrations.erpnext.doc_share.on_trash",
    },
}

has_permission = {
    "HD Ticket": "helpdesk.helpdesk.doctype.hd_ticket.hd_ticket.has_permission",
    "HD Saved Reply": "helpdesk.helpdesk.doctype.hd_saved_reply.hd_saved_reply.has_permission",
}

permission_query_conditions = {
    "HD Ticket": "helpdesk.helpdesk.doctype.hd_ticket.hd_ticket.permission_query",
    "HD Saved Reply": "helpdesk.helpdesk.doctype.hd_saved_reply.hd_saved_reply.permission_query",
}

# DocType Class
# ---------------
# Override standard doctype classes
override_doctype_class = {
    "Email Account": "helpdesk.overrides.email_account.CustomEmailAccount",
}

ignore_links_on_delete = [
    "HD Notification",
    "HD Ticket Comment",
]

# setup wizard
# setup_wizard_requires = "assets/helpdesk/js/setup_wizard.js"
# setup_wizard_stages = "helpdesk.setup.setup_wizard.get_setup_stages"
setup_wizard_complete = "helpdesk.setup.setup_wizard.setup_complete"


# Testing
# ---------------

before_tests = "helpdesk.test_utils.before_tests"
auth_hooks = ["helpdesk.auth.authenticate"]

override_whitelisted_methods = {
    "frappe.core.doctype.user.user.test_password_strength": "helpdesk.overrides.test_password_strength",
}

fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            ["dt", "in", ["HD Ticket", "Customer", "HD Task"]],
            ["fieldname", "in", ["baileys_jid", "baileys_line", "helpdesk_notes"]],
        ],
    },
    {
        "doctype": "Custom Field",
        "filters": [["dt", "=", "WhatsApp Account"], ["fieldname", "=", "bot_enabled"]],
    },
    {
        "doctype": "Custom Field",
        "filters": [
            ["dt", "=", "WhatsApp Message"],
            ["fieldname", "=", "normalized_phone"],
        ],
    },
    {
        "doctype": "Custom Field",
        "filters": [
            ["dt", "in", ["Contact", "Contact Phone"]],
            ["fieldname", "=", "phone_suffix"],
        ],
    },
    # Indexes WhatsApp Message.message_id for the duplicate-delivery guard. Also
    # created by add_whatsapp_message_id_index, but a fresh install marks patches
    # as already-run without executing them, so the fixture is what carries it there.
    {
        "doctype": "Property Setter",
        "filters": [
            ["name", "=", "WhatsApp Message-message_id-search_index"],
        ],
    },
]

# WhatsApp Templates are seeded via a patch (seed_whatsapp_templates) instead of
# fixtures because WhatsAppTemplates.after_insert() immediately POSTs to Meta's API,
# which breaks fixture import. The patch uses db_insert() to bypass that hook.
