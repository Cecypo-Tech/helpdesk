frappe.ui.form.on("Baileys Gateway Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Check Connection"), () => {
			frappe.call({
				method: "helpdesk.integrations.baileys.get_gateway_status",
				callback(r) {
					const d = r.message || {};
					if (d.connected) {
						frappe.show_alert({ message: __("Connected — session: {0}", [d.session || "?"]), indicator: "green" });
					} else if (d.hasQr) {
						frappe.show_alert({ message: __("Not connected — QR code is waiting to be scanned"), indicator: "orange" });
					} else {
						frappe.show_alert({ message: __("Disconnected: {0}", [d.error || "unknown"]), indicator: "red" });
					}
				},
			});
		});

		frm.add_custom_button(__("Fetch Groups from WhatsApp"), () => {
			frappe.call({
				method: "helpdesk.integrations.baileys.fetch_gateway_groups",
				callback(r) {
					const groups = r.message || [];
					if (!groups.length) {
						frappe.show_alert({ message: __("No groups found on this account"), indicator: "orange" });
						return;
					}
					const existing = (frm.doc.group_jids || []).map((row) => row.jid);
					let added = 0;
					groups.forEach((g) => {
						if (!existing.includes(g.jid)) {
							const row = frm.add_child("group_jids");
							row.jid = g.jid;
							row.group_name = g.subject;
							added++;
						}
					});
					frm.refresh_field("group_jids");
					frappe.show_alert({
						message: added
							? __("{0} new group(s) added ({1} total on account)", [added, groups.length])
							: __("All {0} groups already in the list", [groups.length]),
						indicator: "green",
					});
				},
			});
		}, __("WhatsApp"));
	},
});
