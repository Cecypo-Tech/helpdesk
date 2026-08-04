# WABA Template Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put an always-available WhatsApp template picker in the WABA reply toolbar, so agents can send approved templates inside or outside Meta's 24-hour reply window.

**Architecture:** One always-mounted reply component takes a `replyWindowOpen` prop and disables free-form controls when the window is closed, keeping the Templates button live. Template body rendering is extracted into a single backend helper shared by a new preview endpoint and the existing send path, so the preview cannot drift from what is sent.

**Tech Stack:** Frappe v16 (Python 3.14), Vue 3 Composition API + TypeScript, frappe-ui, Tailwind with semantic tokens.

**Spec:** `artifacts/superpowers/2026-08-04-waba-template-picker-design.md`

## Global Constraints

- WABA path only. The WA Line / Evolution API components (`BaileysReplyBox.vue`, `BaileysGroupChatTab.vue`) have no 24-hour window and must not be touched.
- `helpdesk/integrations/wa.py` mixes indentation by region. The template functions near the end of the file use **tabs**; `mark_wa_messages_read` and its helpers near line 2340 use **4 spaces**. Match the region you edit.
- Use semantic Tailwind tokens (`text-ink-gray-5`, `border-outline-gray-3`, `bg-surface-white`), never raw palette colours, so dark mode keeps working.
- Tests must never hit the network. frappe_whatsapp calls Meta from two places, both of which must be patched:
  - `frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_templates.whatsapp_templates.make_post_request` — called from `WhatsAppTemplates.after_insert`, must return `{"id": "...", "status": "APPROVED"}`
  - `frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message.make_post_request` — called from `WhatsAppMessage.notify`, must return `{"messages": [{"id": "wamid.test"}]}`
- The dev site is `dev.localhost`; the bench root is `/home/kushal/frappe-bench`. Run test commands from the bench root.
- Existing suite baseline: ~37 pre-existing failures across three phases. Do not treat those as regressions; compare against `develop` if unsure.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `helpdesk/integrations/wa.py` | WABA + WA Line backend | Modify — render helper, preview endpoint, window helper, enforcement, setting removal |
| `helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/whatsapp_helpdesk_settings.json` | WABA settings singleton | Modify — drop retired field |
| `helpdesk/tests/test_wa_templates.py` | Backend regression cover | Create |
| `desk/src/components/whatsapp/WhatsAppTemplateModal.vue` | Template list + preview + send | Create |
| `desk/src/components/whatsapp/WhatsAppReplyBox.vue` | WABA reply toolbar | Modify — Templates button, closed-window state |
| `desk/src/components/whatsapp/WhatsAppChatTab.vue` | WABA message list + reply host | Modify — delete expired panel, pass prop |
| `CLAUDE.md` | Fork documentation | Modify — correct the window claim, note the retired setting |

The picker goes in its own component rather than inside `WhatsAppReplyBox.vue`, which is already 482 lines and owns attachments, paste/drag handling, AI suggestions and saved replies.

---

### Task 1: Extract template rendering and add a preview endpoint

Fixes the live `NameError`: `send_template_to_ticket` calls `json.dumps` at `wa.py:4424` but `json` is never imported, so every template with `sample_values` fails today.

**Files:**
- Modify: `helpdesk/integrations/wa.py` (imports at line 2; `send_template_to_ticket` ~4389-4444)
- Test: `helpdesk/tests/test_wa_templates.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `_render_template_for_ticket(ticket: str, template_name: str) -> tuple[str, str | None]` returning `(rendered_message, body_param)`
  - `preview_template_for_ticket(ticket: str, template_name: str) -> dict` whitelisted, returning `{"message": str}`

- [ ] **Step 1: Write the failing test**

Create `helpdesk/tests/test_wa_templates.py`:

```python
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.integrations import wa

TEMPLATES_POST = (
	"frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_templates"
	".whatsapp_templates.make_post_request"
)
MESSAGE_POST = (
	"frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message"
	".whatsapp_message.make_post_request"
)


class TestWATemplates(FrappeTestCase):
	"""Cover the WABA template render/preview/send path.

	Nothing here touches the network: frappe_whatsapp calls Meta from
	WhatsAppTemplates.after_insert and WhatsAppMessage.notify, and both are
	patched at the make_post_request boundary.
	"""

	def setUp(self):
		self.addCleanup(frappe.db.commit)
		frappe.set_user("Administrator")
		if not frappe.db.exists("DocType", "WhatsApp Templates"):
			self.skipTest("frappe_whatsapp is not installed")

		self.ticket = frappe.get_doc({
			"doctype": "HD Ticket",
			"subject": "Template render test ticket",
			"raised_by": "wa-template-test@example.com",
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "HD Ticket", self.ticket.name, ignore_permissions=True, force=True
		)

	def _make_template(self, body, sample_values=None, field_names=None):
		with patch(TEMPLATES_POST, return_value={"id": "1", "status": "APPROVED"}):
			doc = frappe.get_doc({
				"doctype": "WhatsApp Templates",
				"template_name": f"tpl_{frappe.generate_hash(length=8)}",
				"template": body,
				"language_code": "en",
				"category": "UTILITY",
				"sample_values": sample_values or "",
				"field_names": field_names or "",
			}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "WhatsApp Templates", doc.name, ignore_permissions=True, force=True
		)
		return doc

	def test_template_with_variables_renders_without_name_error(self):
		# Regression: json.dumps was called with no `json` import in wa.py, so
		# every template carrying sample_values raised NameError.
		tpl = self._make_template(
			"Hello, your ticket {{1}} is open.",
			sample_values="Ticket",
			field_names="name",
		)

		message, body_param = wa._render_template_for_ticket(self.ticket.name, tpl.name)

		self.assertIn(str(self.ticket.name), message)
		self.assertNotIn("{{1}}", message)
		self.assertIn(str(self.ticket.name), body_param)

	def test_template_without_variables_renders_body_unchanged(self):
		tpl = self._make_template("We have received your request.")

		message, body_param = wa._render_template_for_ticket(self.ticket.name, tpl.name)

		self.assertEqual(message, "We have received your request.")
		self.assertIsNone(body_param)

	def test_preview_returns_exactly_what_render_produces(self):
		tpl = self._make_template(
			"Ticket {{1}} update.", sample_values="Ticket", field_names="name"
		)

		rendered, _body = wa._render_template_for_ticket(self.ticket.name, tpl.name)
		preview = wa.preview_template_for_ticket(self.ticket.name, tpl.name)

		self.assertEqual(preview["message"], rendered)

	def test_variables_without_field_names_raise_a_configuration_error(self):
		tpl = self._make_template("Hi {{1}}", sample_values="Name", field_names="")

		with self.assertRaises(frappe.ValidationError):
			wa._render_template_for_ticket(self.ticket.name, tpl.name)
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `/home/kushal/frappe-bench`:

```bash
bench --site dev.localhost run-tests --module helpdesk.tests.test_wa_templates
```

Expected: FAIL — `AttributeError: module 'helpdesk.integrations.wa' has no attribute '_render_template_for_ticket'`

- [ ] **Step 3: Add the json import**

In `helpdesk/integrations/wa.py`, change the import block at the top (line 2) from:

```python
import re
from urllib.parse import quote as _urlquote
```

to:

```python
import json
import re
from urllib.parse import quote as _urlquote
```

- [ ] **Step 4: Extract the render helper**

In `helpdesk/integrations/wa.py`, immediately above `send_template_to_ticket`, add (tabs — this region uses tabs):

```python
def _render_template_for_ticket(ticket: str, template_name: str) -> tuple[str, str | None]:
	"""Render a WhatsApp Template against a ticket.

	Returns (rendered_message, body_param). Shared by preview_template_for_ticket
	and send_template_to_ticket so the agent can never be shown a different
	string from the one that goes to Meta.

	body_param is the JSON object frappe_whatsapp's send_template() reads to take
	its explicit-parameter branch. Its default branch misreads sample_values as
	field names and sends empty strings, which Meta rejects with #131008.
	"""
	template_doc = frappe.get_doc("WhatsApp Templates", template_name)
	body_param = None
	params = {}
	if template_doc.sample_values:
		if not template_doc.field_names:
			frappe.throw(
				_("Template {0} has variables but no Field Names are configured. "
				  "Open the WhatsApp Template and set Field Names to the HD Ticket "
				  "field names that should fill each variable.").format(template_name)
			)
		ticket_doc = frappe.get_doc("HD Ticket", ticket)
		field_names = [f.strip() for f in template_doc.field_names.split(",")]
		for i, fn in enumerate(field_names, 1):
			raw = ticket_doc.get_formatted(fn)
			params[str(i)] = frappe.utils.strip_html(raw) if raw else (
				str(ticket_doc.get(fn)) if ticket_doc.get(fn) is not None else ""
			)
		body_param = json.dumps(params)

	# frappe_whatsapp never sets `message` on template sends, leaving the chat
	# bubble blank, so we render the body ourselves for display.
	rendered_message = template_doc.template or ""
	for idx, value in params.items():
		rendered_message = rendered_message.replace("{{" + idx + "}}", str(value))
	return rendered_message, body_param


@frappe.whitelist()
def preview_template_for_ticket(ticket: str, template_name: str) -> dict:
	"""Return the template body exactly as it will be sent to this ticket's contact."""
	if not frappe.db.exists("WhatsApp Templates", template_name):
		frappe.throw(_("Template {0} not found.").format(template_name))
	message, _body_param = _render_template_for_ticket(ticket, template_name)
	return {"message": message}
```

- [ ] **Step 5: Make send_template_to_ticket use the helper**

In `helpdesk/integrations/wa.py`, inside `send_template_to_ticket`, delete the inline rendering block — everything from `template_doc = frappe.get_doc("WhatsApp Templates", template_name)` through the `for idx, value in params.items():` loop — and replace it with:

```python
	rendered_message, body_param = _render_template_for_ticket(ticket, template_name)
```

The function body after the existing `phone` guard should read:

```python
	rendered_message, body_param = _render_template_for_ticket(ticket, template_name)

	msg_doc = frappe.get_doc({
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": phone,
		"template": template_name,
		"body_param": body_param,
		"message": rendered_message,
		"reference_doctype": "HD Ticket",
		"reference_name": ticket,
	})
	msg_doc.insert(ignore_permissions=True)
	return {"name": msg_doc.name, "status": msg_doc.status}
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
bench --site dev.localhost run-tests --module helpdesk.tests.test_wa_templates
```

Expected: PASS, 4 tests.

- [ ] **Step 7: Confirm no undefined-name lint remains**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk && ruff check --select F821 helpdesk/integrations/wa.py
```

Expected: the `json` F821 at the old line 4424 is gone. (An unrelated pre-existing `F841` unused `settings` may remain — leave it.)

- [ ] **Step 8: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add helpdesk/integrations/wa.py helpdesk/tests/test_wa_templates.py
git commit -m "fix(wa): render WhatsApp templates through one shared helper

send_template_to_ticket called json.dumps with no json import in the
module, so every template carrying sample_values raised NameError.
Extract the rendering into _render_template_for_ticket and expose it as
preview_template_for_ticket, so the preview the agent sees is produced by
the same code that builds the outgoing message."
```

---

### Task 2: Correct window detection and retire the settings gate

**Files:**
- Modify: `helpdesk/integrations/wa.py` (`_fw_allow_template_outside_window` 242-244; `get_whatsapp_ticket_info` window block ~3813-3835)
- Modify: `helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/whatsapp_helpdesk_settings.json`
- Test: `helpdesk/tests/test_wa_templates.py` (extend)

**Interfaces:**
- Consumes: nothing from Task 1
- Produces: `_fw_reply_window_open(ticket: str | int) -> bool` — used by Task 3
- Removes: `_fw_allow_template_outside_window()`, and the `allow_template_outside_window` key from `get_whatsapp_ticket_info()`'s return

- [ ] **Step 1: Write the failing tests**

Append to `helpdesk/tests/test_wa_templates.py`:

```python
	def _make_incoming(self, age_hours=0):
		doc = frappe.get_doc({
			"doctype": "WhatsApp Message",
			"type": "Incoming",
			"from": "254700000000",
			"message": "hello",
			"content_type": "text",
			"message_id": f"wamid.tpl.{frappe.generate_hash(length=10)}",
			"status": "received",
			"reference_doctype": "HD Ticket",
			"reference_name": self.ticket.name,
		}).insert(ignore_permissions=True)
		self.addCleanup(
			frappe.delete_doc, "WhatsApp Message", doc.name, ignore_permissions=True, force=True
		)
		if age_hours:
			frappe.db.set_value(
				"WhatsApp Message", doc.name, "creation",
				frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-age_hours),
				update_modified=False,
			)
		return doc

	def test_window_is_closed_when_no_incoming_message_exists(self):
		# Outgoing-only is a business-initiated conversation, which Meta allows
		# only via a template. Defaulting this open handed the agent a free-form
		# box that could never succeed.
		self.assertFalse(wa._fw_reply_window_open(self.ticket.name))

	def test_window_is_open_within_24h_of_the_last_incoming(self):
		self._make_incoming(age_hours=2)
		self.assertTrue(wa._fw_reply_window_open(self.ticket.name))

	def test_window_is_closed_beyond_24h(self):
		self._make_incoming(age_hours=30)
		self.assertFalse(wa._fw_reply_window_open(self.ticket.name))

	def test_ticket_info_no_longer_exposes_the_retired_setting(self):
		self._make_incoming(age_hours=1)
		info = wa.get_whatsapp_ticket_info(self.ticket.name)
		self.assertNotIn("allow_template_outside_window", info)
		self.assertTrue(info["reply_window_open"])
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
bench --site dev.localhost run-tests --module helpdesk.tests.test_wa_templates
```

Expected: FAIL — `module 'helpdesk.integrations.wa' has no attribute '_fw_reply_window_open'`

- [ ] **Step 3: Add the window helper**

In `helpdesk/integrations/wa.py`, replace `_fw_allow_template_outside_window` (lines 242-244) entirely — this region uses **4 spaces**:

```python
def _fw_reply_window_open(ticket: str | int) -> bool:
    """True when Meta still accepts free-form messages on this ticket.

    The window runs 24 hours from the customer's last *incoming* message. With
    no incoming message at all the conversation is business-initiated, which
    Meta permits only via a template — so the window is closed, not open.
    """
    last_incoming = frappe.db.get_value(
        "WhatsApp Message",
        {"reference_doctype": "HD Ticket", "reference_name": ticket, "type": "Incoming"},
        "creation",
        order_by="creation desc",
    )
    if not last_incoming:
        return False
    return time_diff_in_hours(now_datetime(), last_incoming) < 24
```

- [ ] **Step 4: Use the helper in get_whatsapp_ticket_info**

In `helpdesk/integrations/wa.py`, in the frappe_whatsapp branch of `get_whatsapp_ticket_info`, delete this block:

```python
	# Determine if the 24-hour reply window is open
	last_incoming = frappe.db.get_value(
		"WhatsApp Message",
		{"reference_doctype": "HD Ticket", "reference_name": ticket, "type": "Incoming"},
		"creation",
		order_by="creation desc",
	)
	window_open = True
	if last_incoming:
		window_open = time_diff_in_hours(now_datetime(), last_incoming) < 24
```

and replace it with:

```python
	window_open = _fw_reply_window_open(ticket)
```

Then delete this line from the returned dict:

```python
		"allow_template_outside_window": _fw_allow_template_outside_window(),
```

- [ ] **Step 5: Remove the retired settings field**

In `helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/whatsapp_helpdesk_settings.json`:

- delete the `"allow_template_outside_window",` entry from `field_order` (index 14, line 23)
- delete this object from the `fields` array (around line 112):

```json
  {
   "default": "0",
   "fieldname": "allow_template_outside_window",
   "fieldtype": "Check",
   "label": "Allow Template Messages After 24h Window"
  },
```

- [ ] **Step 6: Apply the doctype change**

```bash
cd /home/kushal/frappe-bench && bench --site dev.localhost migrate
```

Expected: completes without error.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
bench --site dev.localhost run-tests --module helpdesk.tests.test_wa_templates
```

Expected: PASS, 8 tests.

- [ ] **Step 8: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add helpdesk/integrations/wa.py helpdesk/tests/test_wa_templates.py \
  helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/whatsapp_helpdesk_settings.json
git commit -m "fix(wa): close the reply window when no incoming message exists

A ticket with only outgoing messages is business-initiated, which Meta
allows only via a template, but reply_window_open defaulted to True and
handed the agent a free-form box that could never succeed.

Also retires allow_template_outside_window: templates are now always
available from the reply toolbar, so the gate no longer has a meaning."
```

---

### Task 3: Enforce the window server-side

Until now the window was UI-only, so any caller of `send_wa_reply` past 24 hours reached Meta and came back rejected with 131047.

**Files:**
- Modify: `helpdesk/integrations/wa.py` (`_send_fw_reply` ~3397-3410)
- Test: `helpdesk/tests/test_wa_templates.py` (extend)

**Interfaces:**
- Consumes: `_fw_reply_window_open(ticket) -> bool` from Task 2
- Produces: nothing new

- [ ] **Step 1: Write the failing tests**

Append to `helpdesk/tests/test_wa_templates.py`:

```python
	def test_free_form_reply_is_refused_past_the_window(self):
		self._make_incoming(age_hours=30)

		with patch(MESSAGE_POST) as post:
			with self.assertRaises(frappe.ValidationError):
				wa._send_fw_reply(self.ticket.name, "too late")

		post.assert_not_called()

	def test_free_form_reply_is_allowed_inside_the_window(self):
		self._make_incoming(age_hours=1)

		with patch(MESSAGE_POST, return_value={"messages": [{"id": "wamid.test"}]}) as post:
			result = wa._send_fw_reply(self.ticket.name, "still open")

		self.assertTrue(post.called)
		self.assertTrue(result["name"])
		self.addCleanup(
			frappe.delete_doc, "WhatsApp Message", result["name"],
			ignore_permissions=True, force=True,
		)
```

Note: `_send_fw_reply` resolves the contact phone via `get_contact_phone(ticket)` and throws if there is none. The ticket created in `setUp` uses `raised_by`, so if these two tests fail on a missing phone, add a contact with a mobile number in `setUp` using the `phone_nos` child table with `is_primary_mobile_no: 1` — setting `mobile_no` directly is overwritten by `Contact.validate()`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
bench --site dev.localhost run-tests --module helpdesk.tests.test_wa_templates
```

Expected: FAIL — `test_free_form_reply_is_refused_past_the_window` does not raise; the send proceeds.

- [ ] **Step 3: Add the guard**

In `helpdesk/integrations/wa.py`, in `_send_fw_reply`, directly after the existing phone guard (this region uses **tabs**):

```python
	phone = get_contact_phone(ticket)
	if not phone:
		frappe.throw(_("No phone number found for the contact linked to this ticket."))
```

insert:

```python
	# Meta rejects free-form sends outside the 24-hour window with error 131047.
	# Refusing here turns an opaque delivery failure into an actionable message.
	if not _fw_reply_window_open(ticket):
		frappe.throw(
			_("The 24-hour reply window has closed. Send an approved template instead.")
		)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
bench --site dev.localhost run-tests --module helpdesk.tests.test_wa_templates
```

Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add helpdesk/integrations/wa.py helpdesk/tests/test_wa_templates.py
git commit -m "fix(wa): refuse free-form WABA replies past the 24-hour window

The window was enforced in the UI only, so any other caller reached Meta
and came back rejected with 131047. Template sends are unaffected."
```

---

### Task 4: Template picker modal

**Files:**
- Create: `desk/src/components/whatsapp/WhatsAppTemplateModal.vue`

**Interfaces:**
- Consumes: `helpdesk.integrations.wa.get_outgoing_templates` (existing, whitelisted, returns `[{name, template_name, language_code}]`); `helpdesk.integrations.wa.preview_template_for_ticket` from Task 1; `helpdesk.integrations.wa.send_template_to_ticket` (existing)
- Produces: component `WhatsAppTemplateModal` with prop `ticketId: string`, `v-model` open state via `defineModel()`, and emit `(e: "sent")`

- [ ] **Step 1: Create the component**

Create `desk/src/components/whatsapp/WhatsAppTemplateModal.vue`:

```vue
<template>
  <Dialog v-model="show" :options="{ size: 'xl' }">
    <template #body>
      <div class="p-4">
        <div class="mb-3 text-lg font-semibold text-ink-gray-9">
          {{ __("Send a WhatsApp Template") }}
        </div>

        <div v-if="templates.loading" class="py-6 text-center text-sm text-ink-gray-5">
          {{ __("Loading templates…") }}
        </div>

        <div
          v-else-if="!(templates.data || []).length"
          class="py-6 text-center text-sm text-ink-gray-5"
        >
          {{ __("No approved templates available.") }}
        </div>

        <div v-else class="flex flex-col gap-3">
          <select
            v-model="selected"
            class="w-full rounded-lg border border-outline-gray-3 bg-surface-white px-3 py-2 text-sm text-ink-gray-8 focus:border-outline-gray-4 focus:outline-none"
            :disabled="sending"
            @change="loadPreview"
          >
            <option value="">{{ __("Select a template…") }}</option>
            <option v-for="t in templates.data" :key="t.name" :value="t.name">
              {{ t.template_name || t.name }}
            </option>
          </select>

          <div
            v-if="selected"
            class="rounded-lg border border-outline-gray-2 bg-surface-gray-1 p-3 text-sm text-ink-gray-8"
          >
            <div class="mb-1 text-xs font-medium text-ink-gray-5">{{ __("Preview") }}</div>
            <div v-if="previewLoading" class="text-ink-gray-5">{{ __("Rendering…") }}</div>
            <div v-else-if="previewError" class="text-red-600">{{ previewError }}</div>
            <div v-else class="whitespace-pre-wrap">{{ previewText }}</div>
          </div>

          <div class="flex justify-end gap-2">
            <Button :label="__('Cancel')" @click="show = false" />
            <Button
              variant="solid"
              theme="green"
              :label="__('Send')"
              :loading="sending"
              :disabled="!selected || previewLoading || !!previewError"
              @click="sendTemplate"
            />
          </div>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup lang="ts">
import { Button, Dialog, call, createResource, toast } from "frappe-ui";
import { ref } from "vue";

const props = defineProps<{ ticketId: string }>();
const emit = defineEmits<{ (e: "sent"): void }>();

const show = defineModel<boolean>();

const selected = ref("");
const previewText = ref("");
const previewError = ref("");
const previewLoading = ref(false);
const sending = ref(false);

const templates = createResource({
  url: "helpdesk.integrations.wa.get_outgoing_templates",
  auto: true,
});

async function loadPreview() {
  previewText.value = "";
  previewError.value = "";
  if (!selected.value) return;
  previewLoading.value = true;
  try {
    const res = await call("helpdesk.integrations.wa.preview_template_for_ticket", {
      ticket: props.ticketId,
      template_name: selected.value,
    });
    previewText.value = res?.message || "";
  } catch (e: any) {
    previewError.value = e?.messages?.[0] || __("Could not render this template.");
  } finally {
    previewLoading.value = false;
  }
}

async function sendTemplate() {
  if (!selected.value || sending.value) return;
  sending.value = true;
  try {
    await call("helpdesk.integrations.wa.send_template_to_ticket", {
      ticket: props.ticketId,
      template_name: selected.value,
    });
    toast.success(__("Template sent"));
    selected.value = "";
    previewText.value = "";
    show.value = false;
    emit("sent");
  } catch (e: any) {
    toast.error(e?.messages?.[0] || __("Failed to send template"));
  } finally {
    sending.value = false;
  }
}
</script>
```

- [ ] **Step 2: Verify it compiles**

```bash
cd /home/kushal/frappe-bench && bench build --app helpdesk
```

Expected: build succeeds with no errors referencing `WhatsAppTemplateModal.vue`. (The component is not yet mounted anywhere; this step only proves it parses and type-checks.)

- [ ] **Step 3: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add desk/src/components/whatsapp/WhatsAppTemplateModal.vue
git commit -m "feat(wa): add the WhatsApp template picker modal

Lists approved templates and previews the rendered body through
preview_template_for_ticket, which shares its rendering with the send
path so the agent sees exactly what will go out."
```

---

### Task 5: Templates button and closed-window state in the reply box

**Files:**
- Modify: `desk/src/components/whatsapp/WhatsAppReplyBox.vue` (props ~192-195; toolbar row ~69-115; textarea ~150-160; modal mount ~177-183; script imports ~186-190)

**Interfaces:**
- Consumes: `WhatsAppTemplateModal` from Task 4
- Produces: `WhatsAppReplyBox` gains prop `replyWindowOpen?: boolean` (default `true`) — consumed by Task 6

- [ ] **Step 1: Add the prop**

In `desk/src/components/whatsapp/WhatsAppReplyBox.vue`, change:

```ts
const props = defineProps<{
  ticketId: string;
  replyTo?: Record<string, any> | null;
}>();
```

to:

```ts
const props = withDefaults(
  defineProps<{
    ticketId: string;
    replyTo?: Record<string, any> | null;
    replyWindowOpen?: boolean;
  }>(),
  { replyWindowOpen: true }
);
```

- [ ] **Step 2: Import the modal and add its state**

In the same `<script setup>` block, add the import next to the existing `SavedRepliesSelectorModal` import:

```ts
import WhatsAppTemplateModal from "@/components/whatsapp/WhatsAppTemplateModal.vue";
```

and add the ref beside `showSavedReplies`:

```ts
const showTemplates = ref(false);
```

- [ ] **Step 3: Add the Templates button to the toolbar**

In the template, directly after the closing `</button>` of the AI suggest button and before the AI suggestion popover, add:

```html
      <!-- Templates button -->
      <button
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7"
        :class="
          replyWindowOpen
            ? 'border-outline-gray-3'
            : 'border-green-600 bg-green-600 text-white hover:bg-green-700 hover:text-white'
        "
        :title="
          replyWindowOpen
            ? 'WhatsApp templates'
            : 'Reply window closed — send a template'
        "
        @click="showTemplates = true"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2"/>
          <line x1="3" y1="9" x2="21" y2="9"/>
          <line x1="9" y1="21" x2="9" y2="9"/>
        </svg>
      </button>
```

The button turns solid green when the window is closed, so the eye lands on the one control that still works.

- [ ] **Step 4: Disable the free-form controls when the window is closed**

Change the attach button's opening tag to add `:disabled="!replyWindowOpen"` and the disabled styling:

```html
      <button
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-outline-gray-3 text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7 disabled:cursor-not-allowed disabled:opacity-50"
        title="Attach file"
        :disabled="!replyWindowOpen"
        @click="fileInput?.click()"
      >
```

Change the Saved Replies button the same way:

```html
      <button
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-outline-gray-3 text-ink-gray-5 hover:bg-surface-gray-1 hover:text-ink-gray-7 disabled:cursor-not-allowed disabled:opacity-50"
        title="Saved Replies"
        :disabled="!replyWindowOpen"
        @click="showSavedReplies = true"
      >
```

Change the AI button's `:disabled="aiLoading"` to:

```html
        :disabled="aiLoading || !replyWindowOpen"
```

Change the textarea's `:disabled` and `:placeholder`:

```html
        :disabled="sending || !replyWindowOpen"
        :placeholder="
          !replyWindowOpen
            ? '24-hour reply window closed — send a template to re-engage'
            : attachments.length
              ? 'Add a caption (optional)...'
              : 'Type a message...'
        "
```

Change the send button's `:disabled`:

```html
        :disabled="(!text.trim() && !attachments.length) || sending || !replyWindowOpen"
```

- [ ] **Step 5: Add the visible explanation and mount the modal**

Directly above the `<div class="flex items-end gap-2">` toolbar row, add:

```html
    <div
      v-if="!replyWindowOpen"
      class="mb-2 flex items-center gap-1.5 text-xs text-ink-gray-5"
    >
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="shrink-0"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      {{ __("The 24-hour reply window has closed. Only approved templates can be sent until the customer replies.") }}
    </div>
```

This sits in normal text rather than on the disabled controls, because screen readers skip disabled elements.

Then next to the existing `SavedRepliesSelectorModal` mount, add:

```html
  <WhatsAppTemplateModal
    v-if="showTemplates"
    v-model="showTemplates"
    :ticketId="ticketId"
    @sent="$emit('sent')"
  />
```

- [ ] **Step 6: Build and verify**

```bash
cd /home/kushal/frappe-bench && bench build --app helpdesk
```

Expected: build succeeds.

- [ ] **Step 7: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add desk/src/components/whatsapp/WhatsAppReplyBox.vue
git commit -m "feat(wa): add a Templates button to the WABA reply toolbar

The button sits alongside attach, saved replies and AI, and stays live
when the 24-hour window closes — where it becomes the primary action and
the free-form controls are disabled with the reason in the placeholder."
```

---

### Task 6: Unify the chat tab on one reply component

**Files:**
- Modify: `desk/src/components/whatsapp/WhatsAppChatTab.vue` (reply/expired fork ~62-97; template state 127-128, 166-186, 300-307)

**Interfaces:**
- Consumes: `replyWindowOpen` prop from Task 5
- Produces: nothing

- [ ] **Step 1: Always render the reply box**

Replace the whole reply-or-expired fork — the `<WhatsAppReplyBox ... v-if="ticketInfo.data.reply_window_open" />` element together with the entire `<!-- Window expired — template sender -->` `<div v-else>` block that follows it — with:

```html
      <WhatsAppReplyBox
        :ticketId="ticketId"
        :replyTo="replyingTo"
        :replyWindowOpen="ticketInfo.data.reply_window_open"
        @sent="onMessageSent"
        @clearReply="replyingTo = null"
      />
```

- [ ] **Step 2: Delete the now-unused template state**

In the `<script setup>` block, delete:

- `const selectedTemplate = ref("");` (line 127)
- `const sendingTemplate = ref(false);` (line 128)
- the whole `const templates = createResource({ url: "helpdesk.integrations.wa.get_outgoing_templates", auto: true });` block (lines 166-169)
- the whole `const sendTemplateResource = createResource({ url: "helpdesk.integrations.wa.send_template_to_ticket", ... });` block (lines 171-186)
- the whole `function sendTemplate() { ... }` (lines 300-307)

Leave `messages`, `ticketInfo`, `scrollToBottom` and `onMessageSent` alone — `onMessageSent` still reloads both resources after a send, which now covers template sends too, since the modal emits `sent` up through the reply box.

- [ ] **Step 3: Confirm nothing still references the deleted state**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
grep -n "selectedTemplate\|sendingTemplate\|sendTemplateResource\|allow_template_outside_window" desk/src/components/whatsapp/WhatsAppChatTab.vue
```

Expected: no output.

- [ ] **Step 4: Build**

```bash
cd /home/kushal/frappe-bench && bench build --app helpdesk
```

Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add desk/src/components/whatsapp/WhatsAppChatTab.vue
git commit -m "refactor(wa): render one reply component in the WABA chat tab

The tab used to swap the reply box for a separate template panel when the
window closed, which is why templates were unreachable inside the window
and why template logic lived in the message-list component."
```

---

### Task 7: Verify end to end and update the fork docs

**Files:**
- Modify: `CLAUDE.md` (WABA table row "24-hr window"; Backend section)

- [ ] **Step 1: Run the backend tests**

```bash
cd /home/kushal/frappe-bench
bench --site dev.localhost run-tests --module helpdesk.tests.test_wa_templates
```

Expected: PASS, 10 tests.

- [ ] **Step 2: Check for regressions against the baseline**

```bash
cd /home/kushal/frappe-bench
bench --site dev.localhost run-tests --app helpdesk 2>&1 | grep -E "^Ran |^(OK|FAILED)"
```

Expected: three phases. Phase counts should match `develop` except for +10 in the phase carrying `helpdesk.tests.*`, with no new failures. If a phase shows more failures than `develop`, stop and investigate before continuing.

- [ ] **Step 3: Restart the server and rebuild**

```bash
cd /home/kushal/frappe-bench
pkill -f "frappe.app"; bench serve --port 8002 &
bench build --app helpdesk
```

- [ ] **Step 4: Manual pass — window open**

Open a WABA ticket (one with WhatsApp messages and **no** `baileys_jid`) at `http://dev.localhost:8002/helpdesk/tickets/<id>` whose last incoming message is under 24 hours old. Confirm:
- the reply box is editable, and attach / saved replies / AI all work
- a fourth Templates button appears in the toolbar with a normal grey border
- clicking it opens the modal, selecting a template shows a rendered preview with ticket values substituted (no `{{1}}` left)
- sending posts the template and the bubble shows the rendered body

- [ ] **Step 5: Manual pass — window closed**

On a ticket whose last incoming message is over 24 hours old (or which has none), confirm:
- the textarea is greyed and reads "24-hour reply window closed — send a template to re-engage"
- attach, saved replies, AI and send are all disabled
- the explanatory line is visible above the toolbar
- the Templates button is solid green and still opens the modal
- after sending a template the box stays closed — a template does not reopen the window; only a customer reply does

- [ ] **Step 6: Update CLAUDE.md**

In the WABA table, change the `24-hr window` row from:

```
| **24-hr window** | Enforced — after 24 h only templates can be sent |
```

to:

```
| **24-hr window** | Enforced in both UI and API — `_fw_reply_window_open()` gates `_send_fw_reply()`; past the window only templates can be sent. A ticket with no incoming message counts as closed (business-initiated conversations require a template). Sending a template does **not** reopen the window; only a customer reply does. |
```

In the same table, change the `Settings` row from:

```
| **Settings** | `WhatsApp Helpdesk Settings` singleton |
```

to:

```
| **Settings** | `WhatsApp Helpdesk Settings` singleton. `allow_template_outside_window` was retired on 2026-08-04 — templates are always available from the reply toolbar. |
```

In the Frontend file list, add:

```
- `desk/src/components/whatsapp/WhatsAppTemplateModal.vue` — WABA template picker with live preview (rendered by `preview_template_for_ticket`, which shares its rendering with the send path)
```

- [ ] **Step 7: Commit**

```bash
cd /home/kushal/frappe-bench/apps/helpdesk
git add CLAUDE.md
git commit -m "docs: record WABA window enforcement and the template picker"
```

---

## Self-Review

**Spec coverage:**

| Spec item | Task |
|-----------|------|
| `import json` / defect 1 | 1 |
| `_render_template_for_ticket` | 1 |
| `preview_template_for_ticket` | 1 |
| `send_template_to_ticket` delegates | 1 |
| Delete `_fw_allow_template_outside_window` | 2 |
| Drop `allow_template_outside_window` key | 2 |
| `window_open = False` with no incoming / defect 2 | 2 |
| `_send_fw_reply` enforcement / defect 3 | 3 |
| Doctype field removal | 2 |
| Templates button, 4th in toolbar | 5 |
| Picker modal mirroring `SavedRepliesSelectorModal` | 4 |
| `replyWindowOpen` prop | 5 |
| `disabled` + corrected placeholder + primary Templates button | 5 |
| Explanation as visible text, not on a disabled control | 5 |
| Delete the expired panel and its state | 6 |
| Tests: preview == send, NameError regression, window states, enforcement, retired key | 1, 2, 3 |
| Frontend verification via build + manual | 4, 5, 6, 7 |

No gaps.

**Type consistency:** `_render_template_for_ticket` returns `tuple[str, str | None]` in Task 1 and is destructured as `rendered_message, body_param` in Task 1 Step 5 — consistent. `_fw_reply_window_open(ticket) -> bool` is defined in Task 2 and consumed in Task 3 with the same name and signature. `replyWindowOpen` is spelled identically in Tasks 5 and 6. `WhatsAppTemplateModal`'s `v-model` + `ticketId` prop + `sent` emit match its mount in Task 5.

**Known risk carried forward:** `reply_window_open` is still computed only when `ticketInfo` loads, so an agent sitting on a ticket across the 24-hour boundary keeps a stale open box until the next reload. Documented as out of scope in the spec; not addressed here.
