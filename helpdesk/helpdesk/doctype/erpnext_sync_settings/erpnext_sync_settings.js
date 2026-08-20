// Test Connection lives here rather than in the Vue desk app: these settings
// are admin configuration reached at /app, and a form script is the platform's
// own answer. Building a settings page in Vue for one button would be work
// without a payoff.
frappe.ui.form.on("ERPNext Sync Settings", {
	sync_now(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Save the settings first, then sync."));
			return;
		}
		frappe.call({
			method: "helpdesk.integrations.erpnext_sync.enqueue_customer_sync",
			callback() {
				// Queued, not finished. Saying "synced" here would be a lie the
				// first time somebody syncs a few thousand customers.
				frappe.show_alert({
					message: __("Customer sync queued. Last Customer Sync updates when it finishes."),
					indicator: "blue",
				});
			},
		});
	},

	sync_entitlements_now(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Save the settings first, then sync."));
			return;
		}
		frappe.call({
			method: "helpdesk.integrations.erpnext_sync.enqueue_entitlement_sync",
			callback() {
				frappe.show_alert({
					message: __(
						"Entitlement sync queued. Last Entitlement Sync updates when it finishes."
					),
					indicator: "blue",
				});
			},
		});
	},

	test_connection(frm) {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Save the settings first, then test."));
			return;
		}
		frappe.dom.freeze(__("Contacting ERPNext..."));
		frappe.call({
			method: "helpdesk.integrations.erpnext_remote.test_connection",
			callback(r) {
				frappe.dom.unfreeze();
				const res = r.message || {};
				if (res.ok) {
					const d = res.data || {};
					frappe.msgprint({
						title: __("Connected"),
						indicator: "green",
						// Report what came back, not just "it worked" — a green
						// tick that returned zero customers is worth noticing.
						message: __("Reached ERPNext and read {0} customer(s). Endpoint version {1}.", [
							d.sample_count ?? 0,
							d.version ?? "?",
						]),
					});
				} else {
					frappe.msgprint({
						title: __("Could not connect"),
						indicator: "red",
						message: frappe.utils.escape_html(res.error || __("Unknown error")),
					});
				}
			},
			error() {
				frappe.dom.unfreeze();
			},
		});
	},
});
