frappe.listview_settings["Task"] = {
	add_fields: ["status", "script"],

	// `get_indicator` no longer needed — DocType `states` array paints the
	// Status column's pill (Pending/Running/Success/Failure) automatically.

	formatters: {
		subject(value, _df, doc) {
			return value || doc.script || doc.name;
		},
	},
};
