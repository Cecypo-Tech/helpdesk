import LucideBookOpen from "~icons/lucide/book-open";
import LucideCheckSquare from "~icons/lucide/check-square";
import LucideHardDrive from "~icons/lucide/hard-drive";
import LucideContact2 from "~icons/lucide/contact-2";
import LucideBadgeCheck from "~icons/lucide/badge-check";
import LucideTicket from "~icons/lucide/ticket";
import { OrganizationsIcon } from "../icons";
import PhoneIcon from "../icons/PhoneIcon.vue";
import LucideHome from "~icons/lucide/home";
import WhatsAppIcon from "../icons/WhatsAppIcon.vue";
import { __ } from "@/translation";

export const agentPortalSidebarOptions = [
  {
    label: __("Home"),
    icon: LucideHome,
    to: "Home",
  },
  {
    label: __("Tickets"),
    icon: LucideTicket,
    to: "TicketsAgent",
  },
  {
    label: __("Tasks"),
    icon: LucideCheckSquare,
    to: "TasksAgent",
  },
  {
    label: __("Knowledge Base"),
    icon: LucideBookOpen,
    to: "AgentKnowledgeBase",
  },
  {
    label: "Customers",
    icon: OrganizationsIcon,
    to: "CustomerList",
  },
  {
    label: __("Contacts"),
    icon: LucideContact2,
    to: "ContactList",
  },
  {
    label: __("Verifications"),
    icon: LucideBadgeCheck,
    // Sidebar.vue filters this entry out while nothing is pending, and badges
    // it with the count when something is. A permanent entry that is empty most
    // days trains agents to skip past it, and the count is the entire point:
    // thirteen claims went unread because nothing said a number out loud.
    to: "PendingClaims",
  },
  {
    label: __("Call Logs"),
    icon: PhoneIcon,
    to: "CallLogs",
  },
  {
    label: __("WhatsApp Business"),
    icon: WhatsAppIcon,
    to: "WhatsAppBusinessChat",
  },
];

export const customerPortalSidebarOptions = [
  {
    label: __("Tickets"),
    icon: LucideTicket,
    to: "TicketsCustomer",
  },
  {
    label: __("Knowledge Base"),
    icon: LucideBookOpen,
    to: "CustomerKnowledgeBase",
  },
];

// Link out to the Imara Backup portal. Rendered near the top of both sidebars --
// deliberately OUTSIDE the `!isCustomerPortal` guard that hides Search, Dashboard
// and Notifications, so customers get it too. Presence of the imara_backup app is
// the only gate; /backup handles its own permissions.
//
// Not a member of the sidebar option lists, because those render below this in the
// "All Views" section. Both sidebars bind these fields onto a standalone
// SidebarLink, so this stays the single source of the label, icon and target.
//
// This used to leave the app entirely with `window.location.href = "/backup"`.
// It now routes to the BackupPortal page, which embeds that same Frappe web page
// in helpdesk's main body -- so the sidebar, and the rest of helpdesk, stay put.
export const backupPortalOption = {
  label: __("Imara Backup"),
  icon: LucideHardDrive,
  to: "BackupPortal",
};
