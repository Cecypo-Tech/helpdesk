# Unknown-Contact Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an unknown WABA sender identify themselves by company name and KRA PIN, and let one agent click turn that claim into a permanent Contact → HD Customer link.

**Architecture:** A deterministic (non-LLM) state machine on `Contact`, driven from the existing WhatsApp ingestion path. Pure claim-parsing logic lives in `helpdesk/verification.py`; the WABA side effects live in `helpdesk/integrations/wa_verification.py`; agent actions are three `@agent_only` endpoints. Approval writes the Dynamic Link that `helpdesk/utils.py:91` already queries, so every downstream feature (product, entitlement, standing, bot scoping) resolves with no further change.

**Tech Stack:** Frappe v16 (Python 3.14), MariaDB, Vue 3 Composition API + frappe-ui, `frappe_whatsapp` for the WABA transport.

**Spec:** `docs/superpowers/specs/2026-08-19-unknown-contact-verification-design.md`

## Global Constraints

- **WABA only.** WA Line (Evolution API), email and portal are out of scope. Do not touch `WA Message` or `bot.handle_wa_message`.
- **Never auto-link.** A single exact PIN match still requires an agent to approve. This is the load-bearing rule; Task 4 has a test named for it.
- **Everything defaults off.** `verification_enabled` defaults to `0`. Deploying must change nothing observable until somebody ticks it.
- **Fail-open.** Nothing here may break ticket creation or an inbound message. Every side effect is wrapped in `try/except` that logs and continues, matching `wa_ingest.py:60-67`.
- **Custom Fields go in the single consolidated fixture entry** in `hooks.py` (the `Custom Field` block with the `fieldname in [...]` filter around line 208). Frappe writes all `Custom Field` fixture specs to one `custom_field.json`, so a second entry silently clobbers the first — this fork lost `baileys_jid` four times that way.
- **Every Custom Field read is wrapped in `try/except`.** On a site that has not migrated, the column does not exist and Frappe raises `OperationalError` — same hazard as `baileys_jid`.
- **`@agent_only` on every whitelisted endpoint** that reads or writes customer linkage. `@frappe.whitelist()` alone bypasses permissions.
- **Frontend:** semantic Tailwind tokens only (`text-ink-gray-7`, `bg-surface-white`); never raw colours. Note `text-surface-*` compiles to nothing — `surface` is registered for backgrounds only.
- **Tests:** `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module <module>`
- **Commits:** no `Co-Authored-By` trailer.

## File Structure

| File | Responsibility |
|---|---|
| `helpdesk/verification.py` | **new** — pure logic: normalise a PIN, extract a claim from message text, match a claim to HD Customers. No I/O beyond one read query. |
| `helpdesk/integrations/wa_verification.py` | **new** — the WABA state machine: decide whether to ask, send the prompt, record a claim. All side effects. |
| `helpdesk/api/verification.py` | **new** — three `@agent_only` endpoints for the agent UI. |
| `helpdesk/patches/add_contact_verification_fields.py` | **new** — creates the four Contact Custom Fields. |
| `helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/whatsapp_helpdesk_settings.json` | modify — three settings fields. |
| `helpdesk/hooks.py` | modify — add four fieldnames to the consolidated fixture filter; register the patch. |
| `helpdesk/integrations/wa_ingest.py` | modify — one call after the bot dispatch. |
| `desk/src/components/ticket-agent/TicketContactTab.vue` | modify — render the claim and Approve/Reject in the existing panel. |
| `helpdesk/tests/test_contact_verification.py` | **new** — Tasks 2-4. |

---

### Task 1: Schema — Contact fields and settings

**Files:**
- Create: `helpdesk/patches/add_contact_verification_fields.py`
- Modify: `helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/whatsapp_helpdesk_settings.json`
- Modify: `helpdesk/hooks.py` (fixture filter list, and `patches.txt`)
- Modify: `helpdesk/patches.txt`

**Interfaces:**
- Consumes: nothing
- Produces: `Contact.hd_verification_status` (Select: `Unverified`/`Asked`/`Claimed`/`Verified`/`Rejected`, default `Unverified`), `Contact.hd_claimed_company` (Data), `Contact.hd_claimed_tax_id` (Data), `Contact.hd_verification_asked_on` (Datetime); `WhatsApp Helpdesk Settings.verification_enabled` (Check, default 0), `.verification_prompt` (Small Text), `.verification_reask_days` (Int, default 7)

- [ ] **Step 1: Write the patch**

```python
# helpdesk/patches/add_contact_verification_fields.py
"""Add the unknown-contact verification fields to Contact.

Custom Fields rather than edits to Frappe's Contact doctype, so a
`git merge upstream/develop` cannot drop them.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELDS = {
	"Contact": [
		{
			"fieldname": "hd_verification_status",
			"label": "Helpdesk Verification Status",
			"fieldtype": "Select",
			"options": "Unverified\nAsked\nClaimed\nVerified\nRejected",
			"default": "Unverified",
			"insert_after": "email_id",
			"read_only": 1,
		},
		{
			"fieldname": "hd_claimed_company",
			"label": "Claimed Company",
			"fieldtype": "Data",
			"insert_after": "hd_verification_status",
			"read_only": 1,
			"description": "What the contact typed. A claim, never trusted.",
		},
		{
			"fieldname": "hd_claimed_tax_id",
			"label": "Claimed KRA PIN",
			"fieldtype": "Data",
			"insert_after": "hd_claimed_company",
			"read_only": 1,
			"description": "What the contact typed. A claim, never trusted.",
		},
		{
			"fieldname": "hd_verification_asked_on",
			"label": "Verification Asked On",
			"fieldtype": "Datetime",
			"insert_after": "hd_claimed_tax_id",
			"read_only": 1,
		},
	]
}


def execute():
	create_custom_fields(FIELDS, ignore_validate=True)
```

- [ ] **Step 2: Register the patch and extend the fixture filter**

Append to `helpdesk/patches.txt` under `post_model_sync`:

```
helpdesk.patches.add_contact_verification_fields
```

In `helpdesk/hooks.py`, add these four strings to the existing `fieldname in [...]` list inside the single `Custom Field` fixture entry (around line 210). Do **not** add a second `Custom Field` entry:

```python
                    "hd_verification_status",
                    "hd_claimed_company",
                    "hd_claimed_tax_id",
                    "hd_verification_asked_on",
```

- [ ] **Step 3: Add the settings fields**

In `whatsapp_helpdesk_settings.json`, add to `field_order` (after `unknown_contact_action`) and to `fields`:

```json
  {
   "default": "0",
   "fieldname": "verification_enabled",
   "fieldtype": "Check",
   "label": "Ask Unknown Contacts to Identify Themselves",
   "description": "Off by default. When on, an unrecognised WhatsApp number is asked once for its company name and KRA PIN. A match never links anyone automatically — an agent approves."
  },
  {
   "default": "Hi! So we can pull up your account, could you reply with your company name and KRA PIN? Thanks.",
   "depends_on": "eval:doc.verification_enabled",
   "fieldname": "verification_prompt",
   "fieldtype": "Small Text",
   "label": "Verification Prompt"
  },
  {
   "default": "7",
   "depends_on": "eval:doc.verification_enabled",
   "fieldname": "verification_reask_days",
   "fieldtype": "Int",
   "label": "Days Before Asking Again"
  }
```

- [ ] **Step 4: Migrate and verify the schema exists**

Run:
```bash
cd /home/kushal/frappe-bench && bench --site dev.localhost migrate
bench --site dev.localhost console <<'EOF'
import frappe
def check():
    for f in ("hd_verification_status", "hd_claimed_company", "hd_claimed_tax_id", "hd_verification_asked_on"):
        print(f, frappe.db.has_column("Contact", f))
    s = frappe.get_single("WhatsApp Helpdesk Settings")
    print("enabled:", s.verification_enabled, "| reask:", s.verification_reask_days)
check()
exit()
EOF
```
Expected: four `True`, `enabled: 0`, `reask: 7`.

- [ ] **Step 5: Export fixtures and confirm nothing was dropped**

```bash
cd /home/kushal/frappe-bench && bench --site dev.localhost export-fixtures --app helpdesk
cd apps/helpdesk && python3 -c "
import json; d=json.load(open('helpdesk/fixtures/custom_field.json'))
names=[x['name'] for x in d]; print(len(names)); assert 'HD Ticket-baileys_jid' in names; print('baileys_jid survived')"
```
Expected: 22 entries, and the assertion passes. If `baileys_jid` is missing, a second `Custom Field` fixture entry was added — remove it.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(verification): schema for unknown-contact verification

Four Custom Fields on Contact and three settings on WhatsApp Helpdesk
Settings. verification_enabled defaults to 0, so this changes nothing
until it is switched on."
```

---

### Task 2: Claim parsing and matching

**Files:**
- Create: `helpdesk/verification.py`
- Test: `helpdesk/tests/test_contact_verification.py`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime (pure logic + one read of `HD Customer.tax_id`)
- Produces:
  - `normalise_pin(value: str | None) -> str`
  - `extract_claim(text: str | None) -> dict` returning `{"tax_id": str | None, "company": str | None}`
  - `match_claim(tax_id: str | None) -> list[str]` returning HD Customer names

- [ ] **Step 1: Write the failing tests**

```python
# helpdesk/tests/test_contact_verification.py
"""Unknown-contact verification: parsing, matching, state machine, approval.

The rule the whole feature hangs off: a KRA PIN is a claim, not a credential.
It is printed on every invoice and ETR receipt, and 30 customers in the source
ERPNext share one with a namesake. So a match never links anyone automatically.
"""

import unittest

import frappe

from helpdesk import verification

PREFIX = "_test-verif-"


class TestNormalisePin(unittest.TestCase):
	def test_uppercases_and_strips_punctuation(self):
		self.assertEqual(verification.normalise_pin(" p051234567 x "), "P051234567X")

	def test_none_and_empty_are_empty_string(self):
		self.assertEqual(verification.normalise_pin(None), "")
		self.assertEqual(verification.normalise_pin("   "), "")


class TestExtractClaim(unittest.TestCase):
	def test_canonical_pin(self):
		self.assertEqual(verification.extract_claim("P051234567X")["tax_id"], "P051234567X")

	def test_pin_embedded_in_a_sentence(self):
		claim = verification.extract_claim("Hi, we are Blue Lake Ltd, PIN P051234567X thanks")
		self.assertEqual(claim["tax_id"], "P051234567X")
		self.assertIn("Blue Lake", claim["company"])

	def test_accepts_the_malformed_shapes_that_exist_in_real_data(self):
		"""Nine of 2,803 mirrored tax_ids are malformed. A strict PIN regex
		would tell those customers their own PIN is invalid."""
		for raw in ("P05122111X", "P0511478994", "A0011327804", "P05110212C"):
			self.assertEqual(verification.extract_claim(raw)["tax_id"], raw, raw)

	def test_a_phone_number_is_not_a_pin(self):
		self.assertIsNone(verification.extract_claim("254712345678")["tax_id"])

	def test_short_alphanumerics_are_not_pins(self):
		"""'A1 Supermarket' must not read as a PIN."""
		self.assertIsNone(verification.extract_claim("A1 Supermarket")["tax_id"])

	def test_empty_input_is_safe(self):
		self.assertEqual(verification.extract_claim(None), {"tax_id": None, "company": None})


class TestMatchClaim(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.cleanup()
		frappe.get_doc({
			"doctype": "HD Customer",
			"customer_name": PREFIX + "match-co",
			"tax_id": "P051234567X",
		}).insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(self.cleanup)

	def cleanup(self):
		for name in frappe.get_all(
			"HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
		):
			frappe.delete_doc("HD Customer", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_exact_match(self):
		self.assertEqual(verification.match_claim("P051234567X"), [PREFIX + "match-co"])

	def test_matching_ignores_case_and_punctuation(self):
		self.assertEqual(verification.match_claim(" p051234567-x "), [PREFIX + "match-co"])

	def test_no_match_returns_empty_not_an_error(self):
		self.assertEqual(verification.match_claim("P999999999Z"), [])

	def test_empty_claim_matches_nothing(self):
		"""Must not return every customer whose tax_id is blank."""
		self.assertEqual(verification.match_claim(""), [])
		self.assertEqual(verification.match_claim(None), [])
```

- [ ] **Step 2: Run and verify they fail**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module helpdesk.tests.test_contact_verification`
Expected: FAIL — `ModuleNotFoundError: No module named 'helpdesk.verification'`

- [ ] **Step 3: Implement**

```python
# helpdesk/verification.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module helpdesk.tests.test_contact_verification`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(verification): parse and match KRA PIN claims

Matching normalises rather than validates. Nine of 2,803 mirrored tax_ids
are malformed, so a strict PIN regex would tell those customers their own
PIN is invalid. An empty claim matches nothing."
```

---

### Task 3: The WABA state machine

**Files:**
- Create: `helpdesk/integrations/wa_verification.py`
- Modify: `helpdesk/integrations/wa_ingest.py` (after the bot dispatch block, ~line 67)
- Test: `helpdesk/tests/test_contact_verification.py` (append)

**Interfaces:**
- Consumes: `verification.extract_claim`, `verification.normalise_pin` (Task 2); the Contact fields from Task 1
- Produces: `handle_incoming(doc) -> None` — called with a linked `WhatsApp Message`

- [ ] **Step 1: Write the failing tests**

Append to `helpdesk/tests/test_contact_verification.py`:

```python
from unittest.mock import patch

from helpdesk.integrations import wa_verification


def _settings(enabled=1, reask=7):
	return frappe._dict(
		verification_enabled=enabled,
		verification_prompt="What is your company name and KRA PIN?",
		verification_reask_days=reask,
	)


class _StateBase(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.cleanup()
		self.contact = frappe.get_doc({
			"doctype": "Contact",
			"first_name": PREFIX + "caller",
			"phone_nos": [{"phone": "254700111222", "is_primary_mobile_no": 1}],
		}).insert(ignore_permissions=True).name
		self.ticket = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": PREFIX + "ticket",
			"contact": self.contact,
		}).insert(ignore_permissions=True).name
		frappe.db.commit()

		self.sent = []
		p = patch.object(
			wa_verification, "send_prompt",
			side_effect=lambda ticket, text: self.sent.append((ticket, text)),
		)
		p.start()
		self.addCleanup(p.stop)
		p2 = patch.object(wa_verification, "settings", return_value=_settings())
		p2.start()
		self.addCleanup(p2.stop)
		self.addCleanup(self.cleanup)

	def cleanup(self):
		for t in frappe.get_all(
			"HD Ticket", filters={"subject": ["like", PREFIX + "%"]}, pluck="name"
		):
			frappe.delete_doc("HD Ticket", t, force=True, ignore_permissions=True,
			                  delete_permanently=True)
		for c in frappe.get_all(
			"Contact", filters={"first_name": ["like", PREFIX + "%"]}, pluck="name"
		):
			frappe.delete_doc("Contact", c, force=True, ignore_permissions=True,
			                  delete_permanently=True)
		for c in frappe.get_all(
			"HD Customer", filters={"name": ["like", PREFIX + "%"]}, pluck="name"
		):
			frappe.delete_doc("HD Customer", c, force=True, ignore_permissions=True)
		frappe.db.commit()

	def msg(self, text):
		return frappe._dict(
			name=PREFIX + "msg", type="Incoming", message=text,
			reference_doctype="HD Ticket", reference_name=self.ticket,
		)

	def status(self):
		return frappe.db.get_value("Contact", self.contact, "hd_verification_status")


class TestAsking(_StateBase):
	def test_an_unverified_contact_is_asked_once(self):
		wa_verification.handle_incoming(self.msg("my printer is broken"))
		self.assertEqual(len(self.sent), 1)
		self.assertEqual(self.status(), "Asked")

	def test_a_second_message_inside_the_window_does_not_reask(self):
		wa_verification.handle_incoming(self.msg("my printer is broken"))
		wa_verification.handle_incoming(self.msg("are you there?"))
		self.assertEqual(len(self.sent), 1, "must not nag on every message")

	def test_it_asks_again_after_the_reask_window(self):
		wa_verification.handle_incoming(self.msg("hello"))
		frappe.db.set_value(
			"Contact", self.contact, "hd_verification_asked_on",
			frappe.utils.add_days(frappe.utils.now_datetime(), -8),
		)
		wa_verification.handle_incoming(self.msg("hello again"))
		self.assertEqual(len(self.sent), 2)

	def test_a_contact_already_linked_to_a_customer_is_never_asked(self):
		customer = frappe.get_doc({
			"doctype": "HD Customer", "customer_name": PREFIX + "known",
		}).insert(ignore_permissions=True).name
		c = frappe.get_doc("Contact", self.contact)
		c.append("links", {"link_doctype": "HD Customer", "link_name": customer})
		c.save(ignore_permissions=True)
		frappe.db.commit()

		wa_verification.handle_incoming(self.msg("hello"))
		self.assertEqual(self.sent, [])

	def test_disabled_setting_does_nothing_at_all(self):
		with patch.object(wa_verification, "settings", return_value=_settings(enabled=0)):
			wa_verification.handle_incoming(self.msg("hello"))
		self.assertEqual(self.sent, [])
		self.assertIn(self.status(), (None, "Unverified"))


class TestRecordingTheClaim(_StateBase):
	def test_a_pin_in_the_reply_is_stored_and_status_moves_to_claimed(self):
		wa_verification.handle_incoming(self.msg("hello"))
		wa_verification.handle_incoming(self.msg("Blue Lake Ltd P051234567X"))

		row = frappe.db.get_value(
			"Contact", self.contact,
			["hd_verification_status", "hd_claimed_tax_id", "hd_claimed_company"],
			as_dict=True,
		)
		self.assertEqual(row.hd_verification_status, "Claimed")
		self.assertEqual(row.hd_claimed_tax_id, "P051234567X")
		self.assertIn("Blue Lake", row.hd_claimed_company)

	def test_a_reply_with_no_pin_leaves_the_state_alone(self):
		wa_verification.handle_incoming(self.msg("hello"))
		wa_verification.handle_incoming(self.msg("sorry what do you mean?"))
		self.assertEqual(self.status(), "Asked")
		self.assertEqual(len(self.sent), 1)

	def test_a_verified_contact_is_never_relinked_by_a_later_pin(self):
		frappe.db.set_value("Contact", self.contact, "hd_verification_status", "Verified")
		wa_verification.handle_incoming(self.msg("actually our PIN is P099999999Z"))
		self.assertEqual(
			frappe.db.get_value("Contact", self.contact, "hd_claimed_tax_id"), None
		)

	def test_a_rejected_contact_is_never_asked_again(self):
		frappe.db.set_value("Contact", self.contact, "hd_verification_status", "Rejected")
		wa_verification.handle_incoming(self.msg("hello"))
		self.assertEqual(self.sent, [])


class TestFailOpen(_StateBase):
	def test_a_failing_send_does_not_raise(self):
		with patch.object(wa_verification, "send_prompt", side_effect=Exception("boom")):
			wa_verification.handle_incoming(self.msg("hello"))
		self.assertTrue(frappe.db.exists("HD Ticket", self.ticket))
```

- [ ] **Step 2: Run and verify they fail**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module helpdesk.tests.test_contact_verification`
Expected: FAIL — `No module named 'helpdesk.integrations.wa_verification'`

- [ ] **Step 3: Implement the state machine**

```python
# helpdesk/integrations/wa_verification.py
"""Ask an unrecognised WhatsApp number who it is, and record what it answers.

Deterministic on purpose — no LLM. The bot answers the customer's question;
this asks one housekeeping question alongside it, at most once a week, and
records the reply as a claim for an agent to approve.

Nothing here links anybody. See the spec: a KRA PIN is printed on every invoice
and ETR receipt, and a WhatsApp number can be a shared office handset.
"""

import frappe

from helpdesk import verification
from helpdesk.utils import get_customer

DOCTYPE = "WhatsApp Helpdesk Settings"
STATUS_FIELD = "hd_verification_status"

UNVERIFIED = "Unverified"
ASKED = "Asked"
CLAIMED = "Claimed"
VERIFIED = "Verified"
REJECTED = "Rejected"

# An agent's decision, either way, is final as far as automation is concerned.
TERMINAL = (VERIFIED, REJECTED)


def settings():
	return frappe.get_cached_doc(DOCTYPE)


def send_prompt(ticket: str, text: str) -> None:
	"""Free-form WABA reply. Safe without a template: the customer's inbound
	message just opened the 24-hour window, so it is open by construction."""
	from helpdesk.integrations.wa import _send_fw_reply

	_send_fw_reply(ticket, text)


def contact_state(contact: str) -> dict:
	"""Verification fields for a contact, or empty on an unmigrated site."""
	try:
		return frappe.db.get_value(
			"Contact", contact,
			[STATUS_FIELD, "hd_claimed_tax_id", "hd_claimed_company",
			 "hd_verification_asked_on"],
			as_dict=True,
		) or {}
	except Exception:
		# Custom Fields absent — same hazard as baileys_jid on an unmigrated site.
		return {}


def handle_incoming(doc) -> None:
	"""Advance the verification state for one inbound WABA message.

	Never raises. Ticket creation has already happened and committed by the time
	this runs; an automated question failing must not undo it.
	"""
	try:
		_handle(doc)
	except Exception:
		frappe.log_error(
			title="WhatsApp verification failed",
			message=f"Message {doc.get('name')} on ticket {doc.get('reference_name')}",
		)


def _handle(doc) -> None:
	if doc.get("type") != "Incoming":
		return
	if doc.get("reference_doctype") != "HD Ticket" or not doc.get("reference_name"):
		return

	s = settings()
	if not s.get("verification_enabled"):
		return

	ticket = doc.get("reference_name")
	contact = frappe.db.get_value("HD Ticket", ticket, "contact")
	if not contact:
		return

	if get_customer(contact):
		# Already linked to a customer; there is nothing to ask.
		return

	state = contact_state(contact)
	if not state:
		return

	status = state.get(STATUS_FIELD) or UNVERIFIED
	if status in TERMINAL:
		return

	if status in (ASKED, CLAIMED):
		claim = verification.extract_claim(doc.get("message"))
		if claim["tax_id"]:
			frappe.db.set_value("Contact", contact, {
				"hd_claimed_tax_id": claim["tax_id"],
				"hd_claimed_company": claim["company"],
				STATUS_FIELD: CLAIMED,
			})
			frappe.db.commit()
			return

	if status == CLAIMED:
		# Waiting on an agent. Do not keep asking.
		return

	if status == ASKED and not _reask_due(state, s):
		return

	send_prompt(ticket, s.get("verification_prompt"))
	frappe.db.set_value("Contact", contact, {
		STATUS_FIELD: ASKED,
		"hd_verification_asked_on": frappe.utils.now_datetime(),
	})
	frappe.db.commit()


def _reask_due(state: dict, s) -> bool:
	asked_on = state.get("hd_verification_asked_on")
	if not asked_on:
		return True
	days = int(s.get("verification_reask_days") or 7)
	return frappe.utils.date_diff(frappe.utils.now_datetime(), asked_on) >= days
```

- [ ] **Step 4: Wire it into ingestion**

In `helpdesk/integrations/wa_ingest.py`, import `wa_verification` alongside `bot` and `wa`, then append after the existing bot `try/except` block (which ends around line 67):

```python
	# Same discipline as the bot dispatch above: the ticket is already committed
	# and an automated question must not be able to undo it. handle_incoming
	# swallows its own errors too; this is belt and braces.
	try:
		wa_verification.handle_incoming(doc)
	except Exception:
		frappe.log_error(
			title="WhatsApp verification dispatch failed",
			message=f"Message {doc.name} is linked to ticket {doc.reference_name}.",
		)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module helpdesk.tests.test_contact_verification`
Expected: PASS, 22 tests

- [ ] **Step 6: Confirm ingestion still works with the feature off**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module helpdesk.integrations.tests.test_no_erpnext`
Expected: PASS, 8 tests

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(verification): ask unknown WABA numbers who they are

Deterministic state machine on Contact, driven from the existing ingestion
path. Asks at most once per reask window, records a PIN reply as a claim,
and never touches a contact an agent has already verified or rejected.
Fail-open throughout: the ticket is committed before this runs."
```

---

### Task 4: Agent approval API

**Files:**
- Create: `helpdesk/api/verification.py`
- Test: `helpdesk/tests/test_contact_verification.py` (append)

**Interfaces:**
- Consumes: `verification.match_claim` (Task 2); the Contact fields (Task 1); `wa_verification.VERIFIED` / `REJECTED` constants (Task 3)
- Produces:
  - `get_contact_claim(ticket: str | int) -> dict` → `{"contact", "status", "claimed_company", "claimed_tax_id", "matches": [{"name", "customer_name"}]}`
  - `approve_contact_link(contact: str, customer: str) -> dict` → `{"ok": True, "customer": str}`
  - `reject_contact_claim(contact: str) -> dict` → `{"ok": True}`

- [ ] **Step 1: Write the failing tests**

Append to `helpdesk/tests/test_contact_verification.py`:

```python
from helpdesk.api import verification as verification_api
from helpdesk.utils import get_customer


class TestApprovalApi(_StateBase):
	def setUp(self):
		super().setUp()
		self.customer = frappe.get_doc({
			"doctype": "HD Customer",
			"customer_name": PREFIX + "approve-co",
			"tax_id": "P051234567X",
		}).insert(ignore_permissions=True).name
		frappe.db.set_value("Contact", self.contact, {
			"hd_verification_status": "Claimed",
			"hd_claimed_tax_id": "P051234567X",
			"hd_claimed_company": "Blue Lake Ltd",
		})
		frappe.db.commit()

	def test_the_claim_payload_carries_the_matches(self):
		payload = verification_api.get_contact_claim(self.ticket)
		self.assertEqual(payload["status"], "Claimed")
		self.assertEqual([m["name"] for m in payload["matches"]], [self.customer])

	def test_a_single_exact_match_does_not_auto_link(self):
		"""The load-bearing rule. A PIN is printed on every invoice; matching one
		is evidence for an agent, never authorisation."""
		verification_api.get_contact_claim(self.ticket)
		self.assertEqual(get_customer(self.contact), [])
		self.assertEqual(self.status(), "Claimed")

	def test_approve_creates_the_link_that_get_customer_reads(self):
		verification_api.approve_contact_link(self.contact, self.customer)
		self.assertEqual(get_customer(self.contact), [self.customer])
		self.assertEqual(self.status(), "Verified")

	def test_approving_twice_does_not_duplicate_the_link(self):
		verification_api.approve_contact_link(self.contact, self.customer)
		verification_api.approve_contact_link(self.contact, self.customer)
		self.assertEqual(get_customer(self.contact), [self.customer])

	def test_reject_marks_it_and_creates_no_link(self):
		verification_api.reject_contact_claim(self.contact)
		self.assertEqual(self.status(), "Rejected")
		self.assertEqual(get_customer(self.contact), [])

	def test_endpoints_refuse_a_non_agent(self):
		"""Whitelisting alone bypasses permissions; these read and write customer
		linkage, so they need the agent guard."""
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		for call in (
			lambda: verification_api.get_contact_claim(self.ticket),
			lambda: verification_api.approve_contact_link(self.contact, self.customer),
			lambda: verification_api.reject_contact_claim(self.contact),
		):
			with self.assertRaises(frappe.PermissionError):
				call()
```

- [ ] **Step 2: Run and verify they fail**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module helpdesk.tests.test_contact_verification`
Expected: FAIL — `No module named 'helpdesk.api.verification'`

- [ ] **Step 3: Implement**

```python
# helpdesk/api/verification.py
"""Agent-facing actions for an unrecognised contact.

Approving is a human decision by design. A KRA PIN is printed on every invoice
and ETR receipt, so a match is evidence, not authorisation — and a wrong link
lets a stranger read another company's support history and account standing.
"""

import frappe

from helpdesk import verification
from helpdesk.integrations.wa_verification import REJECTED, VERIFIED, contact_state
from helpdesk.utils import agent_only


@frappe.whitelist()
@agent_only
def get_contact_claim(ticket: str | int) -> dict:
	"""What this ticket's contact claims, and who it could be.

	HD Ticket uses autoincrement naming, so `name` is an int in Python and a str
	over HTTP; the annotation accepts both, as in api/entitlement.py.
	"""
	empty = {
		"contact": None, "status": None, "claimed_company": None,
		"claimed_tax_id": None, "matches": [],
	}

	contact = frappe.db.get_value("HD Ticket", ticket, "contact")
	if not contact:
		return empty

	state = contact_state(contact)
	if not state:
		return empty

	claimed = state.get("hd_claimed_tax_id")
	matches = []
	for name in verification.match_claim(claimed):
		matches.append({
			"name": name,
			"customer_name": frappe.db.get_value("HD Customer", name, "customer_name") or name,
		})

	return {
		"contact": contact,
		"status": state.get("hd_verification_status"),
		"claimed_company": state.get("hd_claimed_company"),
		"claimed_tax_id": claimed,
		"matches": matches,
	}


@frappe.whitelist()
@agent_only
def approve_contact_link(contact: str, customer: str) -> dict:
	"""Link the contact to the customer an agent chose.

	Writes the Dynamic Link shape helpdesk/utils.py:91 queries, so HD Ticket
	.customer populates on the next save and product, entitlement and standing
	follow with no further work.
	"""
	if not frappe.db.exists("HD Customer", customer):
		frappe.throw(frappe._("Customer {0} does not exist.").format(customer))

	doc = frappe.get_doc("Contact", contact)
	already = [
		link.link_name for link in doc.get("links", []) if link.link_doctype == "HD Customer"
	]
	if customer not in already:
		doc.append("links", {"link_doctype": "HD Customer", "link_name": customer})
		doc.save(ignore_permissions=True)

	frappe.db.set_value("Contact", contact, "hd_verification_status", VERIFIED)
	frappe.db.commit()
	return {"ok": True, "customer": customer}


@frappe.whitelist()
@agent_only
def reject_contact_claim(contact: str) -> dict:
	"""Dismiss the claim. Stops the automated asking; creates no link."""
	frappe.db.set_value("Contact", contact, "hd_verification_status", REJECTED)
	frappe.db.commit()
	return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kushal/frappe-bench && bench --site dev.localhost run-tests --module helpdesk.tests.test_contact_verification`
Expected: PASS, 28 tests

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(verification): agent approval endpoints

Three @agent_only endpoints. Approving appends the Dynamic Link that
get_customer() reads, so HD Ticket.customer and everything downstream
resolve from the next save. A single exact match still does not
auto-link — there is a test named for it."
```

---

### Task 5: Agent UI in the contact tab

**Files:**
- Modify: `desk/src/components/ticket-agent/TicketContactTab.vue`

**Interfaces:**
- Consumes: `helpdesk.api.verification.get_contact_claim`, `.approve_contact_link`, `.reject_contact_claim` (Task 4)
- Produces: nothing consumed by later tasks

- [ ] **Step 1: Add the resource and the panel**

The file already injects the ticket and renders a coverage panel with a `watch`-driven resource (added for entitlements — `makeParams` is not reactive, so `auto: true` goes stale on navigation). Follow that same shape.

```vue
<script setup lang="ts">
// alongside the existing entitlement resource
const claim = createResource({
  url: "helpdesk.api.verification.get_contact_claim",
  makeParams: () => ({ ticket: ticket.value?.doc?.name }),
})

watch(
  () => ticket.value?.doc?.name,
  (name) => { if (name) claim.fetch() },
  { immediate: true }
)

const approve = (customer: string) =>
  createResource({
    url: "helpdesk.api.verification.approve_contact_link",
    params: { contact: claim.data.contact, customer },
  })
    .fetch()
    .then(() => claim.fetch())

const reject = () =>
  createResource({
    url: "helpdesk.api.verification.reject_contact_claim",
    params: { contact: claim.data.contact },
  })
    .fetch()
    .then(() => claim.fetch())
</script>

<template>
  <!-- after the existing coverage panel -->
  <div
    v-if="claim.data && ['Asked', 'Claimed'].includes(claim.data.status)"
    class="rounded border border-outline-gray-2 bg-surface-gray-1 p-3 text-base"
  >
    <div class="font-medium text-ink-gray-8">Unverified number</div>

    <div v-if="claim.data.status === 'Asked'" class="mt-1 text-ink-gray-6">
      Asked for a company name and KRA PIN; no reply yet.
    </div>

    <template v-else>
      <div class="mt-1 text-ink-gray-6">
        Claims
        <span class="text-ink-gray-8">{{ claim.data.claimed_company }}</span>
        &middot;
        <span class="text-ink-gray-8">{{ claim.data.claimed_tax_id }}</span>
      </div>

      <div v-if="!claim.data.matches.length" class="mt-2 text-ink-gray-6">
        No customer matches that PIN.
      </div>

      <div v-for="m in claim.data.matches" :key="m.name" class="mt-2 flex items-center gap-2">
        <span class="text-ink-gray-8">{{ m.customer_name }}</span>
        <Button variant="subtle" label="Link" @click="approve(m.name)" />
      </div>

      <Button class="mt-2" variant="ghost" label="Dismiss" @click="reject()" />
    </template>
  </div>
</template>
```

- [ ] **Step 2: Build**

Run: `cd /home/kushal/frappe-bench && bench build --app helpdesk`
Expected: build completes with no errors.

- [ ] **Step 3: Verify the semantic tokens actually compiled**

Run:
```bash
grep -rlo "text-ink-gray-8" desk/dist/assets/*.css | head -1
```
Expected: a match. `text-surface-*` compiles to nothing in this preset (`surface` is registered for backgrounds only) — if you reached for one, replace it with an `ink` token.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat(verification): show the claim and Approve/Dismiss in the contact tab

Extends the existing coverage panel rather than adding a component. Uses
watch rather than auto:true, because makeParams is not reactive and the
panel otherwise goes stale when an agent navigates between tickets."
```

---

## Manual verification before switching it on

The suite covers the logic; these cover the wiring, and none of them can be
asserted from a test:

1. `bench --site dev.localhost migrate` and confirm the settings section renders
   at `/app/whatsapp-helpdesk-settings` with the three new fields.
2. With `verification_enabled = 0`, send a WhatsApp from an unknown number:
   a ticket appears and **no prompt is sent**. This is the deploy-safety check.
3. Tick `verification_enabled`, ensure `unknown_contact_action` is
   `Create Contact and Ticket`, and repeat: the prompt arrives once.
4. Reply with a real company name and PIN. The contact tab shows the claim and
   the match.
5. Click **Link**. The claim panel is replaced in place by the coverage panel,
   showing products and standing — no page reload needed. `HD Ticket.customer`
   is populated by the same call: `set_customer()` only runs on save, so
   `approve_contact_link` backfills it for this contact's tickets that have no
   customer yet. Confirm in `/app/hd-ticket/<name>` that `customer` is set.
6. Send another message from that number: no prompt.
