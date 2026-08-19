"""Turn what an unknown contact typed into a claim, and a claim into candidates.

Pure logic plus one read query. Nothing here decides anything — matching a PIN
is evidence for an agent, never authorisation. See the spec for why.
"""

import re

import frappe

# Permissive on purpose. Of the 2,803 tax_ids mirrored from production ERPNext,
# nine are malformed: five end in a digit where the check letter belongs, three
# are a digit short, one is literally "P". A strict ^[A-Z]\d{9}[A-Z]$ would tell
# those customers their own PIN is invalid. Seven digits is the floor that still
# excludes company names like "A1 Supermarket".
PIN_TOKEN = re.compile(r"\b[A-Za-z]\d{7,10}[A-Za-z]?\b")


def normalise_pin(value: str | None) -> str:
	"""Uppercase, and drop anything that is not a letter or digit."""
	if not value:
		return ""
	return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def extract_claim(text: str | None) -> dict:
	"""Pull a PIN-shaped token and whatever else was typed out of a message."""
	if not text:
		return {"tax_id": None, "company": None}

	match = PIN_TOKEN.search(text)
	tax_id = match.group(0) if match else None

	remainder = text[: match.start()] + " " + text[match.end() :] if match else text
	company = re.sub(r"[^\w\s&.'-]", " ", remainder)
	company = re.sub(r"\s+", " ", company).strip()[:140] or None

	return {"tax_id": tax_id, "company": company}


def match_claim(tax_id: str | None) -> list[str]:
	"""HD Customers whose tax_id equals the claim, compared normalised.

	An empty claim matches nothing — never every customer with a blank tax_id.
	"""
	claimed = normalise_pin(tax_id)
	if not claimed:
		return []

	try:
		rows = frappe.get_all(
			"HD Customer", filters={"tax_id": ["is", "set"]}, fields=["name", "tax_id"]
		)
	except Exception:
		# tax_id is a Custom Field; the column is absent on an unmigrated site.
		return []

	return [r["name"] for r in rows if normalise_pin(r.get("tax_id")) == claimed]
