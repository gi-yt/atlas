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
	frappe.realtime.on("task_update", (data) => {
		if (!data || data.name !== frm.doc.name) return;
		frm.reload_doc();
	});
}
