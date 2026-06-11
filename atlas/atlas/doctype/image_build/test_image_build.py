"""Unit tests for the Image Build controller — the bake lifecycle state machine.

All milliseconds, no host: the host steps (provision a build VM, run build.sh in
the guest, snapshot it) are mocked at the module seams; only the pure orchestration
(status transitions, artifact linking, auto-register, terminate, immutability,
fail-loud, rebake) is asserted here. The real bake is the e2e's job (spec/15)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas.doctype.image_build import image_build as image_build_module
from atlas.atlas.doctype.virtual_machine.test_virtual_machine import (
	_ensure_test_image,
	_ensure_test_server,
)


def _purge() -> None:
	for name in frappe.get_all("Image Build", pluck="name"):
		frappe.delete_doc("Image Build", name, force=1, ignore_permissions=True)


def _new_build(recipe: str = "bench", **overrides):
	"""Insert an Image Build WITHOUT firing the background job (after_insert
	enqueues run() — we drive run() by hand in the tests that want it).

	Passes an explicit `base_image` so insert never depends on `default_image()`
	resolving cleanly (the shared test DB carries several active images, which
	default_image() refuses to pick between — that's the operator's job, not this
	test's concern)."""
	doc = {
		"doctype": "Image Build",
		"recipe": recipe,
		"server": _ensure_test_server(),
		"base_image": _ensure_test_image(),
	}
	doc.update(overrides)
	with patch.object(image_build_module.frappe, "enqueue"):
		return frappe.get_doc(doc).insert(ignore_permissions=True)


class TestImageBuildInsert(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_test_server()
		_ensure_test_image()
		_purge()

	def test_before_insert_fills_title_and_status(self) -> None:
		build = _new_build("bench")
		self.assertEqual(build.title, "Golden bench image")
		self.assertEqual(build.status, "Draft")
		# Base image defaulted from Atlas Settings / the active image.
		self.assertTrue(build.base_image)

	def test_proxy_recipe_requires_region(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			_new_build("proxy")  # no region

	def test_proxy_recipe_with_region_inserts(self) -> None:
		build = _new_build("proxy", region="blr1")
		self.assertEqual(build.title, "Reverse proxy image")
		self.assertEqual(build.region, "blr1")

	def test_after_insert_enqueues_run(self) -> None:
		with patch.object(image_build_module.frappe, "enqueue") as enqueue:
			frappe.get_doc(
				{
					"doctype": "Image Build",
					"recipe": "bench",
					"server": _ensure_test_server(),
					"base_image": _ensure_test_image(),
				}
			).insert(ignore_permissions=True)
		enqueue.assert_called_once()
		self.assertEqual(
			enqueue.call_args.args[0],
			"atlas.atlas.doctype.image_build.image_build.run",
		)
		self.assertEqual(enqueue.call_args.kwargs["queue"], "long")

	def test_recipe_is_immutable_after_insert(self) -> None:
		build = _new_build("bench")
		build.recipe = "proxy"
		with self.assertRaises(frappe.ValidationError):
			build.save(ignore_permissions=True)


class TestImageBuildRun(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_test_server()
		_ensure_test_image()
		_purge()

	def _run_with_mocks(self, build, **extra):
		"""Drive run() with every host seam mocked. Returns the mocks for asserting."""
		defaults = dict(
			_provision_build_vm=patch.object(
				image_build_module, "_provision_build_vm", return_value="build-vm-1"
			),
			_wait=patch.object(image_build_module, "_wait_for_vm_running"),
			run_build=patch.object(image_build_module, "run_build"),
			_snap=patch.object(image_build_module, "_stop_and_snapshot", return_value="snap-1"),
			_register=patch.object(image_build_module, "_register"),
			_terminate=patch.object(image_build_module, "_terminate_build_vm"),
			commit=patch.object(image_build_module.frappe.db, "commit"),
		)
		with (
			defaults["_provision_build_vm"] as m_prov,
			defaults["_wait"] as m_wait,
			defaults["run_build"] as m_build,
			defaults["_snap"] as m_snap,
			defaults["_register"] as m_register,
			defaults["_terminate"] as m_terminate,
			defaults["commit"],
		):
			image_build_module.run(build.name)
		return m_prov, m_wait, m_build, m_snap, m_register, m_terminate

	def test_happy_path_reaches_available_and_links_artifacts(self) -> None:
		build = _new_build("bench")
		self._run_with_mocks(build)
		build.reload()
		self.assertEqual(build.status, "Available")
		self.assertEqual(build.build_virtual_machine, "build-vm-1")
		self.assertEqual(build.snapshot, "snap-1")

	def test_bench_build_auto_registers_when_checked(self) -> None:
		build = _new_build("bench", auto_register=1)
		_, _, _, _, m_register, _ = self._run_with_mocks(build)
		m_register.assert_called_once()

	def test_bench_build_skips_register_when_unchecked(self) -> None:
		build = _new_build("bench", auto_register=0)
		_, _, _, _, m_register, _ = self._run_with_mocks(build)
		m_register.assert_not_called()

	def test_proxy_build_never_registers(self) -> None:
		# The proxy recipe has no registers_as, so register is skipped even if the
		# (harmless, defaulted-on) auto_register check is set.
		build = _new_build("proxy", region="blr1", auto_register=1)
		_, _, _, _, m_register, _ = self._run_with_mocks(build)
		m_register.assert_not_called()

	def test_terminate_build_vm_when_checked(self) -> None:
		build = _new_build("bench", terminate_build_vm=1)
		_, _, _, _, _, m_terminate = self._run_with_mocks(build)
		m_terminate.assert_called_once_with("build-vm-1")

	def test_keeps_build_vm_by_default(self) -> None:
		build = _new_build("bench")
		_, _, _, _, _, m_terminate = self._run_with_mocks(build)
		m_terminate.assert_not_called()

	def test_failure_marks_failed_and_records_error_and_reraises(self) -> None:
		build = _new_build("bench")
		with (
			patch.object(image_build_module, "_provision_build_vm", return_value="vm-x"),
			patch.object(image_build_module, "_wait_for_vm_running"),
			patch.object(image_build_module, "run_build", side_effect=RuntimeError("build broke")),
			patch.object(image_build_module.frappe.db, "commit"),
		):
			with self.assertRaises(RuntimeError):
				image_build_module.run(build.name)
		build.reload()
		self.assertEqual(build.status, "Failed")
		self.assertIn("build broke", build.error)

	def test_run_is_noop_when_not_draft(self) -> None:
		build = _new_build("bench")
		build.db_set("status", "Available")
		with patch.object(image_build_module, "_provision_build_vm") as m_prov:
			image_build_module.run(build.name)
		m_prov.assert_not_called()


class TestImageBuildRebake(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_test_server()
		_ensure_test_image()
		_purge()

	def test_rebake_resets_to_draft_and_reenqueues(self) -> None:
		build = _new_build("bench")
		build.db_set("status", "Failed")
		build.db_set("error", "old failure")
		with patch.object(image_build_module.frappe, "enqueue") as enqueue:
			with patch.object(image_build_module.frappe.db, "commit"):
				build.rebake()
		build.reload()
		self.assertEqual(build.status, "Draft")
		self.assertFalse(build.error)
		enqueue.assert_called_once()

	def test_rebake_rejected_while_in_flight(self) -> None:
		build = _new_build("bench")
		build.db_set("status", "Building")
		with self.assertRaises(frappe.ValidationError):
			build.rebake()
