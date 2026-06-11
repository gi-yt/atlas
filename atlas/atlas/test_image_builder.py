"""Unit tests for the shared image-build seam + the recipe registry.

The tree enumeration is pure (reads the committed bench/ and proxy/ trees) and the
run_build path mocks the guest-SSH plumbing — all milliseconds, no host. The host
fact (a real bake actually produces a working bench / serving proxy) is the e2e's
job (spec/08, spec/12, spec/15)."""

import contextlib
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas import image_builder
from atlas.atlas.doctype.virtual_machine.test_virtual_machine import (
	_ensure_test_image,
	_ensure_test_server,
	_new_vm,
)
from atlas.atlas.image_recipes import RECIPES, get_recipe

_BENCH = get_recipe("bench")
_PROXY = get_recipe("proxy")


def _purge() -> None:
	# Tasks are append-only audit rows (not purged); every assertion filters by the
	# per-test VM name (a fresh UUID), so stale Tasks never match.
	for name in frappe.get_all("Virtual Machine", pluck="name"):
		frappe.delete_doc("Virtual Machine", name, force=1, ignore_permissions=True)


@contextlib.contextmanager
def _mock_build_ssh(build_result, finalize_result=("", "", 0)):
	"""Patch the guest-SSH plumbing run_build uses. `build_result` is what the
	detached build returns; `finalize_result` what the proxy recipe's finalize
	run_ssh returns (image_recipes calls run_ssh, so patch it there too). Yields
	(run_ssh, run_scp, run_detached, forget_host, finalize_run_ssh)."""
	run_ssh = MagicMock(return_value=("", "", 0))
	run_scp = MagicMock(return_value=None)
	run_detached = MagicMock(return_value=build_result)
	forget_host = MagicMock(return_value=None)
	finalize_run_ssh = MagicMock(return_value=finalize_result)
	key_cm = MagicMock()
	key_cm.__enter__ = MagicMock(return_value="/tmp/fake.key")
	key_cm.__exit__ = MagicMock(return_value=False)
	with (
		patch.object(image_builder, "run_ssh", run_ssh),
		patch.object(image_builder, "run_scp", run_scp),
		patch.object(image_builder, "run_detached", run_detached),
		patch.object(image_builder, "forget_host", forget_host),
		patch.object(image_builder, "ssh_key_file", return_value=key_cm),
		patch.object(
			image_builder,
			"connection_for_guest",
			return_value=MagicMock(ssh_private_key="KEY", host="2400::dead"),
		),
		patch("atlas.atlas.image_recipes.run_ssh", finalize_run_ssh),
	):
		yield run_ssh, run_scp, run_detached, forget_host, finalize_run_ssh


class TestRecipeRegistry(IntegrationTestCase):
	def test_known_recipes(self) -> None:
		self.assertEqual(sorted(RECIPES), ["bench", "proxy"])

	def test_unknown_recipe_throws(self) -> None:
		with self.assertRaises(frappe.ValidationError):
			get_recipe("nope")

	def test_bench_recipe_shape(self) -> None:
		self.assertEqual(_BENCH.task_script, "bench-build")
		self.assertEqual(_BENCH.registers_as, "default_bench_snapshot")
		self.assertFalse(_BENCH.is_proxy)
		self.assertIsNone(_BENCH.finalize)

	def test_proxy_recipe_shape(self) -> None:
		self.assertEqual(_PROXY.task_script, "proxy-build")
		self.assertIsNone(_PROXY.registers_as)
		self.assertTrue(_PROXY.is_proxy)
		self.assertIsNotNone(_PROXY.finalize)
		self.assertIn("test", _PROXY.exclude)


class TestTreeUploads(IntegrationTestCase):
	def test_bench_tree_has_build_and_toml_no_caches(self) -> None:
		remotes = [remote for _, remote in image_builder.tree_uploads(_BENCH)]
		self.assertTrue(any(r.endswith("/build.sh") for r in remotes), remotes)
		self.assertTrue(any(r.endswith("/bench.toml") for r in remotes), remotes)
		self.assertFalse(any("__pycache__" in r for r in remotes), remotes)
		# build.sh sits at the staging root so it finds its sibling bench.toml.
		build = next(r for _, r in image_builder.tree_uploads(_BENCH) if r.endswith("/build.sh"))
		self.assertEqual(build, _BENCH.remote_entrypoint)

	def test_proxy_tree_excludes_test_harness(self) -> None:
		remotes = [remote for _, remote in image_builder.tree_uploads(_PROXY)]
		self.assertTrue(any(r.endswith("/build.sh") for r in remotes), remotes)
		self.assertTrue(any(r.endswith("/conf/nginx.conf") for r in remotes), remotes)
		self.assertTrue(any(r.endswith("/lua/router.lua") for r in remotes), remotes)
		self.assertTrue(any(r.endswith("/guest/atlas-proxy.service") for r in remotes), remotes)
		# The dev-only compose harness (recipe.exclude=("test",)) + caches are gone.
		self.assertFalse(any("/test/" in r for r in remotes), remotes)
		self.assertFalse(any("__pycache__" in r for r in remotes), remotes)


class TestRunBuild(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_test_server()
		_ensure_test_image()
		_purge()

	def test_uploads_tree_then_runs_detached_and_records_task(self) -> None:
		vm = _new_vm()
		with _mock_build_ssh(("baked", "", 0)) as (run_ssh, run_scp, run_detached, forget_host, _fin):
			image_builder.run_build(vm.name, _BENCH)
		# Every committed file scp'd; a stale recycled-IP host key dropped first.
		self.assertEqual(run_scp.call_count, len(image_builder.tree_uploads(_BENCH)))
		forget_host.assert_called_once_with("2400::dead")
		# mkdir is the first short SSH; the long build goes through run_detached.
		self.assertIn("mkdir -p", run_ssh.call_args_list[0].args[2])
		run_detached.assert_called_once()
		self.assertIn("build.sh", run_detached.call_args.args[2])
		self.assertEqual(run_detached.call_args.kwargs["log_path"], _BENCH.build_log_path)
		self.assertEqual(run_detached.call_args.kwargs["done_path"], _BENCH.build_done_path)
		status = frappe.get_all(
			"Task", filters={"virtual_machine": vm.name, "script": "bench-build"}, pluck="status"
		)
		self.assertEqual(status, ["Success"])

	def test_build_failure_raises_and_records_failure(self) -> None:
		vm = _new_vm()
		with _mock_build_ssh(("bench init: error", "", 1)):
			with self.assertRaises(frappe.ValidationError):
				image_builder.run_build(vm.name, _BENCH)
		status = frappe.get_all(
			"Task", filters={"virtual_machine": vm.name, "script": "bench-build"}, pluck="status"
		)
		self.assertEqual(status, ["Failure"])

	def test_on_task_callback_fires_with_task_name_on_success(self) -> None:
		vm = _new_vm()
		seen = []
		with _mock_build_ssh(("baked", "", 0)):
			image_builder.run_build(vm.name, _BENCH, on_task=seen.append)
		self.assertEqual(len(seen), 1)
		self.assertTrue(frappe.db.exists("Task", seen[0]))

	def test_on_task_callback_fires_before_throw_on_failure(self) -> None:
		# The Image Build controller links the build Task even on a failed build —
		# on_task must fire before run_build throws.
		vm = _new_vm()
		seen = []
		with _mock_build_ssh(("boom", "", 1)):
			with self.assertRaises(frappe.ValidationError):
				image_builder.run_build(vm.name, _BENCH, on_task=seen.append)
		self.assertEqual(len(seen), 1)
		self.assertEqual(frappe.db.get_value("Task", seen[0], "status"), "Failure")

	def test_proxy_recipe_runs_finalize_after_build(self) -> None:
		vm = _new_vm(is_proxy=1, region="blr1")
		with _mock_build_ssh(("built", "", 0)) as (_ssh, _scp, _det, _fh, finalize_run_ssh):
			image_builder.run_build(vm.name, _PROXY)
		# The proxy recipe's finalize wrote the region + restarted the unit.
		finalize_run_ssh.assert_called_once()
		finalize_command = finalize_run_ssh.call_args.args[2]
		self.assertIn("blr1", finalize_command)
		self.assertIn("systemctl restart atlas-proxy.service", finalize_command)
		# It must NOT repoint the cert symlink (push_cert owns that, after the real
		# cert lands — repointing here would dangle the symlink at start).
		self.assertNotIn("ln -sfn", finalize_command)

	def test_proxy_finalize_failure_is_a_build_failure(self) -> None:
		vm = _new_vm(is_proxy=1, region="blr1")
		# Build succeeds, finalize (region-write/restart) fails → run_build throws,
		# and the recorded Task is a Failure.
		with _mock_build_ssh(("built", "", 0), finalize_result=("", "no such unit", 1)):
			with self.assertRaises(frappe.ValidationError):
				image_builder.run_build(vm.name, _PROXY)
		status = frappe.get_all(
			"Task", filters={"virtual_machine": vm.name, "script": "proxy-build"}, pluck="status"
		)
		self.assertEqual(status, ["Failure"])
