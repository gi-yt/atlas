// Image Build — the operator's view of a bake run. A live checklist of the four
// lifecycle steps, derived from `status`, refreshed by the `image_build_progress`
// realtime event the controller pushes on every transition (with the form's own
// reload as the fallback). The one action is Re-bake (retry an Available/Failed
// build). See spec/15-image-builder.md.

// The four real phases, in order, each mapped to the status at which it is RUNNING.
// Provisioning and Building each own one; Snapshotting covers stop+snapshot+register.
const STEPS = [
	{ key: "provision", label: "Provision build VM", running: "Provisioning" },
	{ key: "build", label: "Build inside guest", running: "Building" },
	{ key: "snapshot", label: "Stop + snapshot", running: "Snapshotting" },
];

const STATUS_ORDER = ["Draft", "Provisioning", "Building", "Snapshotting", "Available", "Failed"];

frappe.ui.form.on("Image Build", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		add_buttons(frm);
		render_checklist(frm);
		subscribe_to_realtime(frm);
	},
});

function add_buttons(frm) {
	if (["Available", "Failed"].includes(frm.doc.status)) {
		frappe.atlas.add_action(frm, "Re-bake", () => confirm_rebake(frm));
	}
}

function confirm_rebake(frm) {
	frappe.confirm(
		__("Re-bake {0}? This re-runs the whole pipeline (idempotent).", [frm.doc.name]),
		() => {
			frm.call("rebake").then(() => {
				frappe.show_alert({ message: __("Re-bake started."), indicator: "blue" });
				frm.reload_doc();
			});
		}
	);
}

// Map the row's status to a done/running/pending/failed state per step, then draw
// the checklist as the form intro. The single source of truth for "where are we"
// is `status`; the step view is derived from it (mirrors site_status.steps_for).
function render_checklist(frm) {
	frm.set_intro("");
	const status = frm.doc.status;
	if (status === "Draft") {
		frm.set_intro(__("Queued — the bake starts when a worker picks it up."), "blue");
		return;
	}

	const reached = STATUS_ORDER.indexOf(status);
	const lines = STEPS.map((step) => {
		const running_at = STATUS_ORDER.indexOf(step.running);
		let icon;
		if (status === "Failed" && reached === running_at) {
			icon = "✖";
		} else if (reached > running_at || status === "Available") {
			icon = "✔";
		} else if (reached === running_at) {
			icon = "⟳";
		} else {
			icon = "·";
		}
		return `${icon}  ${__(step.label)}`;
	});

	let color = "blue";
	let header = __("Baking {0} …", [frm.doc.title]);
	if (status === "Available") {
		color = "green";
		header = __("{0} baked — snapshot is the artifact.", [frm.doc.title]);
	} else if (status === "Failed") {
		color = "red";
		header = __("Bake failed — see the error below / the Build Task.");
	}
	frm.set_intro(`${header}\n\n${lines.join("\n")}`, color);
}

function subscribe_to_realtime(frm) {
	if (frm._atlas_image_build_realtime_registered) return;
	frm._atlas_image_build_realtime_registered = true;
	frappe.realtime.on("image_build_progress", (data) => {
		if (!data || data.name !== frm.doc.name) return;
		frm.reload_doc();
	});
}
