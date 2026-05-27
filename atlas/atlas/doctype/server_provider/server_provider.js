frappe.ui.form.on("Server Provider", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		if (frm.doc.provider_type === "DigitalOcean") {
			frm.add_custom_button("Test Connection", () => {
				frm.call("test_connection").then(({message}) => {
					frappe.show_alert({
						message: `OK: ${message.email}`,
						indicator: "green",
					});
				});
			});
		}

		frm.add_custom_button("Provision Server", () => {
			const is_self_managed = frm.doc.provider_type === "Self-Managed";
			const fields = [
				{
					fieldname: "server_name",
					label: "Server Name",
					fieldtype: "Data",
					reqd: 1,
				},
			];
			if (is_self_managed) {
				fields.push(
					{
						fieldname: "ipv4_address",
						label: "IPv4 Address",
						fieldtype: "Data",
						reqd: 1,
						description: "Public IPv4 Atlas will SSH to.",
					},
					{
						fieldname: "ipv6_address",
						label: "IPv6 Address",
						fieldtype: "Data",
						reqd: 1,
						description: "The host's own IPv6.",
					},
					{
						fieldname: "ipv6_prefix",
						label: "IPv6 Prefix",
						fieldtype: "Data",
						reqd: 1,
						description: "Full prefix routed to the host, e.g. 2a03:b0c0:abcd:1234::/64.",
					},
					{
						fieldname: "ipv6_virtual_machine_range",
						label: "IPv6 Virtual Machine Range",
						fieldtype: "Data",
						reqd: 1,
						description: "Subnet Atlas allocates VM addresses from. Any prefix length.",
					},
				);
			}
			const dialog = new frappe.ui.Dialog({
				title: "Provision Server",
				fields: fields,
				primary_action_label: "Provision",
				primary_action(values) {
					frm.call("provision_server", values).then(({message}) => {
						dialog.hide();
						frappe.show_alert({
							message: `Provisioning ${message}; watch the Task list.`,
							indicator: "blue",
						});
						frappe.set_route("Form", "Server", message);
					});
				},
			});
			dialog.show();
		});
	},
});
