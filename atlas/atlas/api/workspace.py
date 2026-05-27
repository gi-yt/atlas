"""Whitelisted helpers used by the Atlas workspace bootstrap checklist.

The workspace HTML block calls `bootstrap_status()` once on render to learn
which of the four onboarding steps are satisfied. Each step turns green as
soon as at least one record of the corresponding doctype exists.
"""

import frappe


@frappe.whitelist()
def bootstrap_status() -> dict[str, int]:
	"""Return the document count for each bootstrap step.

	The keys mirror the wireframe in `ux/solutions/01-workspace-solution.md`.
	"""
	return {
		"providers": frappe.db.count("Server Provider"),
		"servers": frappe.db.count("Server"),
		"images": frappe.db.count("Virtual Machine Image"),
		"virtual_machines": frappe.db.count("Virtual Machine"),
	}
