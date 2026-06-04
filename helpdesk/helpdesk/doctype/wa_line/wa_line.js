frappe.ui.form.on("WA Line", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Delete Line"), async () => {
				const count = await frappe.db.count("WA Message", { line: frm.doc.name });
				let msg = __("Are you sure you want to permanently delete this line?");
				if (count > 0) {
					msg +=
						"<br><br><span style='color:var(--orange-600)'>" +
						__(
							"This line has {0} WhatsApp message(s). All linked messages will also be permanently deleted.",
							[count]
						) +
						"</span>";
				}
				frappe.confirm(msg, () => {
					frappe.call({
						method: "frappe.client.delete",
						args: { doctype: "WA Line", name: frm.doc.name },
						callback() {
							frappe.set_route("List", "WA Line");
						},
					});
				});
			}, __("WhatsApp"));

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

			frm.add_custom_button(__("Test Connection"), () => {
				function showResult(r) {
					const { connected, evo_state, profile, live_error, checked_at, error } = r;

					let indicator, stateLabel, detail;
					if (error) {
						indicator = "red";
						stateLabel = __("Unreachable");
						detail = `<p style="color:var(--red-600)">${frappe.utils.escape_html(error)}</p>`;
					} else if (connected) {
						indicator = "green";
						stateLabel = __("Connected");
						const name = (profile && (profile.name || profile.pushname)) ? frappe.utils.escape_html(profile.name || profile.pushname) : "";
						detail = `<p style="color:var(--green-600)">✓ ${__("Evolution reports <b>{0}</b> and WhatsApp confirmed the session is live.", [evo_state])}${name ? ` (${name})` : ""}</p>`;
					} else if (evo_state === "open") {
						indicator = "orange";
						stateLabel = __("Stale — needs reconnect");
						detail = `<p style="color:var(--orange-600)">⚠ ${__("Evolution reports <b>open</b> but WhatsApp did not respond to the live check.")}</p>`
							+ (live_error ? `<p style="font-size:12px;color:#888">${frappe.utils.escape_html(live_error)}</p>` : "");
					} else {
						indicator = "red";
						stateLabel = __("Disconnected");
						detail = `<p style="color:var(--red-600)">✗ ${__("Evolution state: <b>{0}</b>. WhatsApp session is not active.", [evo_state || "unknown"])}</p>`;
					}

					const d = new frappe.ui.Dialog({
						title: __("Connection Test — {0}", [frm.doc.instance_name]),
						indicator,
						fields: [
							{
								fieldtype: "HTML",
								options: `<div style="padding:8px 0">
									${detail}
									<p style="font-size:12px;color:#888;margin-top:8px">${__("Checked at {0}", [checked_at || ""])}</p>
								</div>`,
							},
						],
						primary_action_label: __("Reconnect"),
						primary_action() {
							d.hide();
							frappe.show_alert({ message: __("Restarting instance…"), indicator: "blue" }, 4);
							frappe.call({
								method: "helpdesk.integrations.wa.reconnect_wa_line",
								args: { line: frm.doc.name },
								freeze: true,
								freeze_message: __("Restarting…"),
								callback(rv) {
									if (rv.exc || !rv.message?.ok) {
										frappe.msgprint({
											title: __("Reconnect Failed"),
											message: rv.message?.error || __("Unknown error"),
											indicator: "red",
										});
										return;
									}
									frappe.show_alert({ message: __("Restarted. Re-testing in 5 seconds…"), indicator: "blue" }, 6);
									setTimeout(() => {
										frappe.call({
											method: "helpdesk.integrations.wa.test_wa_connection",
											args: { line: frm.doc.name },
											freeze: true,
											freeze_message: __("Testing…"),
											callback(rv2) {
												if (rv2.exc) return;
												showResult(rv2.message);
											},
										});
									}, 5000);
								},
							});
						},
					});
					if (connected) d.get_primary_btn().hide();
					d.show();
				}

				frappe.call({
					method: "helpdesk.integrations.wa.test_wa_connection",
					args: { line: frm.doc.name },
					freeze: true,
					freeze_message: __("Testing connection…"),
					callback(r) {
						if (r.exc) return;
						showResult(r.message);
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
							callback(r) {
								if (r.exc) return;
								if (r.message?.status === "locked") {
									frappe.show_alert({ message: __("A sync for this line is already in progress. Try again in a few minutes."), indicator: "orange" }, 6);
									return;
								}
								frappe.show_alert({ message: __("Message sync started in the background."), indicator: "blue" }, 5);
								frappe.realtime.on("helpdesk:wa-old-sync-complete", function handler(data) {
									if (data.line !== frm.doc.name) return;
									frappe.realtime.off("helpdesk:wa-old-sync-complete", handler);
									if (data.error) {
										frappe.msgprint({ title: __("Sync Failed"), message: data.error, indicator: "red" });
									} else {
										frappe.msgprint({
											title: __("Sync Complete"),
											message: __("Imported {0} new messages. {1} already existed.", [data.imported, data.skipped]),
											indicator: "green",
										});
									}
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
