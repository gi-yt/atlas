frappe.ui.form.on("Task", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frm.disable_save();
		add_retry_button(frm);
		pretty_print_variables(frm);
		subscribe_to_realtime(frm);
	},
});

function add_retry_button(frm) {
	if (frm.doc.status !== "Failure") return;
	frappe.atlas.add_primary(frm, "Retry", () => {
		frappe.confirm(__("Retry this Task?"), () => {
			frm.call("retry").then(({ message: task_name }) => {
				frappe.atlas.task_started(frm, "Retry", task_name);
			});
		});
	});
}

function pretty_print_variables(frm) {
	const raw = frm.doc.variables;
	if (!raw || frm._atlas_variables_prettified === frm.doc.name) return;
	let parsed;
	try {
		parsed = JSON.parse(raw);
	} catch (e) {
		return;
	}
	const pretty = JSON.stringify(parsed, null, 2);
	if (pretty === raw) {
		frm._atlas_variables_prettified = frm.doc.name;
		return;
	}
	frm.doc.variables = pretty;
	frm.refresh_field("variables");
	frm._atlas_variables_prettified = frm.doc.name;
}

function subscribe_to_realtime(frm) {
	if (frm._atlas_realtime_registered) return;
	frm._atlas_realtime_registered = true;
	// Lifecycle transitions (Pending->Running->Success/Failure) reload the whole
	// doc so the status pill, stdout/stderr and timing repaint.
	frappe.realtime.on("task_update", (data) => {
		if (!data || data.name !== frm.doc.name) return;
		frm.reload_doc();
	});
	// High-frequency streamed log updates (spec/22): repaint the Live Output panel
	// in place rather than reload the whole doc each poll, so a 10-20 min bake tails
	// smoothly. The server sends the whole (bounded) buffer, so we REPLACE — a late
	// join or a dropped event can't drift out of sync with the server's window.
	frappe.realtime.on("task_log", (data) => {
		if (!data || data.name !== frm.doc.name) return;
		if (data.progress_line !== undefined) {
			frm.doc.progress_line = data.progress_line;
			frm.refresh_field("progress_line");
		}
		if (data.live_output !== undefined) {
			frm.doc.live_output = data.live_output;
			frm.refresh_field("live_output");
		}
	});
}
