import os
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.service_worker import SERVICE_WORKER_ROUTE, ServiceWorkerPage, _built_sw_path


class TestServiceWorkerRoute(FrappeTestCase):
	"""The service worker must be served from the app's own path.

	A worker only controls pages at or below where it is served from. Served out
	of the asset directory it controlled nothing under /helpdesk, which silently
	broke both PWA install and push notifications — nothing errored, the install
	prompt just never appeared and `navigator.serviceWorker.ready` never
	resolved. A regression here would be equally silent, hence these.
	"""

	@contextmanager
	def _request(self, path):
		"""Stand in for a real request. frappe.local has no `request` outside one."""
		had = hasattr(frappe.local, "request")
		previous = getattr(frappe.local, "request", None)
		frappe.local.request = frappe._dict(path=path, environ={})
		try:
			yield
		finally:
			if had:
				frappe.local.request = previous
			else:
				del frappe.local.request

	def _page(self, request_path):
		# website_route_rules flattens every path under /helpdesk/ to the
		# "helpdesk" SPA endpoint before renderers run, so the resolver hands
		# renderers that rewritten value. Passing it here reproduces exactly
		# that: the renderer must key off the real request path instead.
		with self._request(request_path):
			page = ServiceWorkerPage("helpdesk")
			return page, page.can_render()

	def test_claims_its_route_despite_the_spa_rewrite(self):
		_page, can_render = self._page("/helpdesk/sw.js")
		self.assertTrue(
			can_render,
			"renderer must match on the request path, not the rewritten endpoint",
		)

	def test_does_not_hijack_app_routes(self):
		for path in ("/helpdesk", "/helpdesk/tickets", "/helpdesk/whatsapp", "/"):
			_page, can_render = self._page(path)
			self.assertFalse(can_render, f"must not claim {path}")

	def test_declines_when_the_app_is_not_built(self):
		"""Falling through to the SPA beats serving a broken worker.

		An empty or missing worker served with a 200 would unregister the real
		one in every browser that fetched it.
		"""
		with patch("helpdesk.service_worker.os.path.isfile", return_value=False):
			_page, can_render = self._page("/helpdesk/sw.js")
		self.assertFalse(can_render)

	def test_serves_the_built_worker(self):
		path = _built_sw_path()
		if not os.path.isfile(path):
			self.skipTest("app not built")
		with open(path, "rb") as f:
			body = f.read()
		# The push handlers are the whole point of registering a worker here.
		self.assertIn(b'addEventListener("push"', body)
		self.assertIn(b'addEventListener("notificationclick"', body)

	def test_route_constant_sits_under_the_app_path(self):
		# The scope a worker can claim is derived from where it is served, so
		# this constant moving out of /helpdesk/ would silently re-break things.
		self.assertTrue(SERVICE_WORKER_ROUTE.startswith("helpdesk/"))

	def test_manifest_start_url_is_inside_scope(self):
		"""An out-of-scope start_url is what disqualified the install prompt."""
		manifest_path = os.path.join(
			frappe.get_app_path("helpdesk"), "public", "desk", "manifest.webmanifest"
		)
		if not os.path.isfile(manifest_path):
			self.skipTest("app not built")
		with open(manifest_path) as f:
			manifest = frappe.parse_json(f.read())

		scope = manifest.get("scope") or ""
		start_url = manifest.get("start_url") or ""
		self.assertTrue(scope, "manifest must declare a scope")
		self.assertTrue(
			start_url.startswith(scope),
			f"start_url {start_url!r} is outside scope {scope!r}",
		)
		# Scope has to cover the canonical entry point, and "/helpdesk/"
		# 301-redirects to "/helpdesk" — so a trailing slash here would launch
		# the installed app straight out of its own scope.
		self.assertEqual(scope, "/helpdesk")

		# "any" and "maskable" are not interchangeable; sharing one asset is
		# what made the installed icon look wrong.
		by_purpose = {i.get("purpose"): i.get("src") for i in manifest.get("icons", [])}
		self.assertIn("any", by_purpose)
		self.assertIn("maskable", by_purpose)
		self.assertNotEqual(by_purpose["any"], by_purpose["maskable"])
		self.assertNotIn("maskable", by_purpose["any"])
