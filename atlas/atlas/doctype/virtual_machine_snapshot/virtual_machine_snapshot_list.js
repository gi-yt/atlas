// Status pills come from the DocType `states` array (Pending orange,
// Available green, Failed red) — no client `get_indicator` needed.
frappe.listview_settings["Virtual Machine Snapshot"] = {
	add_fields: ["status"],
};
