"""Phase 8 e2e: negative-path coverage against the shared server.

Exercises every documented validation throw without provisioning a fresh
droplet. Adds branch coverage for `_ssh/runner.py`, `task.py`,
`virtual_machine.py`, `virtual_machine_image.py`, and `server.py`.
"""

import frappe

from atlas.atlas._ssh.runner import connection_for_server, execute_task, run_task
from atlas.tests.e2e._shared import (
	ensure_image_on_server,
	ephemeral_public_key,
	expect_validation_error,
	phase,
)


def run(reuse: bool = True, keep: bool = True) -> None:
	with phase("phase-8", reuse=reuse, keep=keep) as server:
		_check_run_task_validation(server)
		_check_connection_for_server_validation()
		_check_execute_task_validation()
		_check_task_doctype_validation(server)
		_check_server_run_task_dialog(server)
		_check_server_get_scripts(server)
		_check_server_bootstrap_status_guard(server)
		_check_virtual_machine_validation(server)
		_check_virtual_machine_image_validation()
		_check_networking_helpers()
		_check_ipv6_exhaustion(server)
		_check_run_task_unknown_script(server)
		_check_run_task_failure(server)
		_check_run_task_timeout(server)


def _check_run_task_validation(server) -> None:
	# Both server= and connection= → throw.
	from atlas.atlas.ssh import Connection

	dummy = Connection(host="0.0.0.0", ssh_private_key="-----BEGIN-----\nx\n-----END-----")
	with expect_validation_error("exactly one"):
		run_task(server=server.name, connection=dummy, script="x.sh", variables={})

	# Neither → throw.
	with expect_validation_error("exactly one"):
		run_task(script="x.sh", variables={})


def _check_connection_for_server_validation() -> None:
	# Server with no ipv4_address: build a transient in-memory doc.
	transient = frappe.get_doc({
		"doctype": "Server",
		"server_name": "phase8-no-ip",
		"status": "Pending",
	})
	with expect_validation_error("no ipv4_address"):
		connection_for_server(transient)

	# Server with ipv4 but no provider → throw.
	transient.ipv4_address = "192.0.2.1"
	with expect_validation_error("no provider"):
		connection_for_server(transient)


def _check_execute_task_validation() -> None:
	# Task with no server attribute → throw.
	task = frappe.get_doc({
		"doctype": "Task",
		"script": "noop.sh",
		"status": "Pending",
		"triggered_by": "Administrator",
	})
	task.variables_dict = {}
	task.insert(ignore_permissions=True)
	frappe.db.commit()
	with expect_validation_error("no server"):
		execute_task(task.name)


def _check_task_doctype_validation(server) -> None:
	# variables empty → throw.
	doc = frappe.get_doc({
		"doctype": "Task",
		"server": server.name,
		"script": "noop.sh",
		"status": "Pending",
		"triggered_by": "Administrator",
	})
	with expect_validation_error("variables is required"):
		doc.insert(ignore_permissions=True)

	# Invalid JSON in variables → throw on save.
	doc = frappe.get_doc({
		"doctype": "Task",
		"server": server.name,
		"script": "noop.sh",
		"status": "Pending",
		"triggered_by": "Administrator",
		"variables": "not json",
	})
	with expect_validation_error("must be valid json"):
		doc.insert(ignore_permissions=True)

	# Non-object JSON → throw.
	doc = frappe.get_doc({
		"doctype": "Task",
		"server": server.name,
		"script": "noop.sh",
		"status": "Pending",
		"triggered_by": "Administrator",
		"variables": "[1, 2]",
	})
	with expect_validation_error("json object"):
		doc.insert(ignore_permissions=True)

	# variables_dict setter rejects non-dict.
	doc = frappe.get_doc({
		"doctype": "Task",
		"server": server.name,
		"script": "noop.sh",
		"status": "Pending",
		"triggered_by": "Administrator",
	})
	with expect_validation_error("must be a dict"):
		doc.variables_dict = [1, 2]

	# Immutability: mutate `script` after insert.
	doc = frappe.get_doc({
		"doctype": "Task",
		"server": server.name,
		"script": "phase1-probe.sh",
		"status": "Pending",
		"triggered_by": "Administrator",
	})
	doc.variables_dict = {"NAME": "x"}
	doc.insert(ignore_permissions=True)
	doc.reload()
	doc.script = "phase1-fail.sh"
	with expect_validation_error("read-only after insert"):
		doc.save(ignore_permissions=True)

	# variables_dict reader returns parsed dict (covers property getter).
	doc2 = frappe.get_doc("Task", doc.name)
	assert doc2.variables_dict == {"NAME": "x"}, doc2.variables_dict

	# `_validate_immutability` early-returns when `_doc_before_save` is None
	# (the doc was loaded without an intervening save). Call it directly to
	# drive that branch.
	fresh = frappe.get_doc("Task", doc.name)
	fresh._validate_immutability()  # must not raise


def _check_server_run_task_dialog(server) -> None:
	# Pre-flight throws are the only branches we can exercise cheaply here;
	# any branch that survives the throw runs a real script and hits the
	# server. The happy path itself is covered by phase 7.

	# Unknown script with explicit dict variables → throw.
	with expect_validation_error("unknown script"):
		server.run_task_dialog(script="not-a-real-script.sh", variables={})

	# variables=None → defaults to {}, then unknown script throw.
	with expect_validation_error("unknown script"):
		server.run_task_dialog(script="not-a-real-script.sh", variables=None)

	# variables as a JSON string round-trips through json.loads → dict, then
	# unknown script throw. Drives the `isinstance(variables, str)` branch.
	with expect_validation_error("unknown script"):
		server.run_task_dialog(script="not-a-real-script.sh", variables='{"X": "1"}')

	# variables as a non-object (string-encoded list) → throw (covers the
	# non-dict guard after JSON parsing).
	with expect_validation_error("variables must"):
		server.run_task_dialog(script="not-a-real-script.sh", variables="[1, 2]")

	# variables_dict roundtrip through the JSON-string branch: the throw
	# above happens *after* the JSON parse, so the parse branch is already
	# covered. The `variables is None → {}` branch is exercised by the
	# `unknown script` call (variables= defaults via {} when missing).


def _check_server_get_scripts(server) -> None:
	scripts = server.get_scripts()
	assert isinstance(scripts, list) and scripts, scripts
	assert "bootstrap-server.sh" in scripts, scripts


def _check_server_bootstrap_status_guard(server) -> None:
	# Active is allowed (and is the path phase 3 covers); force an in-memory
	# Archived to hit the throw without flipping the row.
	server.reload()
	original_status = server.status
	server.status = "Archived"
	with expect_validation_error("cannot bootstrap"):
		server.bootstrap()
	server.status = original_status  # do not save — phase isolation only


def _check_virtual_machine_validation(server) -> None:
	image_doc = ensure_image_on_server(server.name)
	public_key = ephemeral_public_key()

	vm = frappe.get_doc({
		"doctype": "Virtual Machine",
		"description": "phase 8 negative-paths",
		"server": server.name,
		"image": image_doc.name,
		"vcpus": 1,
		"memory_megabytes": 512,
		"disk_gigabytes": 4,
		"ssh_public_key": public_key,
	}).insert(ignore_permissions=True)

	try:
		# start while Pending → throw.
		with expect_validation_error("cannot start"):
			vm.start()
		# stop while Pending → throw.
		with expect_validation_error("cannot stop"):
			vm.stop()
		# restart while Pending → throw.
		with expect_validation_error("cannot restart"):
			vm.restart()

		# provision happy path.
		vm.provision()
		vm.reload()
		assert vm.status == "Running"

		# provision again from Running → throw.
		with expect_validation_error("cannot provision"):
			vm.provision()

		# Immutability: mutate vcpus after insert.
		vm.vcpus = 99
		with expect_validation_error("immutable"):
			vm.save(ignore_permissions=True)
		vm.reload()

		# Call validate() directly on a freshly-loaded doc (no prior save
		# in this session) so the `if not original: return` early branch is
		# exercised.
		fresh_vm = frappe.get_doc("Virtual Machine", vm.name)
		fresh_vm.validate()  # must not raise

		# stop + start to exercise the start-from-Stopped branch.
		vm.stop()
		vm.reload()
		assert vm.status == "Stopped"
		# start while Stopped is the happy path.
		vm.start()
		vm.reload()
		assert vm.status == "Running"

		# set_status_default: the JSON schema sets status default = "Pending"
		# *before* `before_insert` runs, so the assignment in
		# `set_status_default` is dead in `insert()` flow. Call the helper
		# directly on an in-memory doc where we have cleared the field.
		vm_unset_status = frappe.get_doc({
			"doctype": "Virtual Machine",
			"description": "phase 8 unset status",
			"server": server.name,
			"image": image_doc.name,
			"vcpus": 1,
			"memory_megabytes": 512,
			"disk_gigabytes": 4,
			"ssh_public_key": public_key,
		})
		vm_unset_status.status = None
		vm_unset_status.set_status_default()
		assert vm_unset_status.status == "Pending"

		# Pre-derived mac/tap/ipv6 — covers the `if not self.x:` false branches
		# in before_validate.
		vm_pre_derived = frappe.get_doc({
			"doctype": "Virtual Machine",
			"description": "phase 8 pre-derived",
			"server": server.name,
			"image": image_doc.name,
			"vcpus": 1,
			"memory_megabytes": 512,
			"disk_gigabytes": 4,
			"ssh_public_key": public_key,
			"mac_address": "06:00:de:ad:be:ef",
			"tap_device": "atlas-deadbeef",
			"ipv6_address": "fd00::dead",
		}).insert(ignore_permissions=True)
		assert vm_pre_derived.mac_address == "06:00:de:ad:be:ef"
		assert vm_pre_derived.tap_device == "atlas-deadbeef"
		assert vm_pre_derived.ipv6_address == "fd00::dead"

		# Clean up the spare row + main vm.
		vm_pre_derived.status = "Terminated"
		vm_pre_derived.save(ignore_permissions=True)
		# vm_unset_status was never inserted (we only called the helper);
		# no DB cleanup needed.
	finally:
		# Terminate the main vm; ignore errors so failure paths don't leak.
		try:
			vm.reload()
			if vm.status != "Terminated":
				vm.terminate()
		except Exception:
			pass


def _check_virtual_machine_image_validation() -> None:
	# kernel_url not https → throw.
	with expect_validation_error("must be an https"):
		frappe.get_doc({
			"doctype": "Virtual Machine Image",
			"image_name": "phase8-bad-kernel",
			"kernel_url": "http://example.com/kernel",  # http, not https
			"kernel_filename": "k",
			"kernel_sha256": "0" * 64,
			"rootfs_url": "https://example.com/rootfs",
			"rootfs_filename": "r",
			"rootfs_sha256": "0" * 64,
			"default_disk_gigabytes": 1,
		}).insert(ignore_permissions=True)

	# rootfs_url not https → throw.
	with expect_validation_error("must be an https"):
		frappe.get_doc({
			"doctype": "Virtual Machine Image",
			"image_name": "phase8-bad-rootfs",
			"kernel_url": "https://example.com/kernel",
			"kernel_filename": "k",
			"kernel_sha256": "0" * 64,
			"rootfs_url": "ftp://example.com/rootfs",
			"rootfs_filename": "r",
			"rootfs_sha256": "0" * 64,
			"default_disk_gigabytes": 1,
		}).insert(ignore_permissions=True)

	# sync_to_all_servers returns one task per Active server.
	image = frappe.get_doc("Virtual Machine Image", "ubuntu-24.04")
	active_count = frappe.db.count("Server", filters={"status": "Active"})
	tasks = image.sync_to_all_servers()
	assert len(tasks) == active_count, (len(tasks), active_count)


def _check_run_task_unknown_script(server) -> None:
	"""run_task with a missing script raises (covers runner's generic
	exception finalize branch at lines 105-110)."""
	with expect_validation_error("not found"):
		run_task(
			server=server.name,
			script="phase8-unknown-script.sh",
			variables={"X": "1"},
			timeout_seconds=10,
		)
	# Task row should be finalized as Failure (not stuck Running/Pending).
	task = frappe.get_last_doc("Task", filters={"script": "phase8-unknown-script.sh"})
	assert task.status == "Failure", task.status


def _check_run_task_timeout(server) -> None:
	"""run_task whose remote script outruns the timeout raises and finalizes
	Failure (covers runner.py `subprocess.TimeoutExpired` handler 102-104)."""
	with expect_validation_error("timed out"):
		run_task(
			server=server.name,
			script="phase8-sleep.sh",
			variables={},
			timeout_seconds=2,
		)
	task = frappe.get_last_doc("Task", filters={"script": "phase8-sleep.sh"})
	assert task.status == "Failure", task.status
	assert "timed out" in (task.stderr or "").lower(), task.stderr


def _check_run_task_failure(server) -> None:
	"""run_task whose remote script exits non-zero raises and finalizes Failure."""
	with expect_validation_error("exited"):
		run_task(
			server=server.name,
			script="phase1-fail.sh",
			variables={},
			timeout_seconds=10,
		)
	task = frappe.get_last_doc("Task", filters={"script": "phase1-fail.sh"})
	assert task.status == "Failure", task.status
	assert task.exit_code == 7, task.exit_code


def _check_networking_helpers() -> None:
	"""Pure-Python helpers: cheap to exercise, expensive to leave uncovered."""
	from atlas.atlas.networking import (
		carve_virtual_machine_range,
		derive_mac,
		derive_tap,
	)

	# carve_virtual_machine_range: /64 -> first /124.
	cidr = carve_virtual_machine_range("2604:a880:cad:d0::/64")
	assert cidr.endswith("/124"), cidr

	# derive_mac and derive_tap are deterministic on UUID input.
	sample_uuid = "550e8400-e29b-41d4-a716-446655440000"
	mac = derive_mac(sample_uuid)
	assert mac.startswith("06:00:"), mac
	tap = derive_tap(sample_uuid)
	assert tap.startswith("atlas-") and len(tap) == 15, tap


def _check_ipv6_exhaustion(server) -> None:
	"""Fill a transient server's /124 to drive the `No IPv6 capacity` raise.

	A /124 holds 14 usable addresses (skipping ::0 and ::1). We use a fake
	server name that doesn't collide with the real e2e server so we don't
	break parallel runs.
	"""
	from atlas.atlas.networking import allocate_ipv6

	# Use a transient Server with a synthetic /124 range, no SSH state.
	fake_name = "phase8-ipv6-exhaust"
	if frappe.db.exists("Server", fake_name):
		# Clean any leftover VMs from a previous failed run.
		for vm in frappe.get_all(
			"Virtual Machine", filters={"server": fake_name}, pluck="name"
		):
			frappe.delete_doc("Virtual Machine", vm, force=True, ignore_permissions=True)
		frappe.delete_doc("Server", fake_name, force=True, ignore_permissions=True)

	frappe.get_doc({
		"doctype": "Server",
		"server_name": fake_name,
		"provider": server.provider,
		"status": "Pending",
		"ipv4_address": "192.0.2.99",
		"ipv6_address": "2001:db8::1",
		"ipv6_prefix": "2001:db8::/64",
		"ipv6_virtual_machine_range": "2001:db8::/124",
	}).insert(ignore_permissions=True)
	frappe.db.commit()

	# Fill the /124: 14 usable addresses. Each VM must stay non-Terminated
	# to count as occupying its address (Terminated VMs release the address).
	try:
		allocated = set()
		for _ in range(14):
			address = allocate_ipv6(fake_name)
			allocated.add(address)
			frappe.get_doc({
				"doctype": "Virtual Machine",
				"server": fake_name,
				"image": "ubuntu-24.04",
				"vcpus": 1,
				"memory_megabytes": 256,
				"disk_gigabytes": 1,
				"ssh_public_key": "ssh-rsa AAA",
				"ipv6_address": address,
				"status": "Running",
			}).insert(ignore_permissions=True)
		# The 15th allocation has no candidates left.
		with expect_validation_error("no ipv6 capacity"):
			allocate_ipv6(fake_name)
	finally:
		for vm in frappe.get_all(
			"Virtual Machine", filters={"server": fake_name}, pluck="name"
		):
			frappe.delete_doc("Virtual Machine", vm, force=True, ignore_permissions=True)
		frappe.delete_doc("Server", fake_name, force=True, ignore_permissions=True)
		frappe.db.commit()
