"""Phase 10 e2e: synchronous calls to background-job entrypoints.

`server_provider.finish_provisioning` and `atlas.atlas.ssh.execute_task`
normally run in workers (queue "long"), where our `coverage run`
instrumentation does not reach. Both are written to work synchronously;
calling them directly records their coverage without changing production
semantics.

Reuses the shared server.
"""

import time
import traceback

import frappe

from atlas.atlas._ssh.runner import execute_task
from atlas.tests.e2e._shared import (
	ensure_default_image_row,
	phase,
	wait_for_task,
)


def run(reuse: bool = True, keep: bool = True) -> None:
	start = time.monotonic()
	try:
		with phase("phase-10", reuse=reuse, keep=keep) as server:
			_check_execute_task_sync(server)
			_check_test_connection(server)
			_check_provision_server_duplicate_name(server)
			_check_finish_provisioning_sync(server)
	except Exception:
		print(f"phase-10: FAIL in {time.monotonic() - start:.0f}s")
		traceback.print_exc()
		raise
	print(f"phase-10: OK in {time.monotonic() - start:.0f}s")


def _check_execute_task_sync(server) -> None:
	"""Insert a Pending sync-image Task and call execute_task() in-process.

	`image.sync_to_server` does the same insertion path but enqueues. We
	insert by hand and call execute_task directly so the runner's branches
	are recorded by coverage.
	"""
	image = ensure_default_image_row()
	variables = {
		"IMAGE_NAME": image.image_name,
		"KERNEL_URL": image.kernel_url,
		"KERNEL_FILENAME": image.kernel_filename,
		"KERNEL_SHA256": image.kernel_sha256,
		"ROOTFS_URL": image.rootfs_url,
		"ROOTFS_FILENAME": image.rootfs_filename,
		"ROOTFS_SHA256": image.rootfs_sha256,
		"DEFAULT_DISK_GB": str(image.default_disk_gigabytes),
		"GUEST_NETWORK_UNIT": "/tmp/atlas/atlas-network.service",
	}
	task = frappe.get_doc({
		"doctype": "Task",
		"server": server.name,
		"script": "sync-image.sh",
		"status": "Pending",
		"triggered_by": "Administrator",
	})
	task.variables_dict = variables
	task.insert(ignore_permissions=True)
	frappe.db.commit()

	execute_task(task.name)

	# execute_task is synchronous; poll briefly just for the rollback.
	final = wait_for_task(task.name, timeout_seconds=120, poll_seconds=1)
	assert final.status == "Success", (final.status, (final.stderr or "")[:300])


def _check_test_connection(server) -> None:
	"""Cover Server Provider.test_connection. The configured token may be
	scoped without `account:read`, in which case the call 403s; either way
	the code path is recorded."""
	from atlas.atlas.digitalocean import DigitalOceanError

	provider = frappe.get_doc("Server Provider", server.provider)
	try:
		result = provider.test_connection()
		assert result.get("ok") is True, result
	except DigitalOceanError as exception:
		assert "403" in str(exception) or "forbidden" in str(exception).lower(), str(exception)


def _check_provision_server_duplicate_name(server) -> None:
	"""Cover the duplicate-name throw in provision_server."""
	provider = frappe.get_doc("Server Provider", server.provider)
	caught = False
	try:
		provider.provision_server(server.name)
	except frappe.ValidationError as exception:
		caught = "already exists" in str(exception).lower()
	assert caught, "provision_server with duplicate name should have raised"


def _check_finish_provisioning_sync(server) -> None:
	"""Synchronously re-run finish_provisioning on the existing shared server.

	`finish_provisioning` normally runs in a worker. Every step is idempotent:
	`wait_for_active` returns immediately (the droplet is already active),
	address recording overwrites with the same values, `wait_for_ssh` is a
	one-shot probe, and `bootstrap()` is idempotent (phase 11 also re-runs it).
	The final status flip is back to Active.
	"""
	from atlas.atlas.doctype.server_provider.server_provider import finish_provisioning

	assert server.provider_resource_id, "shared server has no provider_resource_id"
	finish_provisioning(server.name, int(server.provider_resource_id))

	# Sanity: row still Active after re-running.
	server.reload()
	assert server.status == "Active", server.status
