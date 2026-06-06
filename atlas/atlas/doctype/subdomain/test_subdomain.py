import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas.doctype.subdomain.subdomain import map_for_region
from atlas.atlas.doctype.virtual_machine.test_virtual_machine import (
	_ensure_test_image,
	_ensure_test_server,
	_new_vm,
)


def _purge() -> None:
	for name in frappe.get_all("Subdomain", pluck="name"):
		frappe.delete_doc("Subdomain", name, force=1, ignore_permissions=True)
	for name in frappe.get_all("Virtual Machine", pluck="name"):
		frappe.delete_doc("Virtual Machine", name, force=1, ignore_permissions=True)


def _make_subdomain(subdomain: str, vm: str, region: str = "blr1", **overrides):
	doc = {
		"doctype": "Subdomain",
		"subdomain": subdomain,
		"virtual_machine": vm,
		"region": region,
	}
	doc.update(overrides)
	return frappe.get_doc(doc).insert(ignore_permissions=True)


class TestSubdomain(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_test_server()
		_ensure_test_image()
		_purge()

	def test_address_is_denormalized_from_vm(self) -> None:
		vm = _new_vm()
		sub = _make_subdomain("acme", vm.name)
		# The proxy dials this literal; it must equal the target VM's /128 so the
		# desired-map query is a join-free SELECT.
		self.assertEqual(sub.address, vm.ipv6_address)
		self.assertTrue(sub.address)

	def test_autoname_is_the_subdomain(self) -> None:
		vm = _new_vm()
		sub = _make_subdomain("widgets", vm.name)
		self.assertEqual(sub.name, "widgets")

	def test_subdomain_is_unique(self) -> None:
		vm = _new_vm()
		_make_subdomain("acme", vm.name)
		with self.assertRaises(frappe.exceptions.DuplicateEntryError):
			_make_subdomain("acme", vm.name)

	def test_target_vm_must_be_addressable(self) -> None:
		vm = _new_vm()
		# Strip the VM's address: an unaddressable target can't be a route.
		frappe.db.set_value("Virtual Machine", vm.name, "ipv6_address", None)
		with self.assertRaises(frappe.ValidationError):
			_make_subdomain("acme", vm.name)

	def test_routing_key_and_target_are_immutable(self) -> None:
		vm = _new_vm()
		other = _new_vm()
		sub = _make_subdomain("acme", vm.name)
		sub.virtual_machine = other.name
		with self.assertRaises(frappe.ValidationError):
			sub.save(ignore_permissions=True)

	def test_active_toggles_without_error(self) -> None:
		vm = _new_vm()
		sub = _make_subdomain("acme", vm.name)
		sub.active = 0
		sub.save(ignore_permissions=True)
		self.assertFalse(sub.active)

	def test_map_for_region_returns_active_only(self) -> None:
		vm_a = _new_vm()
		vm_b = _new_vm()
		_make_subdomain("acme", vm_a.name, region="blr1")
		_make_subdomain("widgets", vm_b.name, region="blr1")
		_make_subdomain("dormant", vm_a.name, region="blr1", active=0)
		site_map = map_for_region("blr1")
		self.assertEqual(
			site_map,
			{"acme": vm_a.ipv6_address, "widgets": vm_b.ipv6_address},
		)
		self.assertNotIn("dormant", site_map)

	def test_map_for_region_scopes_to_region(self) -> None:
		vm = _new_vm()
		_make_subdomain("acme", vm.name, region="blr1")
		_make_subdomain("acme-sg", vm.name, region="sgp1")
		self.assertEqual(set(map_for_region("blr1")), {"acme"})
		self.assertEqual(set(map_for_region("sgp1")), {"acme-sg"})

	def test_empty_region_is_empty_map(self) -> None:
		self.assertEqual(map_for_region("nowhere"), {})
