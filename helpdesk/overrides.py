import frappe

_INT64_MAX = 2**63 - 1
_INT64_MIN = -(2**63)


def _sanitize_for_orjson(obj):
	"""Recursively convert integers that exceed orjson's 64-bit signed range to float."""
	if isinstance(obj, dict):
		return {k: _sanitize_for_orjson(v) for k, v in obj.items()}
	if isinstance(obj, list):
		return [_sanitize_for_orjson(v) for v in obj]
	if isinstance(obj, int) and not isinstance(obj, bool):
		if obj > _INT64_MAX or obj < _INT64_MIN:
			return float(obj)
	return obj


@frappe.whitelist(allow_guest=True)
def test_password_strength(new_password: str, key=None, old_password=None, user_data=None):
	"""Wrapper that sanitizes zxcvbn's large integer outputs before orjson serialization.

	zxcvbn returns crack-time estimates that can be astronomically large (e.g. 10^30
	guesses for a strong API key).  orjson rejects integers outside the signed 64-bit
	range, crashing the response pipeline.  We convert those to float, which is safe
	because the values are estimates anyway.
	"""
	from frappe.core.doctype.user.user import test_password_strength as _orig

	result = _orig(new_password, key=key, old_password=old_password, user_data=user_data)
	return _sanitize_for_orjson(result)
