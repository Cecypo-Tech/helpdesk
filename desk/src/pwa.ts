// Service worker registration.
//
// vite-plugin-pwa's own registerSW.js is disabled (injectRegister: false),
// because it registers the worker at the path the build emits it to —
// /assets/helpdesk/desk/sw.js — and a worker can only control pages at or below
// its own path. The app is served from /helpdesk/, so that worker controlled
// nothing: the install prompt never qualified (start_url outside scope) and
// `navigator.serviceWorker.ready` never resolved, which left
// setupPushNotifications() awaiting it forever and meant no push subscription
// was ever created.
//
// helpdesk/service_worker.py serves the same built file at /helpdesk/sw.js,
// inside the app's own subtree, and sends the Service-Worker-Allowed header
// that lets it claim the scope below.

const SW_URL = "/helpdesk/sw.js";
// Not "/helpdesk/": the app's entry point is "/helpdesk", and scope is matched
// as a plain path prefix. Claiming this wider scope from a worker served out of
// /helpdesk/ is what the Service-Worker-Allowed header on that route permits.
const SW_SCOPE = "/helpdesk";

export function registerServiceWorker(): void {
  if (!("serviceWorker" in navigator)) return;

  window.addEventListener("load", async () => {
    try {
      const registration = await navigator.serviceWorker.register(SW_URL, {
        scope: SW_SCOPE,
      });

      // Previously-registered workers under the old asset scope are not
      // replaced by this one — different scope means a separate registration —
      // so they linger on every browser that ever loaded the app. Clear them
      // out, or they keep serving their stale precache alongside the new one.
      const stale = await navigator.serviceWorker.getRegistrations();
      for (const existing of stale) {
        if (existing !== registration && existing.scope.includes("/assets/")) {
          await existing.unregister();
        }
      }
    } catch (_) {
      // Unsupported, blocked by policy, or insecure origin — the app works
      // without a worker, it just isn't installable.
    }
  });
}
