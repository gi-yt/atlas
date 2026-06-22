frappe.ui.form.on("Reserved IP", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		add_buttons(frm);
	},
});

function add_buttons(frm) {
	if (frm.doc.status === "Allocated") {
		// Allocated = on the Server, no VM. Bind it to a VM, or destroy it.
		frappe.atlas.add_primary(frm, "Attach", () => open_attach_dialog(frm));
		frappe.atlas.add_danger(frm, "Release", () => confirm_release(frm));
	} else if (frm.doc.status === "Attached") {
		// Attached = bound to one VM. Detach returns it to the Server pool.
		frappe.atlas.add_danger(frm, "Detach", () => confirm_detach(frm));
	}
}

function open_attach_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Attach {0}", [frm.doc.ip_address]),
		fields: [
			{
				fieldname: "virtual_machine",
				label: __("Virtual Machine"),
				fieldtype: "Link",
				options: "Virtual Machine",
				reqd: 1,
				only_select: 1,
				// One IP, one VM, same Server: scope the picker to this IP's
				// Server and to VMs that don't already carry a public IPv4.
				get_query: () => ({
					filters: { server: frm.doc.server, public_ipv4: ["is", "not set"] },
				}),
				description: __("Only VMs on {0} without a public IPv4 are eligible.", [
					frm.doc.server,
				]),
			},
			{
				fieldname: "hint",
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__(
					"Binds the address to the VM (Frappe-side). The host 1:1-NAT wiring is a separate step."
				)}</p>`,
			},
		],
		primary_action_label: __("Attach"),
		primary_action({ virtual_machine }) {
			dialog.hide();
			frm.call("attach", { virtual_machine }).then(() => {
				frappe.show_alert({
					message: __("{0} attached to {1}.", [frm.doc.ip_address, virtual_machine]),
					indicator: "green",
				});
				frm.reload_doc();
			});
		},
	});
	dialog.show();
}

function confirm_detach(frm) {
	frappe.confirm(
		__("Detach {0} from {1}?", [frm.doc.ip_address, frm.doc.virtual_machine]),
		() => {
			frm.call("detach").then(() => {
				frappe.show_alert({
					message: __("{0} returned to the {1} pool.", [
						frm.doc.ip_address,
						frm.doc.server,
					]),
					indicator: "green",
				});
				frm.reload_doc();
			});
		}
	);
}

function confirm_release(frm) {
	frappe.atlas.confirm_destructive({
		title: __("Release {0}?", [frm.doc.ip_address]),
		body_html: `<p>${__(
			"Destroys the reserved IP at the provider and deletes this record. This cannot be undone."
		)}</p>`,
		match_string: frm.doc.ip_address,
		match_label: __("Type the IP address to confirm"),
		proceed_label: __("Release"),
		proceed() {
			frm.call("release").then(() => {
				frappe.show_alert({
					message: __("Released {0}.", [frm.doc.ip_address]),
					indicator: "green",
				});
				frappe.set_route("List", "Reserved IP");
			});
		},
	});
}
