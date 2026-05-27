frappe.atlas = frappe.atlas || {};

frappe.atlas.add_primary = function (frm, label, fn) {
	frm.add_custom_button(__(label), fn);
	frm.change_custom_button_type(__(label), null, "primary");
};

frappe.atlas.add_secondary = function (frm, label, fn) {
	frm.add_custom_button(__(label), fn);
};

frappe.atlas.add_action = function (frm, label, fn) {
	frm.add_custom_button(__(label), fn, __("Actions"));
};

frappe.atlas.add_danger = function (frm, label, fn) {
	frm.add_custom_button(__(label), fn, __("Actions"));
	frm.change_custom_button_type(__(label), __("Actions"), "danger");
};

frappe.atlas.confirm_cost = function ({title, body_html, proceed_label, proceed}) {
	return frappe.warn(title, body_html, proceed, proceed_label, true);
};

frappe.atlas.confirm_destructive = function ({
	title,
	body_html,
	match_string,
	match_label,
	proceed_label,
	proceed,
}) {
	const dialog = new frappe.ui.Dialog({
		title: title,
		fields: [
			{fieldname: "body", fieldtype: "HTML", options: body_html},
			{
				fieldname: "confirmation",
				label: match_label || __("Type to confirm"),
				fieldtype: "Data",
				reqd: 1,
				description: __("Type {0} to enable the button below.", [`<b>${frappe.utils.escape_html(match_string)}</b>`]),
			},
		],
		primary_action_label: proceed_label || __("Proceed"),
		primary_action() {
			if (dialog.get_value("confirmation") !== match_string) {
				frappe.show_alert({
					message: __("Confirmation text does not match."),
					indicator: "orange",
				});
				return;
			}
			dialog.hide();
			proceed();
		},
	});
	dialog.show();
	const button = dialog.get_primary_btn();
	button.addClass("btn-danger").prop("disabled", true);
	dialog.fields_dict.confirmation.$input.on("input", () => {
		const matches = dialog.get_value("confirmation") === match_string;
		button.prop("disabled", !matches);
	});
	return dialog;
};

frappe.atlas.task_started = function (frm, label, task_name) {
	frappe.show_alert({
		message: __("{0} Task: {1}", [
			label,
			`<a href="/app/task/${encodeURIComponent(task_name)}">${frappe.utils.escape_html(task_name)}</a>`,
		]),
		indicator: "blue",
	}, 6);
	frappe.set_route("Form", "Task", task_name);
};

frappe.atlas.short_id = function (name) {
	return (name || "").slice(0, 8);
};

frappe.atlas.strip_desk_chrome = function (frm) {
	if (frm.page && frm.page.sidebar) {
		frm.page.sidebar.hide();
	}
	const $body = frm.page && frm.page.wrapper;
	if ($body && $body.find) {
		$body.find(".layout-main-section-wrapper").removeClass("col-lg-8").addClass("col-lg-12");
		$body.find(".form-timeline, .new-timeline, .timeline, .comment-input-container").hide();
	}
};

for (const doctype of [
	"Server",
	"Server Provider",
	"Virtual Machine",
	"Virtual Machine Image",
	"Task",
]) {
	frappe.ui.form.on(doctype, {
		onload(frm) {
			frappe.atlas.strip_desk_chrome(frm);
		},
		refresh(frm) {
			frappe.atlas.strip_desk_chrome(frm);
		},
	});
}

