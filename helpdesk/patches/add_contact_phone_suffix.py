import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

# Must stay in step with _phone_suffix() in helpdesk/integrations/wa.py: digits
# only, trailing 9, whole value when shorter. RIGHT() already returns the whole
# string when it is shorter than the requested length.
SUFFIX_DIGITS = 9

FIELDS = {
	"Contact Phone": {
		"fieldname": "phone_suffix",
		"label": "Phone Suffix",
		"fieldtype": "Data",
		"read_only": 1,
		"no_copy": 1,
		"hidden": 1,
		# Frappe owns the index via search_index. A hand-rolled ALTER here was
		# silently undone later in the same migrate, when fixture sync rebuilt
		# these core doctypes and dropped an index nothing declared — the patch
		# logged as successful with no index to show for it.
		"search_index": 1,
		"insert_after": "is_primary_mobile_no",
		"module": "Helpdesk",
	},
	"Contact": {
		"fieldname": "phone_suffix",
		"label": "Phone Suffix",
		"fieldtype": "Data",
		"read_only": 1,
		"no_copy": 1,
		"hidden": 1,
		# Frappe owns the index via search_index. A hand-rolled ALTER here was
		# silently undone later in the same migrate, when fixture sync rebuilt
		# these core doctypes and dropped an index nothing declared — the patch
		# logged as successful with no index to show for it.
		"search_index": 1,
		"insert_after": "phone",
		"module": "Helpdesk",
	},
}

def execute():
	"""Store and index the phone → contact lookup key.

	match_phone_to_contact() runs on every inbound WhatsApp message and used to
	load every Contact with a number plus every Contact Phone row into Python,
	then loop over both twice. Measured on a 486-contact site: 1.6ms for a hit,
	4.9ms for a miss, growing linearly with contact count.

	The field goes on both tables. Contact Phone is the canonical store —
	Contact.validate() derives mobile_no/phone from it — but a legacy row with a
	number and no child row would silently stop matching if only the child table
	were indexed, and a contact that stops matching starts spawning duplicates.
	The legacy-shape count is logged below so we learn whether that shape exists
	in the wild.

	Creates the Custom Fields itself rather than waiting for the fixture: frappe
	runs patches in run_schema_updates() but syncs fixtures in
	post_schema_updates(), afterwards. On a first migrate the columns would not
	exist yet, the backfill would skip, and — patches running once — no later
	migrate would fill them in.

	The index itself is declared on the field as search_index, not added here.
	A hand-rolled ALTER was silently undone later in the same migrate when
	fixture sync rebuilt these core doctypes; letting frappe own the index makes
	it self-healing. That only works because this index is single-column.

	Re-runnable: backfills only empty rows.
	"""
	for doctype, definition in FIELDS.items():
		if not frappe.db.has_column(doctype, "phone_suffix"):
			create_custom_field(doctype, definition)
	frappe.db.commit()

	frappe.db.sql(
		f"""
		UPDATE `tabContact Phone`
		SET phone_suffix = RIGHT(REGEXP_REPLACE(phone, '[^0-9]', ''), {SUFFIX_DIGITS})
		WHERE IFNULL(phone_suffix, '') = '' AND IFNULL(phone, '') != ''
		"""
	)
	frappe.db.sql(
		f"""
		UPDATE `tabContact`
		SET phone_suffix = RIGHT(
			REGEXP_REPLACE(IFNULL(NULLIF(mobile_no, ''), phone), '[^0-9]', ''), {SUFFIX_DIGITS}
		)
		WHERE IFNULL(phone_suffix, '') = ''
		  AND (IFNULL(mobile_no, '') != '' OR IFNULL(phone, '') != '')
		"""
	)
	frappe.db.commit()

	legacy = frappe.db.sql(
		"""
		SELECT COUNT(*) FROM `tabContact` c
		WHERE (IFNULL(c.phone, '') != '' OR IFNULL(c.mobile_no, '') != '')
		  AND NOT EXISTS (
			SELECT 1 FROM `tabContact Phone` p WHERE p.parent = c.name
		  )
		"""
	)[0][0]
	if legacy:
		print(
			f"  {legacy} Contact(s) carry a number with no Contact Phone row;"
			" these match via the Contact.phone_suffix index."
		)
