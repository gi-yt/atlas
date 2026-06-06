from frappe import _


def get_data():
	return {
		"fieldname": "server",
		"transactions": [
			{"label": _("Operations"), "items": ["Virtual Machine", "Task"]},
			{"label": _("Networking"), "items": ["Reserved IP"]},
		],
	}
