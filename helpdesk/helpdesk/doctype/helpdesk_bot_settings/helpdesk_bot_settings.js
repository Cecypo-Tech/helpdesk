frappe.ui.form.on("Helpdesk Bot Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test API Key"), () => {
			frappe.show_alert({ message: __("Testing connection…"), indicator: "blue" });
			frappe.call({
				method: "helpdesk.helpdesk.doctype.helpdesk_bot_settings.helpdesk_bot_settings.test_connection",
				callback(r) {
					const { ok, message } = r.message || {};
					frappe.show_alert({
						message: message || __("Unknown response"),
						indicator: ok ? "green" : "red",
					});
				},
			});
		});
	},
});
