from urllib.parse import quote

import frappe
from frappe.model.document import Document


class HDNotification(Document):
    def format_message(self):
        user_from = self.get_from()
        if self.notification_type == "Mention":
            if self.reference_comment:
                return f"{user_from} mentioned you in a comment"
            return f"{user_from} mentioned you"
        return ""

    def get_from(self):
        return frappe.db.get_value(
            "User", {"name": self.user_from}, fieldname="full_name"
        )

    def get_button_label(self):
        if self.reference_comment:
            return "See Comment"
        return "Visit"

    def get_url(self):
        res = "/helpdesk"
        if self.reference_ticket:
            res += "/tickets/" + str(self.reference_ticket)
        if self.reference_comment:
            res += "#" + self.reference_comment
        return frappe.utils.get_url(res)

    def parse_html(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self.message, "html.parser")
        if soup.find("img"):
            img = soup.find("img")
            img["src"] = ("").join([frappe.utils.get_url(), img["src"]])
            return str(soup)
        return str(soup)

    def get_args(self):
        if self.notification_type == "Mention":
            return {
                "title": self.format_message(),
                "button_label": self.get_button_label(),
                "callback_url": self.get_url(),
                "comment": self.parse_html(),
            }

    def after_insert(self):
        self._send_push_notification()
        frappe.publish_realtime(
            "helpdesk:new-notification",
            {
                "type": self.notification_type,
                "ticket": self.reference_ticket or "",
                "user_from": self.user_from or "",
                "message": self.message or "",
            },
            user=self.user_to,
            after_commit=True,
        )

        if self.notification_type == "Mention":
            skip_email_workflow = frappe.db.get_single_value(
                "HD Settings", "skip_email_workflow"
            )

            if skip_email_workflow:
                return

            frappe.sendmail(
                recipients=self.user_to,
                subject="New notification",
                message=self.format_message(),
                template="notification",
                args=self.get_args(),
            )

    def _send_push_notification(self):
        from helpdesk.helpdesk.api.push_notifications import send_push_to_user

        title = "Helpdesk"
        body = self.message or ""

        if self.notification_type == "Mention":
            title = self.format_message() or "You were mentioned"
        elif self.notification_type == "Assignment":
            title = "Ticket assigned to you"
        elif self.notification_type == "WhatsApp":
            title = "New WhatsApp message"
        elif self.notification_type == "Reaction":
            title = "New reaction on your comment"

        if self.reference_ticket:
            url = f"/helpdesk/tickets/{self.reference_ticket}"
        elif self.reference_wa_jid and self.reference_wa_line:
            # WA Line conversations mostly have no ticket, so without this the
            # push would land the agent on the dashboard with no way to tell
            # which chat it was about. WhatsAppPage reads ?jid to open it.
            url = f"/helpdesk/whatsapp/{quote(self.reference_wa_line)}?jid={quote(self.reference_wa_jid)}"
        else:
            url = "/helpdesk"
        if self.reference_comment:
            url += f"#{self.reference_comment}"

        send_push_to_user(
            user=self.user_to,
            title=title,
            body=body,
            url=url,
            tag=f"helpdesk-{self.reference_ticket or self.reference_wa_jid or 'general'}",
        )
