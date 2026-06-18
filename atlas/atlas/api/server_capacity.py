"""Whitelisted helper used by the Virtual Machine creation form.

Returns "what does this Server have, and how much of it is already spoken for?"
so the operator can see oversubscription before clicking Provision. vCPU totals
come from a small static dict keyed by DigitalOcean size slug — same maintenance
model as `Provider Size.monthly_cost_usd`.

Capacity is deliberately oversubscribable: a VM's `cpu_max_cores` is a cgroup
cpu.max bandwidth cap, not a pinned core, so a host can safely back more vCPUs
than it physically has. The fleet-wide multiplier is `Atlas Settings.overprovision_factor`
(default 1 — no oversubscription until the operator raises it). A size we don't
recognize has *no* known total, so we report unlimited capacity and let
placement put a VM there — the operator vouched for the host by marking it
Active (self-managed hosts have no slug at all).
"""

import frappe

# vCPUs per DigitalOcean size slug. Hand-maintained; missing slugs report
# unlimited capacity from `capacity_for_server` and the client falls back to a
# "—" total.
DIGITALOCEAN_VCPUS_BY_SIZE: dict[str, int] = {
	"s-1vcpu-1gb": 1,
	"s-1vcpu-2gb": 1,
	"s-2vcpu-2gb": 2,
	"s-2vcpu-4gb-intel": 2,
	"s-2vcpu-4gb": 2,
	"s-4vcpu-8gb": 4,
	"s-8vcpu-16gb-intel": 8,
	"s-8vcpu-16gb": 8,
	"c-2": 2,
	"c-4": 4,
}


def overprovision_factor() -> float:
	"""Fleet-wide vCPU oversubscription multiplier from Atlas Settings.

	Default 1 (no oversubscription) when unset. A host's effective vCPU budget
	is its physical total times this factor."""
	value = frappe.db.get_single_value("Atlas Settings", "overprovision_factor")
	return float(value) if value else 1.0


@frappe.whitelist()
def capacity_for_server(server: str) -> dict:
	"""Return total vs. used vCPUs and VM count for a Server.

	`total_vcpus` is the host's physical vCPU count; `effective_vcpus` is that
	times `Atlas Settings.overprovision_factor` — the budget placement actually
	checks against. Both are None when the Server's size slug isn't in the
	static dict (self-managed hosts have no slug), which the client renders as
	"—" and placement treats as unlimited. `used` sums the CPU bandwidth cap
	(`cpu_max_cores`, falling back to `vcpus`) of non-Terminated VMs — the true
	cost, so fractional-vCPU VMs don't each spend a whole vCPU of budget.
	"""
	size = frappe.db.get_value("Server", server, "size")
	# Server.size is now a Link to Provider Size, stored as "{type}/{slug}".
	# Strip the prefix before looking up vCPUs in the legacy slug-keyed dict.
	slug = size.split("/", 1)[1] if size and "/" in size else size
	total = DIGITALOCEAN_VCPUS_BY_SIZE.get(slug) if slug else None
	effective = total * overprovision_factor() if total is not None else None
	used_rows = frappe.get_all(
		"Virtual Machine",
		filters={"server": server, "status": ["!=", "Terminated"]},
		fields=["vcpus", "cpu_max_cores"],
	)
	# Sum the true CPU *bandwidth* cost (cpu_max_cores), not the guest thread
	# count (vcpus): seven 1/16-vCPU VMs cost ~0.44 vCPU of budget, not 7. Older
	# rows with no cpu_max_cores fall back to vcpus (whole-core behavior).
	used = sum(float(row.cpu_max_cores or row.vcpus or 0) for row in used_rows)
	return {
		"server": server,
		"size": size,
		"total_vcpus": total,
		"effective_vcpus": effective,
		"used_vcpus": used,
		"virtual_machine_count": len(used_rows),
	}


@frappe.whitelist()
def cluster_capacity() -> dict:
	"""Aggregate `capacity_for_server` across every Active Server.

	The fleet-wide view behind the per-server one: "how much room does the whole
	cluster have, regardless of which host a VM lands on?" — the same question
	placement asks (atlas/placement.py), summed instead of walked one server at a
	time.

	`total_vcpus`/`effective_vcpus` sum only the servers with a *known* total;
	`uncatalogued_servers` counts the rest (self-managed hosts, or sizes not in
	the static dict) whose effective budget is None and which placement treats as
	unlimited — so the totals are a floor, not a ceiling. `used_vcpus` and
	`virtual_machine_count` sum across all Active servers regardless. `servers`
	carries the per-server breakdown for a drill-down.
	"""
	names = frappe.get_all(
		"Server",
		filters={"status": "Active"},
		pluck="name",
		order_by="creation asc",
	)
	servers = [capacity_for_server(name) for name in names]
	catalogued = [s for s in servers if s["effective_vcpus"] is not None]
	return {
		"server_count": len(servers),
		"uncatalogued_servers": len(servers) - len(catalogued),
		"total_vcpus": sum(s["total_vcpus"] for s in catalogued),
		"effective_vcpus": sum(s["effective_vcpus"] for s in catalogued),
		"used_vcpus": sum(s["used_vcpus"] for s in servers),
		"virtual_machine_count": sum(s["virtual_machine_count"] for s in servers),
		"servers": servers,
	}
