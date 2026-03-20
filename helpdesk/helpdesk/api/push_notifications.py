import base64

import frappe


def _get_settings():
	return frappe.get_single("HD Push Settings")


def _ensure_vapid_keys():
	"""Auto-generate VAPID keys if not yet set. Returns the settings doc."""
	settings = _get_settings()
	if settings.vapid_public_key and settings.vapid_private_key:
		return settings

	from py_vapid import Vapid
	from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

	v = Vapid()
	v.generate_keys()

	private_pem = v.private_pem().decode("utf-8")
	public_bytes = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
	public_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("utf-8")

	settings.vapid_public_key = public_b64
	settings.vapid_private_key = private_pem
	settings.save(ignore_permissions=True)

	return settings


@frappe.whitelist(allow_guest=False)
def get_vapid_public_key():
	settings = _ensure_vapid_keys()
	return settings.vapid_public_key


@frappe.whitelist(allow_guest=False)
def save_push_subscription(subscription):
	data = frappe.parse_json(subscription)
	endpoint = data.get("endpoint", "")
	keys = data.get("keys", {})

	if not endpoint:
		frappe.throw("Missing endpoint")

	existing = frappe.db.get_value("HD Push Subscription", {"endpoint": ("like", endpoint[:200])}, "name")
	if existing:
		frappe.db.set_value("HD Push Subscription", existing, "user", frappe.session.user)
		return

	doc = frappe.get_doc(
		{
			"doctype": "HD Push Subscription",
			"user": frappe.session.user,
			"endpoint": endpoint,
			"keys_p256dh": keys.get("p256dh", ""),
			"keys_auth": keys.get("auth", ""),
		}
	)
	doc.insert(ignore_permissions=True)


@frappe.whitelist(allow_guest=False)
def remove_push_subscription(endpoint):
	name = frappe.db.get_value("HD Push Subscription", {"endpoint": ("like", endpoint[:200])}, "name")
	if name:
		frappe.delete_doc("HD Push Subscription", name, ignore_permissions=True)


def send_push_to_user(user, title, body, url="/helpdesk", tag="helpdesk"):
	"""Send a Web Push notification to all registered subscriptions for a user.
	Silently skips if VAPID keys are not configured.
	"""
	try:
		settings = _get_settings()
		if not settings.vapid_public_key or not settings.vapid_private_key:
			return

		vapid_email = settings.vapid_email or "admin@example.com"
		if not vapid_email.startswith("mailto:"):
			vapid_email = f"mailto:{vapid_email}"

		subscriptions = frappe.get_all(
			"HD Push Subscription",
			filters={"user": user},
			fields=["name", "endpoint", "keys_p256dh", "keys_auth"],
		)

		if not subscriptions:
			return

		from pywebpush import WebPushException, webpush

		payload = frappe.as_json({"title": title, "body": body, "data": {"url": url}, "tag": tag})

		for sub in subscriptions:
			try:
				webpush(
					subscription_info={
						"endpoint": sub.endpoint,
						"keys": {"p256dh": sub.keys_p256dh, "auth": sub.keys_auth},
					},
					data=payload,
					vapid_private_key=settings.get_password("vapid_private_key"),
					vapid_claims={"sub": vapid_email},
				)
			except WebPushException as e:
				if e.response is not None and e.response.status_code in (404, 410):
					# Expired or invalid subscription — clean up
					frappe.delete_doc("HD Push Subscription", sub.name, ignore_permissions=True)
				else:
					frappe.log_error(str(e), "Web Push Error")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Web Push Error")
