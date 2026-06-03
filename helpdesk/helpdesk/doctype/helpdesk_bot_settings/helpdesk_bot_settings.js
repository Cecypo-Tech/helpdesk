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

		frm.add_custom_button(__("Test Chat"), () => {
			const history = [];

			const dialog = new frappe.ui.Dialog({
				title: __("Test Bot Chat"),
				size: "large",
			});

			// Build chat UI inside dialog body
			const $body = dialog.$wrapper.find(".modal-body");
			$body.css({ padding: "0", display: "flex", "flex-direction": "column", height: "480px" });

			const $messages = $(`<div>`)
				.css({
					flex: "1",
					"overflow-y": "auto",
					padding: "16px",
					display: "flex",
					"flex-direction": "column",
					gap: "8px",
					background: "var(--fg-color)",
				})
				.appendTo($body);

			const $footer = $(`<div>`)
				.css({
					display: "flex",
					gap: "8px",
					padding: "12px 16px",
					"border-top": "1px solid var(--border-color)",
					background: "var(--fg-color)",
				})
				.appendTo($body);

			const $input = $(`<input type="text" class="form-control" placeholder="${__("Type a message…")}">`)
				.css({ flex: "1" })
				.appendTo($footer);

			const $send = $(`<button class="btn btn-primary btn-sm">${__("Send")}</button>`)
				.appendTo($footer);

			function addBubble(text, role, meta) {
				const isUser = role === "user";
				const $wrap = $(`<div>`).css({ display: "flex", "justify-content": isUser ? "flex-end" : "flex-start" });
				const $bubble = $(`<div>`)
					.css({
						"max-width": "72%",
						padding: "8px 12px",
						"border-radius": "12px",
						background: isUser ? "var(--primary)" : "var(--control-bg)",
						color: isUser ? "#fff" : "var(--text-color)",
						"font-size": "13px",
						"white-space": "pre-wrap",
						"word-break": "break-word",
					})
					.text(text);
				$wrap.append($bubble);
				if (meta) {
					const $meta = $(`<div>`).css({ "font-size": "11px", color: "var(--text-muted)", "margin-top": "2px", "text-align": isUser ? "right" : "left" }).text(meta);
					const $outer = $(`<div>`).css({ display: "flex", "flex-direction": "column", "align-items": isUser ? "flex-end" : "flex-start", "max-width": "72%" });
					$outer.append($bubble).append($meta);
					$wrap.empty().append($outer);
				}
				$messages.append($wrap);
				$messages.scrollTop($messages[0].scrollHeight);
			}

			function send() {
				const msg = $input.val().trim();
				if (!msg) return;
				$input.val("").prop("disabled", true);
				$send.prop("disabled", true).text(__("…"));
				addBubble(msg, "user");
				const priorHistory = JSON.stringify(history);
				history.push({ role: "user", content: msg });

				frappe.call({
					method: "helpdesk.helpdesk.doctype.helpdesk_bot_settings.helpdesk_bot_settings.test_chat",
					args: { message: msg, history: priorHistory },
					callback(r) {
						const { ok, reply, articles } = r.message || {};
						if (ok) {
							const meta = articles && articles.length
								? __("KB: {0}", [articles.map(a => a.title).join(", ")])
								: __("No KB articles matched");
							addBubble(reply, "bot", meta);
							history.push({ role: "assistant", content: reply });
						} else {
							addBubble(`Error: ${reply}`, "bot");
						}
					},
					always() {
						$input.prop("disabled", false).focus();
						$send.prop("disabled", false).text(__("Send"));
					},
				});
			}

			$send.on("click", send);
			$input.on("keydown", (e) => { if (e.key === "Enter") send(); });

			dialog.show();
			setTimeout(() => $input.focus(), 100);
		});
	},
});
