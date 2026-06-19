frappe.ui.form.on("Virtual Machine Snapshot", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Available") {
			return;
		}
		frappe.atlas.add_primary(frm, "Clone to new VM", () => open_clone_dialog(frm));
		add_restore_button(frm);
		add_promote_button(frm);
		frappe.atlas.add_danger(frm, "Delete", () => confirm_delete(frm));
	},
});

function add_promote_button(frm) {
	// Promote turns this snapshot into a first-class base image new VMs select via
	// the ordinary `image` field. A WARM snapshot can't be promoted — its value is
	// the frozen memory pair, which a cold-booting base image discards — so paint a
	// live button only for a cold snapshot and explain the refusal otherwise (the
	// same "don't show a button you'll refuse" rule the Restore button follows).
	if (frm.doc.kind === "Warm") {
		frappe.atlas.add_action(frm, __("Promote to image (cold snapshots only)"), () =>
			frappe.msgprint({
				title: __("Warm snapshot"),
				message: __(
					"A warm snapshot's value is the frozen memory clones resume. Promoting it would discard that — promote a cold snapshot, or use Clone to new VM here."
				),
				indicator: "orange",
			})
		);
		return;
	}
	frappe.atlas.add_secondary(frm, "Promote to image", () => open_promote_dialog(frm));
}

function open_promote_dialog(frm) {
	const default_name = (frm.doc.title || "")
		.toLowerCase()
		.replace(/[^a-z0-9.-]+/g, "-")
		.replace(/^-+|-+$/g, "");
	const dialog = new frappe.ui.Dialog({
		title: __("Promote {0} to a base image", [frm.doc.title]),
		fields: [
			{
				fieldname: "image_name",
				label: __("Image name"),
				fieldtype: "Data",
				reqd: 1,
				default: default_name,
				description: __(
					"Lowercase letters, digits, dots and dashes. Becomes the image record name and the on-host LV (atlas-image-<name>)."
				),
			},
			{
				fieldname: "title",
				label: __("Title"),
				fieldtype: "Data",
				default: frm.doc.title,
			},
			{
				fieldname: "hint",
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__(
					"Copies the snapshot's disk into a read-only base image on this server. New VMs on this server can then pick it as their image. It stays on this server (no fleet sync)."
				)}</p>`,
			},
		],
		primary_action_label: __("Promote"),
		primary_action(values) {
			dialog.hide();
			frm.call("promote_to_image", values).then(({ message: image_name }) => {
				frappe.show_alert(
					{ message: __("Promoted to image {0}.", [image_name]), indicator: "green" },
					6
				);
				frappe.set_route("Form", "Virtual Machine Image", image_name);
			});
		},
	});
	dialog.show();
}

function add_restore_button(frm) {
	// Restore overwrites the source VM's disk via rebuild(), which only runs on
	// a Stopped VM. Paint a live button only when that's true — the same
	// "don't show a button you'll refuse" rule the VM disk actions follow — and
	// surface the current status in the Actions menu when it isn't yet eligible.
	// The status read is async, so capture the doc this lookup is for and bail
	// if the operator navigated to another snapshot before it resolved (stale
	// callback → wrong form's buttons).
	const snapshot_name = frm.doc.name;
	frappe.db
		.get_value("Virtual Machine", frm.doc.virtual_machine, "status")
		.then(({ message }) => {
			if (frm.doc.name !== snapshot_name) return;
			const vm_status = message && message.status;
			if (vm_status === "Stopped") {
				frappe.atlas.add_secondary(frm, "Restore to VM", () => confirm_restore(frm));
			} else {
				frappe.atlas.add_action(
					frm,
					__("Restore to VM (needs Stopped VM, now {0})", [vm_status || "?"]),
					() =>
						frappe.msgprint({
							title: __("VM is not Stopped"),
							message: __(
								"Stop {0} first, then Restore overwrites its disk with this snapshot.",
								[frm.doc.virtual_machine]
							),
							indicator: "orange",
						})
				);
			}
		});
}

function open_clone_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Clone {0} to a new VM", [frm.doc.title]),
		fields: [
			{ fieldname: "title", label: __("New VM title"), fieldtype: "Data", reqd: 1 },
			{
				fieldname: "ssh_public_key",
				label: __("SSH Public Key"),
				fieldtype: "Long Text",
				reqd: 1,
				description: __(
					"The clone gets fresh host keys, IP and machine-id; this is the login key."
				),
			},
			{
				fieldname: "cost_hint",
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__(
					"Creates and auto-provisions a brand-new VM seeded from this snapshot — a billable workload, ready in ~90 s."
				)}</p>`,
			},
		],
		primary_action_label: __("Create clone"),
		primary_action(values) {
			dialog.hide();
			frm.call("clone_to_new_vm", values).then(({ message: vm_name }) => {
				frappe.show_alert(
					{
						message: __("Clone created; provisioning."),
						indicator: "blue",
					},
					6
				);
				frappe.set_route("Form", "Virtual Machine", vm_name);
			});
		},
	});
	dialog.show();
}

function confirm_restore(frm) {
	frappe.atlas.confirm_cost({
		title: __("Restore {0} onto {1}?", [frm.doc.title, frm.doc.virtual_machine]),
		body_html: `<p>${__(
			"Overwrites the VM's current disk with this snapshot — current data is lost. Takes up to a few minutes; the VM stays Stopped."
		)}</p>`,
		proceed_label: __("Restore"),
		proceed() {
			frm.call("restore_to_vm").then(({ message: task_name }) =>
				frappe.atlas.task_started(frm, "Restore", task_name)
			);
		},
	});
}

function confirm_delete(frm) {
	frappe.atlas.confirm_destructive({
		title: __("Delete snapshot {0}?", [frm.doc.title]),
		body_html: __("<p>The on-host snapshot files are deleted. This cannot be undone.</p>"),
		match_string: frm.doc.title,
		match_label: __("Type the snapshot title to confirm"),
		proceed_label: __("Delete"),
		proceed() {
			frappe.db.delete_doc("Virtual Machine Snapshot", frm.doc.name).then(() => {
				frappe.show_alert({ message: __("Snapshot deleted."), indicator: "green" });
				frappe.set_route("List", "Virtual Machine Snapshot");
			});
		},
	});
}
