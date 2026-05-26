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
