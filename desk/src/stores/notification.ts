import { useAuthStore } from "@/stores/auth";
import { ListResource, Notification as HDNotification } from "@/types";
import { isCustomerPortal } from "@/utils";
import { createListResource, createResource, toast } from "frappe-ui";
import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";
import { globalStore } from "./globalStore";

export const useNotificationStore = defineStore("notification", () => {
  const authStore = useAuthStore();
  const { $socket } = globalStore();

  const visible = ref(false);
  const resource: ListResource<HDNotification> = createListResource({
    doctype: "HD Notification",
    cache: "Notifications",
    fields: [
      "creation",
      "message",
      "name",
      "notification_type",
      "read",
      "reference_comment",
      "reference_ticket",
      "user_from",
      "user_to",
    ],
    orderBy: "modified desc",
  });
  const clear = createResource({
    url: "helpdesk.helpdesk.doctype.hd_notification.utils.clear",
    auto: false,
    onSuccess: () => resource.reload(),
  });

  const read = (ticket: string) => {
    createResource({
      url: "helpdesk.helpdesk.doctype.hd_notification.utils.clear",
      auto: true,
      params: {
        ticket,
      },
      onSuccess: () => resource.reload(),
    });
  };

  const readByName = (name: string) => {
    createResource({
      url: "helpdesk.helpdesk.doctype.hd_notification.utils.clear",
      auto: true,
      params: {
        notification: name,
      },
      onSuccess: () => resource.reload(),
    });
  };

  const data = computed(() => resource.data || []);
  const unread = computed(() => data.value.filter((d) => !d.read).length);

  function toggle() {
    visible.value = !visible.value;
  }

  const pushSupported =
    "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  const pushPermissionStatus = ref<NotificationPermission | "unsupported">(
    pushSupported ? Notification.permission : "unsupported"
  );

  watch(
    () => authStore.hasDeskAccess,
    (newVal) => {
      if (!newVal) return;
      resource.filters = {
        user_to: ["=", authStore.userId],
      };
      resource.reload();
      setupPushNotifications();
    },
    { immediate: true }
  );

  async function requestPushPermission() {
    if (!pushSupported) return;
    const result = await Notification.requestPermission();
    pushPermissionStatus.value = result;
    if (result === "granted") await setupPushNotifications();
  }

  async function setupPushNotifications() {
    if (!pushSupported) return;
    try {
      // Only proceed if the user has already granted permission.
      // Calling requestPermission() automatically (outside a user click) is
      // blocked by modern browsers and throws a console error.
      if (Notification.permission !== "granted") return;

      const reg = await navigator.serviceWorker.ready;

      // Fetch VAPID public key from backend
      const keyRes = await fetch(
        "/api/method/helpdesk.helpdesk.api.push_notifications.get_vapid_public_key",
        { headers: { "X-Frappe-CSRF-Token": getCsrfToken() } }
      );
      if (!keyRes.ok) return;
      const { message: publicKey } = await keyRes.json();
      if (!publicKey) return;

      // Subscribe (or reuse existing subscription)
      let sub = await reg.pushManager.getSubscription();
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(publicKey),
        });
      }

      // Save subscription to backend
      await fetch(
        "/api/method/helpdesk.helpdesk.api.push_notifications.save_push_subscription",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Frappe-CSRF-Token": getCsrfToken(),
          },
          body: JSON.stringify({ subscription: JSON.stringify(sub) }),
        }
      );
    } catch (_) {
      // Push not supported or user denied — fail silently
    }
  }

  function getCsrfToken(): string {
    return (
      document.cookie.match(/csrf_token=([^;]+)/)?.[1] ??
      (window as any).csrf_token ??
      ""
    );
  }

  function urlBase64ToUint8Array(base64String: string): Uint8Array {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding)
      .replace(/-/g, "+")
      .replace(/_/g, "/");
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }

  $socket.on("helpdesk:comment-reaction-update", () => {
    if (isCustomerPortal.value) return;
    resource.reload();
  });

  $socket.on("helpdesk:whatsapp-message", (data: { is_incoming: boolean }) => {
    if (isCustomerPortal.value) return;
    if (data.is_incoming) {
      resource.reload();
      try {
        const audio = new Audio("/assets/frappe/sounds/alert.mp3");
        audio.volume = 0.4;
        audio.play();
      } catch (_) {}
    }
  });

  $socket.on("helpdesk:baileys-notification", (data: { jid: string; message: string; sender: string }) => {
    if (isCustomerPortal.value) return;
    resource.reload();
    try {
      const audio = new Audio("/assets/frappe/sounds/alert.mp3");
      audio.volume = 0.4;
      audio.play();
    } catch (_) {}
  });

  $socket.on("helpdesk:new-notification", (data: { type: string; ticket: string; user_from: string; message: string }) => {
    if (isCustomerPortal.value) return;
    resource.reload();

    let message = "";
    let ticketUrl = "";

    if (data.type === "Assignment" && data.ticket) {
      message = `Ticket <strong>#${data.ticket}</strong> has been assigned to you`;
      ticketUrl = `/helpdesk/tickets/${data.ticket}`;
    } else if (data.type === "Mention" && data.ticket) {
      message = `You were mentioned in ticket <strong>#${data.ticket}</strong>`;
      ticketUrl = `/helpdesk/tickets/${data.ticket}`;
    } else if (data.type === "WhatsApp" && data.ticket) {
      message = data.message || `New WhatsApp message on ticket <strong>#${data.ticket}</strong>`;
      ticketUrl = `/helpdesk/tickets/${data.ticket}`;
    }

    if (message) {
      toast.create({
        message,
        type: "info",
        duration: 0,
        closable: true,
        ...(ticketUrl ? {
          action: {
            label: "View →",
            altText: "View ticket",
            onClick: () => { window.location.href = ticketUrl; },
          },
        } : {}),
      });
    }
  });

  return {
    clear,
    data,
    toggle,
    read,
    readByName,
    unread,
    visible,
    resource,
    pushSupported,
    pushPermissionStatus,
    requestPushPermission,
  };
});
