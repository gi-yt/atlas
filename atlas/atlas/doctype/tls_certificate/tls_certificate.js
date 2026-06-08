frappe.ui.form.on("TLS Certificate", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frappe.atlas.add_primary(frm, "Issue/Renew", () => confirm_renew(frm));
		if (frm.doc.fullchain_path && frm.doc.privkey_path) {
			frappe.atlas.add_action(frm, "Push to Proxies", () => run_push(frm));
		}
	},
});

function confirm_renew(frm) {
	frappe.confirm(
		__("Re-issue {0} via the ACME server and push it to the region's proxies?", [
			`<b>${frappe.utils.escape_html(frm.doc.common_name)}</b>`,
		]),
		() => {
			frappe.show_alert({ message: __("Renewing certificate…"), indicator: "blue" });
			frm.call("renew").then(() => {
				frappe.show_alert({
					message: __("Certificate renewed and pushed."),
					indicator: "green",
				});
				frm.reload_doc();
			});
		}
	);
}

function run_push(frm) {
	frappe.show_alert({ message: __("Pushing to proxies…"), indicator: "blue" });
	frm.call("push_to_proxies").then(({ message: pushed }) => {
		const count = (pushed || []).length;
		frappe.show_alert({
			message: __("Pushed to {0} proxy VM(s).", [count]),
			indicator: count ? "green" : "orange",
		});
	});
}
