import frappe
from frappe.model.document import Document

# The routing key is the identity (autoname field:subdomain) and the target VM
# is fixed once chosen — repointing a live subdomain at a different VM is a
# delete-and-recreate, not an in-place edit, so the proxy map change is explicit.
IMMUTABLE_AFTER_INSERT = (
	"subdomain",
	"virtual_machine",
	"region",
)


class Subdomain(Document):
	def validate(self) -> None:
		self._validate_immutability()
		self._denormalize_address()

	def _validate_immutability(self) -> None:
		"""Lock the routing key, its target VM, and its region once written. The
		`address` is the one mutable field (it tracks the VM's ipv6), and `active`
		toggles the mapping in/out of the served map."""
		if self.is_new():
			return
		original = self.get_doc_before_save()
		if not original:
			return
		for field in IMMUTABLE_AFTER_INSERT:
			if getattr(original, field) != getattr(self, field):
				frappe.throw(f"{field} is immutable after insert")

	def _denormalize_address(self) -> None:
		"""Copy the target VM's public IPv6 onto `address`, so the desired-map
		query (map_for_region) is a single SELECT with no join. The proxy dials
		this literal; it never resolves a VM. A VM with no ipv6 yet is a hard
		error — an unaddressable target can't be a routing destination."""
		address = frappe.db.get_value("Virtual Machine", self.virtual_machine, "ipv6_address")
		if not address:
			frappe.throw(
				f"Virtual Machine {self.virtual_machine} has no ipv6_address; cannot map a subdomain to it"
			)
		self.address = address


def map_for_region(region: str) -> dict[str, str]:
	"""The desired subdomain→address map for a region: every ACTIVE subdomain in
	the region. This is the full map every proxy VM in the region serves (the
	design's "each proxy holds the whole regional map", proxy-design.md §7.1).

	The proxy reconcile (atlas.atlas.proxy) compares this, serialized canonically,
	against each proxy guest's live `/map` and bulk-`/sync`s on drift."""
	rows = frappe.get_all(
		"Subdomain",
		filters={"region": region, "active": 1},
		fields=["subdomain", "address"],
	)
	return {row["subdomain"]: row["address"] for row in rows}
