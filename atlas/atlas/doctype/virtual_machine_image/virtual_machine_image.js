frappe.ui.form.on("Virtual Machine Image", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		if (frm.doc.is_active) {
			frappe.atlas.add_danger(frm, "Archive", () => confirm_archive(frm));
		}
	},
});

function confirm_archive(frm) {
	frappe.atlas.confirm_archive(frm, {
		match: frm.doc.title || frm.doc.image_name,
		match_label: __("Type the image title to confirm"),
		alert_message: __("Image archived."),
	});
}
