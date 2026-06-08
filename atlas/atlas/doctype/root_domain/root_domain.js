frappe.ui.form.on("Root Domain", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frappe.atlas.add_primary(frm, "Issue / Renew Certificate", () => confirm_issue(frm));
	},
});

function confirm_issue(frm) {
	frappe.confirm(
		__(
			"Issue (or renew) {0} and push it to every proxy in {1}? This contacts the ACME server and writes DNS records.",
			[
				`<b>*.${frappe.utils.escape_html(frm.doc.domain)}</b>`,
				`<b>${frappe.utils.escape_html(frm.doc.region)}</b>`,
			]
		),
		() => {
			frappe.show_alert({ message: __("Issuing certificate…"), indicator: "blue" });
			frm.call("issue_certificate").then(({ message: cert_name }) => {
				frappe.show_alert({
					message: __("Certificate issued; pushed to the region's proxies."),
					indicator: "green",
				});
				if (cert_name) {
					frappe.set_route("Form", "TLS Certificate", cert_name);
				}
			});
		}
	);
}
