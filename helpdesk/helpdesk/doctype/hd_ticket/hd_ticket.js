// Copyright (c) 2023, Frappe Technologies and contributors
// For license information, please see license.txt

frappe.ui.form.on("HD Ticket", {
  onload(frm) {
    if (frm.is_new()) return;
    frm.call("mark_seen");
  },
  refresh(frm) {
    frappe.call({
      method: "helpdesk.integrations.whatsapp.get_product_options",
      callback(r) {
        if (!r.message || !r.message.length) return;
        const options = ["", ...r.message];
        frappe.meta.get_docfield("HD Ticket", "product", frm.doc.name).options = options.join("\n");
        frm.refresh_field("product");
      },
    });
  },
});
