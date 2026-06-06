import frappe
from frappe.model.document import Document

from atlas.atlas.providers import for_provider

# The IP belongs to the Server for its lifetime; only the VM attachment moves.
IMMUTABLE_AFTER_INSERT = (
	"ip_address",
	"server",
	"provider_resource_id",
)


class ReservedIP(Document):
	def validate(self) -> None:
		self._validate_immutability()
		self._sync_status()

	def _validate_immutability(self) -> None:
		"""Lock the IP, its Server, and the vendor handle once written. Allow the
		initial None → value population (the same idiom as Server)."""
		if self.is_new():
			return
		original = self.get_doc_before_save()
		if not original:
			return
		for field in IMMUTABLE_AFTER_INSERT:
			old_value = getattr(original, field)
			if old_value and old_value != getattr(self, field):
				frappe.throw(f"{field} is immutable after insert")

	def _sync_status(self) -> None:
		"""status is derived from virtual_machine: Attached iff a VM is set."""
		self.status = "Attached" if self.virtual_machine else "Allocated"

	@frappe.whitelist()
	def attach(self, virtual_machine: str) -> None:
		"""Attach this Server-allocated IP to a VM on the same Server.

		Denormalizes the address onto Virtual Machine.public_ipv4 so the VM row
		carries its public v4 directly. The host-side wiring (DO reserved-IP
		attach + the 1:1 nftables NAT to the guest /30) is a follow-up Task; this
		method owns the Frappe-side invariant — one IP, one VM, same Server."""
		if self.virtual_machine:
			frappe.throw(f"{self.ip_address} is already attached to {self.virtual_machine}")
		vm = frappe.get_doc("Virtual Machine", virtual_machine)
		if vm.server != self.server:
			frappe.throw(f"{self.ip_address} is allocated to a different Server than {virtual_machine}")
		if vm.public_ipv4:
			frappe.throw(f"{virtual_machine} already has a public IPv4 ({vm.public_ipv4})")
		self.virtual_machine = virtual_machine
		self.save()
		vm.db_set("public_ipv4", self.ip_address)

	@frappe.whitelist()
	def detach(self) -> None:
		"""Release this IP from its VM, leaving it allocated to the Server and
		available to attach elsewhere. Clears the VM's denormalized address."""
		if not self.virtual_machine:
			frappe.throw(f"{self.ip_address} is not attached to any VM")
		vm_name = self.virtual_machine
		self.virtual_machine = None
		self.save()
		# The VM row may already be gone (terminated + record deleted); guard it.
		if frappe.db.exists("Virtual Machine", vm_name):
			frappe.db.set_value("Virtual Machine", vm_name, "public_ipv4", None)

	@frappe.whitelist()
	def release(self) -> None:
		"""Destroy the vendor reserved IP and delete this row, returning the
		address to the vendor's pool. The IP must be detached first.

		Explicit, like `Server.archive()` — destroying a vendor resource is
		never a side effect of deleting the Frappe row (see `on_trash`)."""
		if self.virtual_machine:
			frappe.throw(f"Detach {self.ip_address} from {self.virtual_machine} before releasing it")
		if self.provider_resource_id:
			_provider_for_server(self.server).release_reserved_ip(self.provider_resource_id)
		self.delete()

	def on_trash(self) -> None:
		"""Refuse to delete a row whose IP is still attached — that would strand
		the VM's denormalized `public_ipv4` and (later) the host NAT. Deleting
		the row does NOT touch the vendor; use `release()` to destroy the vendor
		IP, mirroring `Server`'s explicit `archive()`."""
		if self.virtual_machine:
			frappe.throw(f"Detach {self.ip_address} from {self.virtual_machine} before deleting it")


def _provider_for_server(server: str):
	"""Resolve the Provider for a Reserved IP via its Server. Reserved IPs are
	per-Server, so we use the Server's own provider rather than the globally
	active one (`atlas.get_provider()`) — correct even with several providers."""
	provider_name = frappe.db.get_value("Server", server, "provider")
	return for_provider(provider_name)


@frappe.whitelist()
def allocate(server: str) -> str:
	"""Reserve a fresh public IPv4 at the vendor for `server` and write a
	`Reserved IP` row for it (Allocated, unattached). Returns the new row name.

	The vendor reserves the IP in its (single) region; binding it to the
	droplet and the host 1:1-NAT happen on `attach()` (a follow-up Task), not
	here — an allocated-but-unattached IP is the resting state of a pool entry."""
	reserved = _provider_for_server(server).allocate_reserved_ip()
	return frappe.get_doc({
		"doctype": "Reserved IP",
		"ip_address": reserved.ip_address,
		"server": server,
		"provider_resource_id": reserved.provider_resource_id,
	}).insert().name


@frappe.whitelist()
def discover(server: str) -> list[str]:
	"""Import the vendor's reserved IPs already bound to `server`'s droplet
	into the pool, creating a `Reserved IP` row for each one Atlas doesn't yet
	model. Returns the names of the rows created (existing ones are skipped).

	Reconcile, vendor → Frappe: a reserved IP the operator created out-of-band
	(or one that survived a row deletion) shows up in the pool on the next
	discover. Mapped by `droplet_resource_id` == the Server's
	`provider_resource_id`, so only IPs on *this* host are imported."""
	droplet_id = frappe.db.get_value("Server", server, "provider_resource_id")
	if not droplet_id:
		frappe.throw(f"Server {server} has no provider_resource_id; cannot discover reserved IPs")
	created = []
	for reserved in _provider_for_server(server).list_reserved_ips():
		if reserved.droplet_resource_id != droplet_id:
			continue
		if frappe.db.exists("Reserved IP", {"ip_address": reserved.ip_address}):
			continue
		row = frappe.get_doc({
			"doctype": "Reserved IP",
			"ip_address": reserved.ip_address,
			"server": server,
			"provider_resource_id": reserved.provider_resource_id,
		}).insert()
		created.append(row.name)
	return created
