frappe.ui.form.on("WA Line", {
	refresh(frm) {
		if (!frm.is_new()) {
			frappe.db.count("WA Message", { line: frm.doc.name }).then((count) => {
				if (count > 0) {
					frm.set_intro(
						__(
							"This line has {0} WhatsApp message(s). Deleting this line will also permanently delete all linked messages.",
							[count]
						),
						"orange"
					);
				}
			});

			frm.add_custom_button(__("Configure Webhook"), () => {
				frappe.call({
					method: "helpdesk.integrations.wa.configure_wa_webhook",
					args: { line: frm.doc.name },
					freeze: true,
					freeze_message: __("Registering webhook…"),
					callback(r) {
						if (r.exc) return;
						frappe.msgprint({
							title: __("Webhook Configured"),
							message: __("WhatsApp API will now send events to:<br><code>{0}</code>", [r.message.webhook_url]),
							indicator: "green",
						});
					},
				});
			}, __("WhatsApp"));

			frm.add_custom_button(__("Show QR Code"), () => {
				frappe.call({
					method: "helpdesk.integrations.wa.get_wa_qr",
					args: { line: frm.doc.name },
					freeze: true,
					freeze_message: __("Fetching QR code…"),
					callback(r) {
						if (r.exc) return;
						const { base64, code } = r.message;
						let body = "";
						if (base64) {
							body = `<div style="text-align:center;padding:16px">
								<img src="${base64}" style="max-width:280px;border:1px solid #ddd;border-radius:6px" />
								<p style="margin-top:12px;font-size:12px;color:#666">
									Scan with WhatsApp → Linked Devices → Link a Device
								</p>
							</div>`;
						} else if (code) {
							body = `<div style="text-align:center;padding:24px">
								<div style="font-size:28px;font-weight:700;letter-spacing:4px;font-family:monospace">${code}</div>
								<p style="margin-top:12px;font-size:12px;color:#666">
									Enter this pairing code in WhatsApp → Linked Devices → Link with phone number
								</p>
							</div>`;
						} else {
							body = `<p style="padding:16px;color:#888">
								No QR code returned. The instance may already be connected or not yet initialised.
							</p>`;
						}
						frappe.msgprint({ title: __("WhatsApp QR Code — {0}", [frm.doc.instance_name]), message: body, indicator: "green" });
					},
				});
			}, __("WhatsApp"));

			frm.add_custom_button(__("Sync Old Messages"), () => {
				frappe.prompt(
					{
						fieldtype: "Int",
						label: __("Messages per chat"),
						fieldname: "limit",
						default: 50,
						description: __("How many recent messages to fetch per chat (max 500)"),
					},
					(values) => {
						frappe.call({
							method: "helpdesk.integrations.wa.sync_wa_old_messages",
							args: { line: frm.doc.name, limit_per_chat: values.limit || 50 },
							freeze: true,
							freeze_message: __("Fetching message history from WhatsApp API…"),
							callback(r) {
								if (r.exc) return;
								const { imported, skipped } = r.message;
								frappe.msgprint({
									title: __("Sync Complete"),
									message: __("Imported {0} new messages. {1} already existed.", [imported, skipped]),
									indicator: "green",
								});
							},
						});
					},
					__("Sync Old Messages"),
					__("Sync"),
				);
			}, __("WhatsApp"));
		}
	},
});
