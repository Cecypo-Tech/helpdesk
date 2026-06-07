import frappe


@frappe.whitelist()
def clear(ticket: str | int | None = None, comment: str | None = None, notification: str | None = None):
    """
    Mark notifications as read. No arguments will clear all notifications for `user`.

    :param ticket: Ticket to clear notifications for
    :param comment: Comment to clear notifications for
    :param notification: A single HD Notification name to clear (used for non-ticket notifications)
    """
    filters = {"user_to": frappe.session.user, "read": False}
    if ticket:
        filters["reference_ticket"] = ticket
    if comment:
        filters["reference_comment"] = comment
    if notification:
        filters["name"] = notification
    for notification_name in frappe.get_all(
        "HD Notification", filters=filters, pluck="name"
    ):
        frappe.db.set_value(
            "HD Notification", notification_name, "read", 1, update_modified=False
        )
