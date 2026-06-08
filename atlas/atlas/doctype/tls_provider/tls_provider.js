frappe.ui.form.on("TLS Provider", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frappe.atlas.add_action(frm, "Test Connection", () => run_test_connection(frm));
		if (frm.doc.is_active) {
			frappe.atlas.add_danger(frm, "Archive", () => confirm_archive(frm));
		}
	},
});

function run_test_connection(frm) {
	frappe.show_alert({ message: __("Testing connection…"), indicator: "blue" });
	frm.call("authenticate").then(({ message }) => {
		if (message.ok) {
			const label = message.account_label || frm.doc.provider_name;
			frappe.show_alert({ message: __("OK: {0}", [label]), indicator: "green" });
		} else {
			frappe.show_alert({
				message: __("Failed: {0}", [message.error || __("unknown error")]),
				indicator: "red",
			});
		}
	});
}

function confirm_archive(frm) {
	frappe.atlas.confirm_archive(frm, {
		match: frm.doc.provider_name,
		match_label: __("Type the provider name to confirm"),
		alert_message: __("TLS Provider archived."),
	});
}
