# Design: WABA template picker in the reply toolbar

Date: 2026-08-04
Integration: WABA (frappe_whatsapp) only — the WA Line / Evolution API path has
no 24-hour window and is untouched.

## Problem

Meta closes the free-form reply window 24 hours after the customer's last
message. After that only approved templates may be sent.

Detection for this already exists and is wired end to end:

| Piece | Location |
|-------|----------|
| `reply_window_open` (last Incoming < 24 h) | `wa.py` `get_whatsapp_ticket_info()` ~3813-3822 |
| `allow_template_outside_window` settings gate | `wa.py` `_fw_allow_template_outside_window()` 242-244 |
| Approved-template list | `wa.py` `get_outgoing_templates()` ~4376 |
| Template send | `wa.py` `send_template_to_ticket()` ~4389 |
| Expired-window template panel | `WhatsAppChatTab.vue` ~72-95 |

What is missing is reach: templates are only offered *after* the window
expires. An agent inside the window has no way to send one.

### Defects found while exploring

1. **Templates with variables raise `NameError` today.**
   `send_template_to_ticket` calls `json.dumps(params)` (`wa.py:4424`) but `json`
   is never imported in that module. Only templates with `sample_values` set take
   that branch, which is why variable-free templates work and this went unseen.
2. **`window_open` is `True` when the ticket has no incoming message at all.**
   That path is an outgoing-only conversation, where Meta requires a template.
   The bug is in the permissive direction: the agent gets a free-form box that
   cannot succeed.
3. **No server-side enforcement.** `_send_fw_reply` never checks the window, so
   a free-form send past 24 h reaches Meta and returns rejection 131047. The
   CLAUDE.md line calling the window "Enforced" describes the UI only.

## Decisions taken

| Question | Decision |
|----------|----------|
| Picker depth | Preview the rendered body; no per-variable editing |
| Availability in-window | Template button always present and enabled |
| `allow_template_outside_window` | **Retire it** — templates always permitted |
| Server-side enforcement | Add it (free-form only; templates unaffected) |

Consequence of retiring the setting, accepted deliberately: on a site where the
flag is currently off, agents gain template sending they did not have, and the
admin control to withdraw it is gone.

## Architecture

### One reply component, one template entry point

`WhatsAppChatTab` currently renders **either** `WhatsAppReplyBox` (window open)
**or** a separate inline template panel (window closed). That fork is the reason
templates are unreachable in-window, and it puts template logic inside a
component whose job is the message list.

`WhatsAppReplyBox` becomes always-mounted and takes a `replyWindowOpen` prop:

- **window open** — unchanged behaviour, plus a Templates button in the toolbar.
- **window closed** — textarea, attach, AI and send are `disabled`; the
  placeholder is replaced with the reason; an inline notice explains why; the
  Templates button stays live and becomes the visually primary action.

`disabled`, not `readonly`: `readonly` is for content you can read and copy but
not change, and it renders looking active, so agents would click in, get a
caret, type, and see nothing happen. A greyed control is the clearer failure.
The explanation lives in visible text beside the box rather than as a tooltip on
the disabled control, since screen readers skip disabled elements.

This state persists after a template send — sending a template does not reopen
the window (see Data flow) — so it must read as a deliberate mode, not as a
transient error.

The separate panel in `WhatsAppChatTab` and its `templates` / `selectedTemplate`
/ `sendTemplate` state are deleted.

### Preview must be the same code as send

If preview re-rendered the template independently it would drift from what
actually sends. Extract the existing inline logic:

```
_render_template_for_ticket(ticket, template_name) -> (rendered_message, body_param)
```

- resolves `WhatsApp Templates.field_names` against the HD Ticket
- substitutes `{{1}}`, `{{2}}`, … into `WhatsApp Templates.template`
- returns `body_param` as the JSON string frappe_whatsapp expects
- raises the existing "variables but no Field Names configured" error unchanged

Called by both `send_template_to_ticket` and a new whitelisted
`preview_template_for_ticket(ticket, template_name)`. The agent sees the exact
string that will be sent because it is produced by the same function.

`import json` lands here — the helper is the code that calls `json.dumps`, so
defect 1 is fixed by the extraction rather than by a stray one-line patch.

## Components

### Backend — `helpdesk/integrations/wa.py`

| Change | Detail |
|--------|--------|
| `import json` | module header |
| `_render_template_for_ticket()` | new; extracted from `send_template_to_ticket` |
| `preview_template_for_ticket()` | new whitelisted endpoint returning `{message}` |
| `send_template_to_ticket()` | delegates rendering to the helper |
| `_fw_allow_template_outside_window()` | delete (242-244) |
| `get_whatsapp_ticket_info()` | drop `allow_template_outside_window` key (3834) |
| `get_whatsapp_ticket_info()` | `window_open = False` when no incoming message exists |
| `_send_fw_reply()` | throw when the window is closed |

`get_outgoing_templates()` is unchanged — name, `template_name`, `language_code`
are enough to populate the list; the body arrives via preview on selection.

### Frontend

`desk/src/components/whatsapp/WhatsAppReplyBox.vue`
- new prop `replyWindowOpen: boolean` (default `true`)
- Templates button, fourth in the toolbar row after Attach / Saved Replies / AI,
  matching their `h-9 w-9` bordered styling
- template picker rendered as a **modal**, mirroring `SavedRepliesSelectorModal`
  (already imported in this file): `v-if` + `v-model` on a `showTemplates` ref
- selecting a template calls `preview_template_for_ticket` and shows the
  rendered body; Send dispatches `send_template_to_ticket`
- when `replyWindowOpen` is false: textarea/attach/AI/send disabled, inline
  expiry notice, Templates still enabled

`desk/src/components/whatsapp/WhatsAppChatTab.vue`
- always render `WhatsAppReplyBox`, passing `:replyWindowOpen`
- delete the expired-window panel and its template state
- keep the existing `ticketInfo.reload()` on send and on incoming message

### Doctype

`helpdesk/helpdesk/doctype/whatsapp_helpdesk_settings/whatsapp_helpdesk_settings.json`
— remove `allow_template_outside_window` from both `field_order` (line 23) and
the `fields` array (line 112). Requires `bench migrate`.

## Data flow

```
agent opens ticket
  → get_whatsapp_ticket_info() → reply_window_open
      → WhatsAppChatTab passes it to WhatsAppReplyBox
agent clicks Templates
  → get_outgoing_templates()            → list of approved templates
agent selects one
  → preview_template_for_ticket()       → rendered body (same code as send)
agent clicks Send
  → send_template_to_ticket()           → WhatsApp Message (template set)
      → frappe_whatsapp before_insert → send_template() → Meta
  → ticketInfo.reload()
```

Note: sending a template does **not** reopen the free-form window. Only an
incoming customer message does, since `reply_window_open` is computed from the
last *Incoming* message. So after a template send the box stays disabled and the
Templates button stays live — which is correct, and is why the reload must not
be assumed to flip the state.

## Error handling

| Case | Behaviour |
|------|-----------|
| Template has variables, no `field_names` | Existing throw, surfaced in the picker |
| Free-form send past the window | `_send_fw_reply` throws before reaching Meta |
| No approved templates | Picker shows an empty state |
| `WhatsApp Templates` doctype absent | `get_outgoing_templates()` returns `[]`; button hidden |
| Preview fails | Error shown in the picker; Send stays disabled |

## Testing

Backend, mirroring `helpdesk/tests/test_wa_read_receipt.py` (`FrappeTestCase`,
no network — frappe_whatsapp's Meta call stubbed at the boundary):

- preview and send produce identical rendered output for the same
  ticket + template
- a template with `sample_values` renders without `NameError` — regression cover
  for defect 1
- `window_open` is `False` when the ticket has no incoming message, `True`
  within 24 h, `False` beyond it — regression cover for defect 2
- `_send_fw_reply` throws past the window and is unaffected inside it
- `get_whatsapp_ticket_info` no longer returns `allow_template_outside_window`

Frontend has no test infra in this repo: `bench build --app helpdesk` plus a
manual pass over both window states.

## Out of scope

- WA Line / Evolution API reply box — no 24-hour window there
- Editing template variable values by hand
- Template authoring or approval; templates come from frappe_whatsapp
- The ~37 pre-existing suite failures unrelated to this work
- Refreshing `reply_window_open` on a timer while a ticket sits open (gap 4 from
  exploration): the window still refreshes on send and on incoming message, so
  an agent crossing the boundary mid-session may see a stale open box until the
  next reload. Noted, not fixed here.
