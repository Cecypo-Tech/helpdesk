import {
  cleanupOutdatedCaches,
  createHandlerBoundToURL,
  precacheAndRoute,
} from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";

self.skipWaiting();

self.addEventListener("activate", (event) => {
  event.waitUntil(clients.claim());
});

precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();
registerRoute(new NavigationRoute(createHandlerBoundToURL("index.html")));

// ── Web Push ──────────────────────────────────────────────────────────────────

self.addEventListener("push", (event) => {
  if (!event.data) return;

  let payload;
  try {
    payload = event.data.json();
  } catch {
    payload = { title: "Helpdesk", body: event.data.text() };
  }

  const title = payload.title || "Helpdesk";
  const options = {
    body: payload.body || "",
    icon: "/assets/helpdesk/desk/manifest/manifest-icon-192.maskable.png",
    badge: "/assets/helpdesk/desk/manifest/manifest-icon-192.maskable.png",
    data: payload.data || {},
    tag: payload.tag || "helpdesk",
  };

  event.waitUntil(
    // Don't show notification if the app is already visible in a tab
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        for (const client of clientList) {
          if (client.visibilityState === "visible") return;
        }
        return self.registration.showNotification(title, options);
      })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const url = event.notification.data?.url || "/helpdesk";

  event.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        for (const client of clientList) {
          if (client.url.includes("/helpdesk") && "focus" in client) {
            client.navigate(url);
            return client.focus();
          }
        }
        return clients.openWindow(url);
      })
  );
});
