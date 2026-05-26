# WA Contact LID/PN Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate duplicate WA Contact rows caused by the same person having both a `@lid` JID and a `@s.whatsapp.net` JID; merge all messages and tickets to the PN row and re-route future LID traffic automatically.

**Architecture:** Add a `canonical_jid` field to `WA Contact` — empty means the row is live, non-empty means it is a dead LID alias pointing to the PN JID. A single `_merge_lid_into_pn()` function re-keys `WA Message.jid` and `HD Ticket.baileys_jid` physically and marks the LID row as an alias. Incoming webhook messages on a LID JID are silently re-routed to the PN JID before any processing. Background sync is moved to an RQ job so the UI button returns immediately.

**Tech Stack:** Python (Frappe framework), MariaDB raw SQL for bulk ops, Vue 3 / frappe-ui for frontend sync button, Frappe RQ for background job, `frappe.publish_realtime` for completion event.

---

## File Map

| File | Action |
|------|--------|
| `helpdesk/helpdesk/doctype/wa_contact/wa_contact.json` | Add `canonical_jid` Data field |
| `helpdesk/integrations/wa.py` | All backend logic changes (Tasks 2–8) |
| `helpdesk/helpdesk/doctype/wa_api_settings/wa_api_settings.js` | Wipe button (Task 8) |
| `desk/src/components/whatsapp/BaileysConversationList.vue` | Async sync button (Task 9) |
| `helpdesk/integrations/tests/test_wa_webhook.py` | New test cases (Tasks 2–7) |

---

## Task 1: Schema — add `canonical_jid` to WA Contact

**Files:**
- Modify: `helpdesk/helpdesk/doctype/wa_contact/wa_contact.json`

- [ ] **Step 1: Add the field to `wa_contact.json`**

In `wa_contact.json`, add `"canonical_jid"` to `"field_order"` after `"assigned_team"`, and add this object to the `"fields"` array:

```json
{
 "fieldname": "canonical_jid",
 "fieldtype": "Data",
 "label": "Canonical JID",
 "description": "If set, this row is a dead LID alias. All traffic is redirected to this PN JID."
}
```

The complete `field_order` becomes:
```json
"field_order": [
 "jid",
 "phone",
 "col_break_1",
 "custom_name",
 "company",
 "section_assignment",
 "assigned_team",
 "canonical_jid"
]
```

- [ ] **Step 2: Run migrate**

```bash
cd /home/frappeuser/bench16
bench --site site16.local migrate
```

Expected: no errors; output ends with `[DONE]` lines.

- [ ] **Step 3: Write a schema test in `test_wa_webhook.py`**

Add to the existing `TestWaWebhook` class in `helpdesk/integrations/tests/test_wa_webhook.py`:

```python
def test_wa_contact_has_canonical_jid_field(self):
    meta = frappe.get_meta("WA Contact")
    field_names = [f.fieldname for f in meta.fields]
    self.assertIn("canonical_jid", field_names)
```

- [ ] **Step 4: Run the test**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_wa_contact_has_canonical_jid_field
```

Expected: `OK (1 test)`

- [ ] **Step 5: Commit**

```bash
git add helpdesk/helpdesk/doctype/wa_contact/wa_contact.json \
        helpdesk/integrations/tests/test_wa_webhook.py
git commit -m "feat(wa): add canonical_jid field to WA Contact for LID alias tracking"
```

---

## Task 2: Core merge function `_merge_lid_into_pn`

**Files:**
- Modify: `helpdesk/integrations/wa.py` (after `_upsert_contact_name`, around line 458)

- [ ] **Step 1: Write the failing test**

Add to `TestWaWebhook` in `helpdesk/integrations/tests/test_wa_webhook.py`:

```python
def test_merge_lid_into_pn_rekeys_messages_and_sets_canonical(self):
    from helpdesk.integrations.wa import _merge_lid_into_pn
    lid_jid = "99999000001@lid"
    pn_jid = "447900000001@s.whatsapp.net"

    # Create LID contact row with metadata
    if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
        frappe.get_doc({
            "doctype": "WA Contact",
            "jid": lid_jid,
            "phone": "",
            "custom_name": "Merge Test",
            "company": "",
            "assigned_team": "",
        }).insert(ignore_permissions=True)

    # Create a WA Message on the LID JID
    msg = frappe.get_doc({
        "doctype": "WA Message",
        "direction": "Incoming",
        "jid": lid_jid,
        "sender_jid": lid_jid,
        "message": "test merge",
        "content_type": "text",
        "message_id": "_test-merge-lid-001",
        "status": "Delivered",
        "line": "_test-evo",
        "is_read": 0,
    }).insert(ignore_permissions=True)
    frappe.db.commit()

    _merge_lid_into_pn(lid_jid, pn_jid)

    # LID row should have canonical_jid set
    canonical = frappe.db.get_value("WA Contact", {"jid": lid_jid}, "canonical_jid")
    self.assertEqual(canonical, pn_jid)

    # PN row should exist
    self.assertTrue(frappe.db.exists("WA Contact", {"jid": pn_jid}))

    # WA Message should now be keyed to PN JID
    new_jid = frappe.db.get_value("WA Message", msg.name, "jid")
    self.assertEqual(new_jid, pn_jid)

    # Cleanup
    frappe.db.delete("WA Message", {"message_id": "_test-merge-lid-001"})
    frappe.db.delete("WA Contact", {"jid": lid_jid})
    frappe.db.delete("WA Contact", {"jid": pn_jid})
    frappe.db.commit()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_merge_lid_into_pn_rekeys_messages_and_sets_canonical
```

Expected: FAIL — `ImportError: cannot import name '_merge_lid_into_pn'`

- [ ] **Step 3: Add `_merge_lid_into_pn` to `wa.py`**

Insert this function immediately after `_upsert_contact_name` (after line ~458):

```python
def _merge_lid_into_pn(lid_jid: str, pn_jid: str) -> None:
    """Merge a @lid alias row into the canonical @s.whatsapp.net row.

    - Ensures the PN row exists.
    - Copies non-empty metadata (custom_name, company, assigned_team) to PN if PN has blanks.
    - Re-keys WA Message.jid and HD Ticket.baileys_jid from lid_jid → pn_jid.
    - Sets canonical_jid on the LID row so future messages are re-routed.
    Idempotent: no-op if canonical_jid already set on the LID row.
    """
    if not lid_jid or not pn_jid:
        return
    existing_canonical = frappe.db.get_value("WA Contact", {"jid": lid_jid}, "canonical_jid")
    if existing_canonical:
        return  # already merged

    # Ensure PN row exists
    phone = _phone_from_jid(pn_jid)
    lid_row = frappe.db.get_value(
        "WA Contact", {"jid": lid_jid},
        ["custom_name", "company", "assigned_team"], as_dict=True,
    ) or {}
    _upsert_contact(pn_jid, phone, lid_row.get("custom_name") or "")

    # Copy metadata to PN row where PN has blanks
    pn_row = frappe.db.get_value(
        "WA Contact", {"jid": pn_jid},
        ["custom_name", "company", "assigned_team"], as_dict=True,
    ) or {}
    updates = {}
    for field in ("custom_name", "company", "assigned_team"):
        if not pn_row.get(field) and lid_row.get(field):
            updates[field] = lid_row[field]
    if updates:
        frappe.db.set_value("WA Contact", {"jid": pn_jid}, updates, update_modified=False)

    # Physically re-key WA Messages
    frappe.db.sql(
        "UPDATE `tabWA Message` SET jid = %s WHERE jid = %s",
        (pn_jid, lid_jid),
    )

    # Physically re-key HD Ticket.baileys_jid (field may not exist on all sites)
    try:
        frappe.db.sql(
            "UPDATE `tabHD Ticket` SET baileys_jid = %s WHERE baileys_jid = %s",
            (pn_jid, lid_jid),
        )
    except Exception:
        pass

    # Mark LID row as dead alias
    frappe.db.set_value("WA Contact", {"jid": lid_jid}, "canonical_jid", pn_jid, update_modified=False)
    frappe.db.commit()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_merge_lid_into_pn_rekeys_messages_and_sets_canonical
```

Expected: `OK (1 test)`

- [ ] **Step 5: Add idempotency test**

Add to `TestWaWebhook`:

```python
def test_merge_lid_into_pn_is_idempotent(self):
    from helpdesk.integrations.wa import _merge_lid_into_pn
    lid_jid = "99999000002@lid"
    pn_jid = "447900000002@s.whatsapp.net"

    if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
        frappe.get_doc({
            "doctype": "WA Contact",
            "jid": lid_jid,
            "phone": "",
            "custom_name": "Idempotent Test",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    _merge_lid_into_pn(lid_jid, pn_jid)
    _merge_lid_into_pn(lid_jid, pn_jid)  # second call must not raise

    canonical = frappe.db.get_value("WA Contact", {"jid": lid_jid}, "canonical_jid")
    self.assertEqual(canonical, pn_jid)

    # Cleanup
    frappe.db.delete("WA Contact", {"jid": lid_jid})
    frappe.db.delete("WA Contact", {"jid": pn_jid})
    frappe.db.commit()
```

- [ ] **Step 6: Run both tests**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_merge_lid_into_pn_rekeys_messages_and_sets_canonical
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_merge_lid_into_pn_is_idempotent
```

Expected: `OK (1 test)` for each.

- [ ] **Step 7: Commit**

```bash
git add helpdesk/integrations/wa.py helpdesk/integrations/tests/test_wa_webhook.py
git commit -m "feat(wa): add _merge_lid_into_pn — physical rekey of messages and tickets"
```

---

## Task 3: Trigger merge in `_upsert_contact` and `_handle_contacts_upsert`

**Files:**
- Modify: `helpdesk/integrations/wa.py` — `_upsert_contact` (~line 423), `_handle_contacts_upsert` (~line 626)

- [ ] **Step 1: Write the failing test**

Add to `TestWaWebhook`:

```python
def test_upsert_contact_triggers_merge_for_lid_with_phone(self):
    from helpdesk.integrations.wa import _upsert_contact
    lid_jid = "99999000003@lid"
    phone = "447900000003"
    pn_jid = f"{phone}@s.whatsapp.net"

    # Pre-create LID row
    if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
        frappe.get_doc({
            "doctype": "WA Contact",
            "jid": lid_jid,
            "phone": "",
            "custom_name": "Trigger Test",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    _upsert_contact(lid_jid, phone, "Trigger Test")

    canonical = frappe.db.get_value("WA Contact", {"jid": lid_jid}, "canonical_jid")
    self.assertEqual(canonical, pn_jid)

    # Cleanup
    frappe.db.delete("WA Contact", {"jid": lid_jid})
    frappe.db.delete("WA Contact", {"jid": pn_jid})
    frappe.db.commit()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_upsert_contact_triggers_merge_for_lid_with_phone
```

Expected: FAIL — canonical_jid is None (merge not triggered)

- [ ] **Step 3: Modify `_upsert_contact` to trigger merge**

Find `_upsert_contact` (~line 423) and replace the entire function body:

```python
def _upsert_contact(jid: str, phone: str, name: str) -> None:
    """Create-or-blank-fill a WA Contact row keyed by JID.
    If jid is a @lid and phone is known, triggers _merge_lid_into_pn immediately.
    """
    if not jid:
        return
    # If this is a LID JID and we know the phone, merge into the PN row
    if jid.endswith("@lid") and phone:
        pn_jid = f"{_normalize_phone(phone)}@s.whatsapp.net"
        _merge_lid_into_pn(jid, pn_jid)
        return
    try:
        if frappe.db.exists("WA Contact", {"jid": jid}):
            existing = frappe.db.get_value(
                "WA Contact", {"jid": jid}, ["custom_name", "phone"], as_dict=True
            ) or {}
            updates = {}
            if not existing.get("custom_name") and name:
                updates["custom_name"] = name
            if not existing.get("phone") and phone:
                updates["phone"] = phone
            if updates:
                frappe.db.set_value("WA Contact", {"jid": jid}, updates, update_modified=False)
        else:
            frappe.get_doc({
                "doctype": "WA Contact",
                "jid": jid,
                "phone": phone,
                "custom_name": name,
                "company": "",
                "assigned_team": "",
            }).insert(ignore_permissions=True)
    except Exception:
        pass
```

- [ ] **Step 4: Modify `_handle_contacts_upsert` to pass phone from payload**

Find `_handle_contacts_upsert` (~line 626) and replace:

```python
def _handle_contacts_upsert(contacts: list) -> None:
    for c in contacts:
        jid = c.get("id") or ""
        name = c.get("notify") or c.get("verifiedName") or c.get("name") or ""
        if not jid or not name:
            continue
        # For @lid JIDs, attempt to resolve phone from the payload fields
        if jid.endswith("@lid"):
            phone = _normalize_phone(c.get("phone") or "")
            if phone:
                _merge_lid_into_pn(jid, f"{phone}@s.whatsapp.net")
                continue
            # No phone in payload — create the LID row as-is; merge will happen
            # when upsert_contact_mapping delivers the confirmed LID↔PN pair
        phone = _phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else ""
        _upsert_contact(jid, phone, name)
```

- [ ] **Step 5: Run the test**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_upsert_contact_triggers_merge_for_lid_with_phone
```

Expected: `OK (1 test)`

- [ ] **Step 6: Run all wa_webhook tests to check for regressions**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add helpdesk/integrations/wa.py helpdesk/integrations/tests/test_wa_webhook.py
git commit -m "feat(wa): trigger LID→PN merge in _upsert_contact and _handle_contacts_upsert"
```

---

## Task 4: `upsert_contact_mapping` endpoint

This is the primary trigger — the gateway calls it on every message with a confirmed LID+phone pair.

**Files:**
- Modify: `helpdesk/integrations/wa.py` (add new whitelisted function after `_merge_lid_into_pn`)

- [ ] **Step 1: Write the failing test**

Add to `TestWaWebhook`:

```python
def test_upsert_contact_mapping_merges_lid(self):
    from helpdesk.integrations.wa import upsert_contact_mapping
    lid_jid = "99999000004@lid"
    phone = "447900000004"
    pn_jid = f"{phone}@s.whatsapp.net"

    if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
        frappe.get_doc({
            "doctype": "WA Contact",
            "jid": lid_jid,
            "phone": "",
            "custom_name": "",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    result = upsert_contact_mapping(lid=lid_jid, phone=phone, name="Gateway User")

    self.assertEqual(result.get("status"), "ok")
    canonical = frappe.db.get_value("WA Contact", {"jid": lid_jid}, "canonical_jid")
    self.assertEqual(canonical, pn_jid)

    # Cleanup
    frappe.db.delete("WA Contact", {"jid": lid_jid})
    frappe.db.delete("WA Contact", {"jid": pn_jid})
    frappe.db.commit()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_upsert_contact_mapping_merges_lid
```

Expected: FAIL — `ImportError: cannot import name 'upsert_contact_mapping'`

- [ ] **Step 3: Add `upsert_contact_mapping` to `wa.py`**

Add immediately after `_merge_lid_into_pn`:

```python
@frappe.whitelist(allow_guest=True)
def upsert_contact_mapping(lid: str = "", phone: str = "", name: str = "") -> dict:
    """Gateway calls this with a confirmed LID↔PN pair on every message.
    Validates the API key, then triggers the merge."""
    settings = _settings()
    if not settings.enabled:
        return {"status": "disabled"}

    api_key = frappe.request.headers.get("apikey") or frappe.request.headers.get("Authorization") or ""
    if api_key != (settings.global_api_key or ""):
        frappe.response["http_status_code"] = 401
        return {"error": "Unauthorized"}

    lid = (lid or "").strip()
    phone = _normalize_phone(phone or "")
    name = (name or "").strip()

    if not lid or not lid.endswith("@lid") or not phone:
        return {"status": "skipped", "reason": "missing lid or phone"}

    pn_jid = f"{phone}@s.whatsapp.net"
    _upsert_contact(pn_jid, phone, name)
    _merge_lid_into_pn(lid, pn_jid)
    return {"status": "ok", "lid": lid, "pn": pn_jid}
```

- [ ] **Step 4: Run the test**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_upsert_contact_mapping_merges_lid
```

Expected: `OK (1 test)`

- [ ] **Step 5: Commit**

```bash
git add helpdesk/integrations/wa.py helpdesk/integrations/tests/test_wa_webhook.py
git commit -m "feat(wa): add upsert_contact_mapping endpoint for gateway LID+phone pushes"
```

---

## Task 5: Webhook re-routing — remap LID JIDs at message entry

**Files:**
- Modify: `helpdesk/integrations/wa.py` — `_handle_upsert` (~line 513)

- [ ] **Step 1: Write the failing test**

Add to `TestWaWebhook`:

```python
def test_incoming_message_on_lid_is_rerouted_to_pn(self):
    from helpdesk.integrations.wa import _handle_upsert, _line, _settings
    line = _line("_test-evo")
    settings = _settings()
    lid_jid = "99999000005@lid"
    pn_jid = "447900000005@s.whatsapp.net"

    # Pre-create a merged LID alias row
    if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
        frappe.get_doc({
            "doctype": "WA Contact",
            "jid": lid_jid,
            "phone": "",
            "custom_name": "Reroute Test",
            "canonical_jid": pn_jid,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    frappe.set_user("Administrator")
    _handle_upsert({
        "key": {
            "remoteJid": lid_jid,
            "fromMe": False,
            "id": "_test-reroute-lid-001",
        },
        "pushName": "Reroute Test",
        "message": {"conversation": "hello reroute"},
    }, line, settings)

    # Message should be stored under the PN JID, not the LID
    msg = frappe.db.get_value(
        "WA Message", {"message_id": "_test-reroute-lid-001"}, ["jid", "name"], as_dict=True
    )
    self.assertIsNotNone(msg)
    self.assertEqual(msg["jid"], pn_jid)

    # Cleanup
    frappe.db.delete("WA Message", {"message_id": "_test-reroute-lid-001"})
    frappe.db.delete("WA Contact", {"jid": lid_jid})
    frappe.db.commit()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_incoming_message_on_lid_is_rerouted_to_pn
```

Expected: FAIL — message stored with `jid = lid_jid` not `pn_jid`

- [ ] **Step 3: Add LID re-routing at the top of `_handle_upsert`**

In `_handle_upsert`, after the broadcast skip check (after line ~524, before `_is_blocked`), add:

```python
    # Re-route LID JIDs to their canonical PN JID if already merged
    try:
        _canonical = frappe.db.get_value("WA Contact", {"jid": jid}, "canonical_jid")
        if _canonical:
            jid = _canonical
    except Exception:
        pass

    # Re-route sender LID to PN for group messages
    if sender and sender != jid:
        try:
            _sender_canonical = frappe.db.get_value("WA Contact", {"jid": sender}, "canonical_jid")
            if _sender_canonical:
                sender = _sender_canonical
        except Exception:
            pass
```

- [ ] **Step 4: Run the test**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_incoming_message_on_lid_is_rerouted_to_pn
```

Expected: `OK (1 test)`

- [ ] **Step 5: Run all wa_webhook tests**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add helpdesk/integrations/wa.py helpdesk/integrations/tests/test_wa_webhook.py
git commit -m "feat(wa): reroute incoming LID JIDs to canonical PN JID in _handle_upsert"
```

---

## Task 6: Query guards — filter aliases from search; redirect agent edits

**Files:**
- Modify: `helpdesk/integrations/wa.py` — `search_whatsapp_contacts` (~line 1728), `save_whatsapp_contact` (~line 1699)

- [ ] **Step 1: Write failing tests**

Add to `TestWaWebhook`:

```python
def test_search_contacts_excludes_lid_aliases(self):
    from helpdesk.integrations.wa import search_whatsapp_contacts
    lid_jid = "99999000006@lid"
    pn_jid = "447900000006@s.whatsapp.net"

    # Create a dead LID alias
    if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
        frappe.get_doc({
            "doctype": "WA Contact",
            "jid": lid_jid,
            "custom_name": "Alias Search Test",
            "canonical_jid": pn_jid,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    results = search_whatsapp_contacts(query="Alias Search Test")
    jids = [r["jid"] for r in results]
    self.assertNotIn(lid_jid, jids)

    # Cleanup
    frappe.db.delete("WA Contact", {"jid": lid_jid})
    frappe.db.commit()

def test_save_contact_on_lid_alias_redirects_to_pn(self):
    from helpdesk.integrations.wa import save_whatsapp_contact
    lid_jid = "99999000007@lid"
    pn_jid = "447900000007@s.whatsapp.net"

    if not frappe.db.exists("WA Contact", {"jid": lid_jid}):
        frappe.get_doc({
            "doctype": "WA Contact",
            "jid": lid_jid,
            "custom_name": "",
            "canonical_jid": pn_jid,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    result = save_whatsapp_contact(jid=lid_jid, custom_name="Redirected Name")

    # Result should report the PN JID, not the LID
    self.assertEqual(result["jid"], pn_jid)
    # PN row should have the name
    pn_name = frappe.db.get_value("WA Contact", {"jid": pn_jid}, "custom_name")
    self.assertEqual(pn_name, "Redirected Name")

    # Cleanup
    frappe.db.delete("WA Contact", {"jid": lid_jid})
    frappe.db.delete("WA Contact", {"jid": pn_jid})
    frappe.db.commit()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_search_contacts_excludes_lid_aliases
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_save_contact_on_lid_alias_redirects_to_pn
```

Expected: both FAIL.

- [ ] **Step 3: Update `search_whatsapp_contacts`**

In `search_whatsapp_contacts`, update the `if q:` SQL query from:
```sql
WHERE jid NOT LIKE '%%@broadcast'
  AND (custom_name LIKE %s OR phone LIKE %s OR company LIKE %s)
```
to:
```sql
WHERE jid NOT LIKE '%%@broadcast'
  AND (canonical_jid IS NULL OR canonical_jid = '')
  AND (custom_name LIKE %s OR phone LIKE %s OR company LIKE %s)
```

Update the `else:` branch from:
```python
wa_rows = frappe.get_all(
    "WA Contact",
    filters=[["jid", "not like", "%@broadcast"]],
    ...
)
```
to:
```python
wa_rows = frappe.get_all(
    "WA Contact",
    filters=[
        ["jid", "not like", "%@broadcast"],
        ["canonical_jid", "in", ["", None]],
    ],
    ...
)
```

- [ ] **Step 4: Update `save_whatsapp_contact`**

At the very top of `save_whatsapp_contact`, after the parameter normalization, add a redirect guard:

```python
    # Redirect writes on dead LID alias rows to the canonical PN row
    canonical = frappe.db.get_value("WA Contact", {"jid": jid}, "canonical_jid")
    if canonical:
        jid = canonical
```

- [ ] **Step 5: Run tests**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_search_contacts_excludes_lid_aliases
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook \
  --test TestWaWebhook.test_save_contact_on_lid_alias_redirects_to_pn
```

Expected: both `OK (1 test)`.

- [ ] **Step 6: Commit**

```bash
git add helpdesk/integrations/wa.py helpdesk/integrations/tests/test_wa_webhook.py
git commit -m "feat(wa): exclude LID aliases from contact search; redirect agent edits to canonical"
```

---

## Task 7: Background sync — `enqueue_wa_sync` + bulk upsert in `sync_wa_contacts`

**Files:**
- Modify: `helpdesk/integrations/wa.py` — `sync_wa_contacts` (~line 2117), add `enqueue_wa_sync` + `_run_wa_sync_job`

- [ ] **Step 1: Add `enqueue_wa_sync` and `_run_wa_sync_job` to `wa.py`**

Add immediately before `sync_wa_contacts`:

```python
@frappe.whitelist()
def enqueue_wa_sync() -> dict:
    """Queue a background job that runs sync_wa_contacts then sync_wa_groups."""
    frappe.enqueue(
        "helpdesk.integrations.wa._run_wa_sync_job",
        queue="long",
        timeout=300,
        now=False,
    )
    return {"status": "queued"}


def _run_wa_sync_job() -> None:
    """Background job: sync contacts then groups, emit realtime event when done."""
    try:
        contact_result = sync_wa_contacts()
        group_result = sync_wa_groups()
        frappe.publish_realtime(
            "helpdesk:wa-sync-complete",
            message={
                "contacts": contact_result.get("total", 0),
                "groups": group_result.get("total", 0),
            },
        )
    except Exception as exc:
        frappe.log_error(str(exc), "WA Sync Job Failed")
        frappe.publish_realtime(
            "helpdesk:wa-sync-complete",
            message={"error": str(exc)},
        )
```

- [ ] **Step 2: Rewrite `sync_wa_contacts` with bulk upsert**

Replace the entire `sync_wa_contacts` function body with:

```python
@frappe.whitelist()
def sync_wa_contacts() -> dict:
    """Upsert WA Contacts from local WA Message sender history using a single bulk SQL."""
    rows = frappe.db.sql(
        """
        SELECT sender_jid,
               MAX(sender_name)   AS sender_name,
               MAX(profile_name)  AS profile_name
        FROM `tabWA Message`
        WHERE direction = 'Incoming'
          AND sender_jid IS NOT NULL AND sender_jid != ''
          AND sender_jid NOT LIKE '%%@broadcast'
          AND sender_jid NOT LIKE '%%@g.us'
        GROUP BY sender_jid
        """,
        as_dict=True,
    )
    if not rows:
        return {"created": 0, "updated": 0, "total": 0}

    values = []
    for row in rows:
        jid = row.sender_jid
        name = row.sender_name or row.profile_name or ""
        phone = _phone_from_jid(jid) if jid.endswith("@s.whatsapp.net") else ""
        doc_name = frappe.generate_hash(length=10)
        values.append((doc_name, jid, phone, name))

    # Bulk INSERT — skip rows with duplicate jid (unique constraint); only fill blanks on UPDATE
    frappe.db.sql(
        """
        INSERT INTO `tabWA Contact` (name, jid, phone, custom_name, company, assigned_team)
        VALUES (%s, %s, %s, %s, '', '')
        ON DUPLICATE KEY UPDATE
            custom_name = IF(custom_name IS NULL OR custom_name = '', VALUES(custom_name), custom_name),
            phone       = IF(phone IS NULL OR phone = '', VALUES(phone), phone)
        """,
        values,
        as_list=True,
    )
    frappe.db.commit()
    return {"created": len(values), "updated": 0, "total": len(values)}
```

- [ ] **Step 3: Run the full test suite for wa_webhook**

```bash
cd /home/frappeuser/bench16
bench --site site16.local run-tests --app helpdesk \
  --module helpdesk.integrations.tests.test_wa_webhook
```

Expected: all pass.

- [ ] **Step 4: Manually verify enqueue works**

```bash
cd /home/frappeuser/bench16
bench --site site16.local execute helpdesk.integrations.wa.enqueue_wa_sync
```

Expected: `{'status': 'queued'}` printed with no error.

- [ ] **Step 5: Commit**

```bash
git add helpdesk/integrations/wa.py
git commit -m "feat(wa): background sync via enqueue_wa_sync + bulk upsert in sync_wa_contacts"
```

---

## Task 8: `wipe_wa_contacts` endpoint + WA API Settings button

**Files:**
- Modify: `helpdesk/integrations/wa.py` (add `wipe_wa_contacts`)
- Modify: `helpdesk/helpdesk/doctype/wa_api_settings/wa_api_settings.js`

- [ ] **Step 1: Add `wipe_wa_contacts` to `wa.py`**

Add after `enqueue_wa_sync`:

```python
@frappe.whitelist()
def wipe_wa_contacts() -> dict:
    """Delete all WA Contact rows. Used before a clean resync to remove LID/PN duplicates."""
    if frappe.session.user == "Guest":
        frappe.throw("Not permitted")
    count = frappe.db.count("WA Contact")
    frappe.db.sql("DELETE FROM `tabWA Contact`")
    frappe.db.commit()
    return {"deleted": count}
```

- [ ] **Step 2: Add a button to `wa_api_settings.js`**

Replace the entire contents of `wa_api_settings.js`:

```javascript
frappe.ui.form.on("WA API Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Wipe WA Contacts"), function () {
			frappe.confirm(
				__("Delete ALL WA Contact rows? Do this before running a fresh contact sync to remove LID/PN duplicates. This cannot be undone."),
				function () {
					frappe.call({
						method: "helpdesk.integrations.wa.wipe_wa_contacts",
						callback(r) {
							if (r.message) {
								frappe.msgprint(__("Deleted {0} contacts.", [r.message.deleted]));
							}
						},
					});
				}
			);
		}, __("Tools"));
	},
});
```

- [ ] **Step 3: Restart to pick up Python change**

```bash
cd /home/frappeuser/bench16
bench restart
```

- [ ] **Step 4: Verify button appears**

Open `http://site16.local:8002/app/wa-api-settings` in the browser. The "Tools" menu in the form toolbar should show "Wipe WA Contacts". Do not click it yet.

- [ ] **Step 5: Commit**

```bash
git add helpdesk/integrations/wa.py \
        helpdesk/helpdesk/doctype/wa_api_settings/wa_api_settings.js
git commit -m "feat(wa): add wipe_wa_contacts endpoint and Tools button in WA API Settings"
```

---

## Task 9: Frontend — async sync button in `BaileysConversationList.vue`

**Files:**
- Modify: `desk/src/components/whatsapp/BaileysConversationList.vue`

- [ ] **Step 1: Replace the sync logic in `BaileysConversationList.vue`**

Find the `syncContactsResource`, `syncGroupsResource`, and `syncContacts` function (roughly lines 173–203). Replace the entire block with:

```typescript
const syncResource = createResource({
  url: "helpdesk.integrations.wa.enqueue_wa_sync",
  auto: false,
  onSuccess() {
    // Job is queued; spinner stays until we hear helpdesk:wa-sync-complete
  },
  onError(e: any) {
    syncingContacts.value = false;
    toast.error(e?.messages?.[0] || "Sync failed to queue");
  },
});

function syncContacts() {
  if (syncingContacts.value) return;
  syncingContacts.value = true;
  syncResource.submit({});
}
```

- [ ] **Step 2: Add the realtime listener**

Find `onMounted` (or the script setup area where `watch` calls live) and add, after the existing watches:

```typescript
import { onMounted, onBeforeUnmount } from "vue";
import { globalStore } from "@/stores/globalStore";

onMounted(() => {
  const { $socket } = globalStore();
  $socket.on("helpdesk:wa-sync-complete", onSyncComplete);
});

onBeforeUnmount(() => {
  const { $socket } = globalStore();
  $socket.off("helpdesk:wa-sync-complete", onSyncComplete);
});

function onSyncComplete(data: { contacts?: number; groups?: number; error?: string }) {
  syncingContacts.value = false;
  if (data?.error) {
    toast.error(`Sync failed: ${data.error}`);
  } else {
    toast.success(`Synced ${data?.contacts ?? 0} contact(s) and ${data?.groups ?? 0} group(s)`);
  }
  conversations.reload();
}
```

- [ ] **Step 3: Remove the now-unused `_contactTotal` reference**

Delete the old `syncGroupsResource` `onSuccess` callback that referenced `syncContactsResource.data` — it no longer exists. Verify the file has no dangling references to `syncContactsResource` or `syncGroupsResource`.

- [ ] **Step 4: Build frontend assets**

```bash
cd /home/frappeuser/bench16
bench build --app helpdesk
```

Expected: build completes with no TypeScript errors.

- [ ] **Step 5: Test the sync button in browser**

Open `http://site16.local:8002` → WhatsApp section → click the sync icon (⟳). The spinner should appear immediately (not block for 90 s). After the job completes, a toast should appear with the contact/group counts and the conversation list should reload.

- [ ] **Step 6: Commit**

```bash
git add desk/src/components/whatsapp/BaileysConversationList.vue
git commit -m "feat(wa): async sync button — enqueue job, listen for wa-sync-complete event"
```

---

## Task 10: Migration — wipe and resync

This is a one-time operator action, not a code task. Document the steps here for reference.

- [ ] **Step 1: Wipe existing contacts**

Open WA API Settings → Tools → "Wipe WA Contacts" → confirm.

- [ ] **Step 2: Trigger fresh sync**

In the WhatsApp sidebar, click the sync icon (⟳). The background job will run `sync_wa_contacts` + `sync_wa_groups`. When the toast appears, the contacts are repopulated with the new merge logic in place.

- [ ] **Step 3: Verify no duplicates**

Open `http://site16.local:8002/app/wa-contact`. Sort by JID. Confirm there are no `@lid` rows with an empty `canonical_jid` alongside a matching `@s.whatsapp.net` row.

---

## Self-Review

**Spec coverage check:**
- Section 1 (schema) → Task 1 ✓
- Section 2 (_merge_lid_into_pn) → Task 2 ✓; triggers → Task 3 + 4 ✓
- Section 3 (webhook re-routing) → Task 5 ✓
- Section 4 (query changes — search, save) → Task 6 ✓; get_wa_conversations → no change needed (covered in spec) ✓
- Section 5 (background sync, bulk upsert) → Task 7 + 9 ✓
- Section 6 (wipe + migration) → Task 8 + 10 ✓

**All tasks produce tested, committable increments. No placeholders.**
