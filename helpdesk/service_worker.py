"""Serve the PWA service worker from the app's own path instead of the asset path.

A service worker can only control pages at or below the path it is served from.
The build emits `sw.js` into the app's asset directory, so vite-plugin-pwa
registered it at `/assets/helpdesk/desk/sw.js` — scope `/assets/helpdesk/desk/`.
The app itself is served at `/helpdesk/`, an entirely different subtree, so the
worker never controlled a single page of it. Two things followed:

  * `start_url` sat outside `scope`, which is invalid per the web app manifest
    spec, so the install prompt never qualified.
  * `navigator.serviceWorker.ready` never resolves on a page no worker controls.
    setupPushNotifications() awaits it before subscribing, so it stopped there
    forever — silently, inside a catch — and no push subscription was ever
    created. Push notifications had never worked for either WhatsApp
    integration, and could not have.

Serving the same file at `/helpdesk/sw.js` puts it in the app's own subtree,
where it can claim scope `/helpdesk` — the canonical entry point, since
`/helpdesk/` 301-redirects to it. That last character costs a
`Service-Worker-Allowed` header, which this renderer can set precisely because
the route is now served by the app; `/assets` is served by nginx, where adding
headers is not something the app can do on a managed host.

Frappe's own StaticPage renderer cannot do this: `js` is in its
UNSUPPORTED_STATIC_PAGE_TYPES, so a file dropped in `www/` would 404. Hence a
custom page renderer, registered via the `page_renderer` hook.
"""

import mimetypes
import os

import frappe
from werkzeug.wrappers import Response
from werkzeug.wsgi import wrap_file

# Path the worker is served from, and the scope that buys us. Keep in sync with
# the registration in desk/src/pwa.ts and with `scope` in the vite manifest.
SERVICE_WORKER_ROUTE = "helpdesk/sw.js"

# Emitted by the vite build into the app's public directory.
_BUILT_SW_RELATIVE_PATH = os.path.join("public", "desk", "sw.js")


def _built_sw_path() -> str:
	return os.path.join(frappe.get_app_path("helpdesk"), _BUILT_SW_RELATIVE_PATH)


class ServiceWorkerPage:
	"""Page renderer for `/helpdesk/sw.js`."""

	def __init__(self, path=None, http_status_code=None):
		# Deliberately ignores the `path` the resolver passes in. That value is
		# the *rewritten* endpoint: website_route_rules maps
		# `/helpdesk/<path:app_path>` onto the `helpdesk` SPA page, and
		# resolve_path() applies it before any renderer is consulted — so every
		# path under /helpdesk/ arrives here already flattened to "helpdesk".
		# Only the original request path can distinguish the worker.
		request = getattr(frappe.local, "request", None)
		self.path = (request.path if request else "").strip("/ ")
		self.http_status_code = http_status_code or 200

	def can_render(self) -> bool:
		# Never claim the route when the app hasn't been built — falling through
		# to the SPA is far easier to diagnose than serving an empty worker,
		# which would silently unregister the real one.
		return self.path == SERVICE_WORKER_ROUTE and os.path.isfile(_built_sw_path())

	def render(self) -> Response:
		path = _built_sw_path()
		# Left open deliberately; the WSGI middleware closes it.
		f = open(path, "rb")
		response = Response(
			wrap_file(frappe.local.request.environ, f), direct_passthrough=True
		)
		response.mimetype = mimetypes.guess_type(path)[0] or "text/javascript"
		# Required, not belt-and-braces. Serving from /helpdesk/sw.js grants a
		# default max scope of "/helpdesk/", but the app's canonical entry point
		# is "/helpdesk" without the slash ("/helpdesk/" 301-redirects to it),
		# and that is not a prefix match for "/helpdesk/". This widens the
		# permitted scope by exactly one character so the registration can claim
		# "/helpdesk" and cover the entry point.
		response.headers["Service-Worker-Allowed"] = "/helpdesk"
		# A stale worker is how a PWA gets stuck on an old build — the browser
		# revalidates the worker script on its own schedule, and caching it
		# defeats registerType: "autoUpdate".
		response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
		return response
