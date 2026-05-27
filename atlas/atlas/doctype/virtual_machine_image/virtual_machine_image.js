const LOCKED_AFTER_SYNC = [
	"kernel_url",
	"kernel_filename",
	"kernel_sha256",
	"rootfs_url",
	"rootfs_filename",
	"rootfs_sha256",
];


frappe.ui.form.on("Virtual Machine Image", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frappe.atlas.add_secondary(frm, "Sync to Server", () => open_sync_to_server_dialog(frm));
		frappe.atlas.add_action(frm, "Sync to All Servers", () => confirm_sync_to_all(frm));
		render_sync_status_panel(frm);
		enforce_lock_state(frm);
	},
});


function render_sync_status_panel(frm) {
	const field = frm.fields_dict.sync_status_html;
	if (!field || !field.$wrapper) return;
	field.$wrapper.html(`<div class="text-muted small">${__("Loading sync status…")}</div>`);
	frm.call("sync_status").then(({message: rows}) => {
		if (!rows || !rows.length) {
			field.$wrapper.html(
				`<div class="text-muted small">${__("No active servers.")}</div>`,
			);
			return;
		}
		const list_html = rows.map((row) => {
			const server = frappe.utils.escape_html(row.server);
			const region = row.region ? `<span class="text-muted small">${frappe.utils.escape_html(row.region)}</span>` : "";
			let right_cell;
			if (row.task) {
				// `comment_when` already returns an HTML <span> — do not escape
				// it again or you see the literal markup.
				const when_html = frappe.datetime.comment_when(row.synced_at);
				right_cell = `
					${when_html}
					<a href="/app/task/${encodeURIComponent(row.task)}" class="ml-2 small">${frappe.utils.escape_html(row.task.slice(0, 10))} →</a>
				`;
			} else {
				right_cell = `
					<span class="text-muted small">${__("never")}</span>
					<a href="#" class="ml-2 small atlas-sync-now" data-server="${server}">${__("Sync now")} →</a>
				`;
			}
			return `<tr>
				<td><a href="/app/server/${encodeURIComponent(row.server)}">${server}</a> ${region}</td>
				<td class="text-right">${right_cell}</td>
			</tr>`;
		}).join("");
		field.$wrapper.html(`
			<table class="table table-sm atlas-sync-status">
				<thead>
					<tr>
						<th>${__("Server")}</th>
						<th class="text-right">${__("Last sync")}</th>
					</tr>
				</thead>
				<tbody>${list_html}</tbody>
			</table>
		`);
		field.$wrapper.off("click.atlas-sync-now").on("click.atlas-sync-now", ".atlas-sync-now", (event) => {
			event.preventDefault();
			const server = event.currentTarget.dataset.server;
			if (!server) return;
			open_sync_to_server_dialog(frm, server);
		});
	});
}


function enforce_lock_state(frm) {
	// Server-side validate() also blocks the change; the client read-only
	// flag is just an early hint so the operator doesn't get a save-time
	// error after editing four fields.
	frappe.db.exists("Task", {
		script: "sync-image.sh",
		status: "Success",
		variables: ["like", `%"IMAGE_NAME": "${frm.doc.name}"%`],
	}).then((exists) => {
		if (!exists) return;
		for (const fieldname of LOCKED_AFTER_SYNC) {
			frm.set_df_property(fieldname, "read_only", 1);
		}
		frm.set_intro(
			__("This image has been synced. To change kernel or rootfs, create a new image (e.g. {0}-v2). Editing here would invalidate prior audit rows.", [frm.doc.name]),
			"blue",
		);
	});
}


function open_sync_to_server_dialog(frm, prefilled_server) {
	const dialog = new frappe.ui.Dialog({
		title: __("Sync to Server"),
		fields: [
			{
				fieldname: "server_name",
				label: __("Server"),
				fieldtype: "Link",
				options: "Server",
				only_select: 1,
				reqd: 1,
				default: prefilled_server || "",
				get_query: () => ({filters: {status: "Active"}}),
			},
			{
				fieldname: "hint",
				fieldtype: "HTML",
				options: `<div class="text-muted small">${__("Each download takes a few minutes per server depending on image size.")}</div>`,
			},
		],
		primary_action_label: __("Sync"),
		primary_action(values) {
			frm.call("sync_to_server", {server_name: values.server_name})
				.then(({message: task_name}) => {
					dialog.hide();
					frappe.atlas.task_started(frm, "Sync image", task_name);
				});
		},
	});
	dialog.show();
}


function confirm_sync_to_all(frm) {
	frappe.db.get_list("Server", {
		fields: ["name", "region", "status"],
		filters: {status: "Active"},
		order_by: "name asc",
		limit: 100,
	}).then((servers) => {
		if (!servers.length) {
			frappe.show_alert({
				message: __("No active servers to sync to."),
				indicator: "orange",
			});
			return;
		}
		const target_rows = servers.map((server) => `
			<li><b>${frappe.utils.escape_html(server.name)}</b>
				<span class="text-muted">${frappe.utils.escape_html(server.region || "")} · ${frappe.utils.escape_html(server.status)}</span>
			</li>
		`).join("");
		const body = `
			<p>${__("Image: {0}", [`<b>${frappe.utils.escape_html(frm.doc.image_name || frm.doc.name)}</b>`])}</p>
			<p>${__("Targets:")}</p>
			<ul class="list-unstyled" style="padding-left: 1em">${target_rows}</ul>
			<p class="text-muted small">${__("Each download fetches kernel + rootfs over the public internet, verifies SHA-256, and runs sync-image.sh.")}</p>
		`;
		frappe.atlas.confirm_cost({
			title: __("Sync to {0} active server(s)?", [servers.length]),
			body_html: body,
			proceed_label: __("Sync to All"),
			proceed() {
				frm.call("sync_to_all_servers").then(({message}) => {
					frappe.show_alert({
						message: __("Enqueued {0} sync Task(s).", [message.length]),
						indicator: "blue",
					});
				});
			},
		});
	});
}
