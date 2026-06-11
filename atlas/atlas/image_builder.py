"""Image builder — the shared seam that builds an image INSIDE a guest over SSH.

`run_build(vm, recipe)` is the de-duplicated core of what `bench_image.build_bench`
and `proxy.build_proxy` both used to do verbatim: upload a committed source tree
into a freshly-provisioned guest, run its `build.sh` DETACHED (so a mid-build SSH
reset doesn't kill the long compile/bake), run the recipe's finalize step, and
record one Task row for the operator's audit trail — failing loud on a non-zero
exit. The two `build_*` functions are now thin wrappers that hand it a recipe.

The full provision→build→snapshot→register lifecycle around this seam lives in the
`Image Build` DocType (spec/15-image-builder.md); this module owns only the
upload+build+finalize+audit half. It SSHes *into the guest* (`connection_for_guest`,
the second SSH target type, spec/04), not onto a Server — the same path the proxy
control plane uses.
"""

import shlex
from collections.abc import Callable
from pathlib import Path

import frappe

from atlas.atlas._ssh.transport import forget_host, run_detached, run_scp, run_ssh, ssh_key_file
from atlas.atlas.image_recipes import ImageRecipe
from atlas.atlas.proxy import _record_guest_task, _remote_parent
from atlas.atlas.ssh import connection_for_guest


def _source_directory(recipe: ImageRecipe) -> Path:
	"""The committed tree the recipe bakes, e.g. `<repo>/bench` or `<repo>/proxy`.
	The `..` resolves the app symlink to the repo root, where these trees sit
	beside `scripts/` (the same idiom as scripts_catalog.scripts_directory())."""
	return Path(frappe.get_app_path("atlas", "..")).resolve() / recipe.source_directory


def tree_uploads(recipe: ImageRecipe) -> list[tuple[Path, str]]:
	"""Every committed file under the recipe's source tree, mapped to its remote
	path under `recipe.remote_directory`, preserving the relative layout so
	`build.sh` finds its siblings (bench.toml, or conf/lua/html/guest) beside
	itself — it reads from its own directory. Top-level entries in `recipe.exclude`
	(the proxy's dev-only `test/` harness) and any `__pycache__` are skipped."""
	source = _source_directory(recipe)
	uploads: list[tuple[Path, str]] = []
	for entry in sorted(source.rglob("*")):
		if not entry.is_file():
			continue
		relative = entry.relative_to(source)
		if relative.parts[0] in recipe.exclude or "__pycache__" in relative.parts:
			continue
		uploads.append((entry, f"{recipe.remote_directory}/{relative.as_posix()}"))
	return uploads


def run_build(
	virtual_machine: str, recipe: ImageRecipe, on_task: Callable[[str], None] | None = None
) -> None:
	"""Upload the recipe's committed tree into the guest, run its build entrypoint
	DETACHED, then run the recipe's finalize hook. Records one Task row (named by
	`recipe.task_script`) and throws on any non-zero exit.

	`on_task`, if given, is called with the recorded Task's name right after it is
	inserted and BEFORE the throw — so a caller (the Image Build controller) can
	link the build Task for its audit trail even when the build failed.

	Idempotent: the committed `build.sh` scripts are idempotent (spec taste #16,
	retry = re-run), so this doubles as the re-bake verb."""
	vm = frappe.get_doc("Virtual Machine", virtual_machine)
	connection = connection_for_guest(vm)
	uploads = tree_uploads(recipe)
	# Freshly-provisioned VM, possibly on a recycled IP whose old host key we
	# pinned. This path goes straight to run_scp/run_ssh (no wait_for_ssh), so
	# accept-new never re-pins a CHANGED key — drop the stale entry first or the
	# first scp hard-fails "REMOTE HOST IDENTIFICATION HAS CHANGED"
	# (real-provision-traps #1).
	forget_host(connection.host)
	with ssh_key_file(connection.ssh_private_key) as key_path:
		_stage_tree(connection, key_path, uploads)
		# Run the build (long: apt + clone + uv for bench; nginx + luajit compile
		# for proxy) DETACHED, so a connection reset mid-build doesn't SIGHUP it.
		# The shared run_detached helper owns the setsid+nohup + marker-poll
		# mechanics; we hand it the entrypoint and its own log/done paths.
		stdout, stderr, code = run_detached(
			connection,
			key_path,
			f"chmod +x {recipe.remote_entrypoint} && {recipe.remote_entrypoint}",
			log_path=recipe.build_log_path,
			done_path=recipe.build_done_path,
		)
		if code == 0 and recipe.finalize:
			# Fast follow-up after a successful build (no detach needed). Its
			# stdout/stderr/code become the recorded result, so a finalize failure
			# is a build failure.
			stdout, stderr, code = recipe.finalize(vm, connection, key_path)
	task_name = _record_guest_task(
		virtual_machine, recipe.task_script, {"recipe": recipe.name}, stdout, stderr, code
	)
	if on_task:
		on_task(task_name)
	if code != 0:
		frappe.throw(f"{recipe.title} build on {virtual_machine} failed (exit {code}): {stderr[-500:]}")


def _stage_tree(connection, key_path, uploads: list[tuple[Path, str]]) -> None:
	"""mkdir -p every remote parent dir in one SSH call, then scp every file.
	Staging the whole tree under one dir is what lets `build.sh` find its siblings
	(it reads from its own directory)."""
	remote_dirs = sorted({_remote_parent(remote) for _, remote in uploads})
	run_ssh(
		connection,
		key_path,
		"mkdir -p " + " ".join(shlex.quote(directory) for directory in remote_dirs),
		timeout_seconds=60,
	)
	for local, remote in uploads:
		run_scp(connection, key_path, str(local), remote, timeout_seconds=300)
