// Status pills come from the DocType `states` array — no client
// `get_indicator` needed.
frappe.listview_settings["Server"] = {
	add_fields: ["status"],
};
