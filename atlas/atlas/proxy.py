"""Proxy control plane — Atlas reconciles each proxy guest's live map (and pushes
the wildcard cert) over SSH-to-the-guest.

Atlas is the source of truth; each proxy VM's `lua_shared_dict` is a cache
(spec principle #2). This module is the controller side of the design's §7:
build the desired regional map from the `Subdomain` rows, serialize it the SAME
canonical way the guest's persist.lua does (so "in sync?" is a byte compare),
SSH into each proxy guest, read its live `/map` off the unix-socket admin API,
and bulk-`/sync` the full map on drift. Cert push uses the same guest-SSH path.

This is NOT a host Task (which stages a script onto a Server and runs it there):
it runs on the controller and SSHes *into the guest* (the second SSH target,
`connection_for_guest`). Each guest operation is still recorded as a Task row for
the operator's audit trail, with a synthetic script name (`proxy-sync` /
`proxy-push-cert`) and the proxy VM in `virtual_machine`.
"""

import json
import shlex

import frappe

from atlas.atlas._ssh.transport import run_ssh, ssh_key_file
from atlas.atlas.doctype.subdomain.subdomain import map_for_region
from atlas.atlas.ssh import connection_for_guest

ADMIN_SOCKET = "/run/atlas-proxy/admin.sock"
CERT_DIRECTORY = "/var/lib/atlas-proxy/certs"
# The guest admin API answers HTTP over the unix socket; the host part is ignored
# but curl needs one, so use a fixed placeholder.
ADMIN_BASE = "http://localhost"


def canonical_json(site_map: dict[str, str]) -> str:
	"""The one canonical serialization of a subdomain→address map, byte-identical
	to the guest's persist.lua output: sorted keys, 2-space indent, one key per
	line, trailing newline. Because both sides emit the same bytes, the reconcile
	"in sync?" check is a plain string compare — no semantic diff (design §4.3,
	§7.2)."""
	return json.dumps(site_map, sort_keys=True, indent=2) + "\n"


def reconcile_region(region: str) -> list[str]:
	"""Reconcile every proxy VM in `region` to the region's desired map. Returns
	the names of the proxy VMs that were synced (drifted). Each proxy holds the
	WHOLE regional map (design §1 non-goals), so they all get the same body.

	A proxy that can't be reached is recorded as a failed Task and skipped — the
	other proxies still serve, so one wedged guest never wedges the loop (§7.3)."""
	desired_json = canonical_json(map_for_region(region))
	synced = []
	for vm_name in _proxy_vms_in_region(region):
		try:
			if _reconcile_proxy(vm_name, desired_json):
				synced.append(vm_name)
		except Exception as exception:
			# Record the failure on the Task row (done inside _reconcile_proxy's
			# guest-task wrapper) and move to the next proxy. Don't abort the loop.
			frappe.log_error(f"Proxy reconcile failed for {vm_name}: {exception}", "Proxy reconcile")
	return synced


def reconcile_proxy(virtual_machine: str) -> bool:
	"""Reconcile a single proxy VM to its region's desired map. Returns True iff a
	sync was needed (the live map had drifted). The region is read off the VM."""
	region = frappe.db.get_value("Virtual Machine", virtual_machine, "region")
	if not region:
		frappe.throw(f"Virtual Machine {virtual_machine} has no region; not a proxy")
	desired_json = canonical_json(map_for_region(region))
	return _reconcile_proxy(virtual_machine, desired_json)


def _reconcile_proxy(virtual_machine: str, desired_json: str) -> bool:
	vm = frappe.get_doc("Virtual Machine", virtual_machine)
	connection = connection_for_guest(vm)
	with ssh_key_file(connection.ssh_private_key) as key_path:
		# 1. Read the live map. The admin API serves the SAME canonical bytes, so
		#    the compare below is exact.
		live_json, _stderr, _code = run_ssh(
			connection, key_path, _curl_command("GET", "/map"), timeout_seconds=60
		)
		if live_json == desired_json:
			return False
		# 2. Drift: bulk declarative /sync the full desired map (idempotent,
		#    self-healing, rebuild-safe — design §7.2). Stream the body to the
		#    guest curl's stdin (--data-binary @-), no file staged on the guest.
		stdout, stderr, code = run_ssh(
			connection,
			key_path,
			_curl_command("POST", "/sync", data_stdin=True),
			timeout_seconds=120,
			stdin=desired_json,
		)
	_record_guest_task(virtual_machine, "proxy-sync", {"region": vm.region}, stdout, stderr, code)
	if code != 0:
		frappe.throw(f"Proxy sync to {virtual_machine} failed (exit {code}): {stderr[-500:]}")
	return True


def push_cert(virtual_machine: str, fullchain: str, privkey: str) -> None:
	"""Push the regional wildcard cert into a proxy guest and reload nginx.

	Drops fullchain.pem/privkey.pem into the guest's per-region cert dir over the
	same guest-SSH path as the map sync, then reloads (a reload is fine here —
	cert changes are rare, unlike map changes; design §7.3). The cert is pushed,
	never baked into the image, so one proxy image serves any region and a renewed
	cert is a re-push, not a rebuild (§5.3)."""
	vm = frappe.get_doc("Virtual Machine", virtual_machine)
	region = vm.region
	if not region:
		frappe.throw(f"Virtual Machine {virtual_machine} has no region; not a proxy")
	connection = connection_for_guest(vm)
	cert_dir = f"{CERT_DIRECTORY}/{region}"
	with ssh_key_file(connection.ssh_private_key) as key_path:
		# Write both PEMs and reload in one round trip. The key is 0600; the dir is
		# created first. `tee` writes from stdin so the private key never lands in
		# a process argv (which `ps` could read). Two tees → two stdin streams, so
		# do them as separate commands but in one SSH session via `&&`.
		_write_guest_file(
			connection, key_path, f"{cert_dir}/fullchain.pem", fullchain, mode="0644", make_dir=cert_dir
		)
		_write_guest_file(connection, key_path, f"{cert_dir}/privkey.pem", privkey, mode="0600")
		stdout, stderr, code = run_ssh(
			connection,
			key_path,
			"/opt/atlas-proxy/sbin/nginx -s reload",
			timeout_seconds=60,
		)
	_record_guest_task(virtual_machine, "proxy-push-cert", {"region": region}, stdout, stderr, code)
	if code != 0:
		frappe.throw(f"Cert push/reload to {virtual_machine} failed (exit {code}): {stderr[-500:]}")


def _write_guest_file(
	connection, key_path, path: str, content: str, mode: str, make_dir: str | None = None
) -> None:
	"""Write `content` to `path` in the guest via `tee` (content arrives on stdin,
	never in argv), then chmod. Optionally mkdir -p the parent first."""
	quoted = shlex.quote(path)
	command = ""
	if make_dir:
		command += f"mkdir -p {shlex.quote(make_dir)} && "
	command += f"tee {quoted} >/dev/null && chmod {mode} {quoted}"
	_stdout, stderr, code = run_ssh(connection, key_path, command, timeout_seconds=60, stdin=content)
	if code != 0:
		frappe.throw(f"Writing {path} to guest failed (exit {code}): {stderr[-300:]}")


def _curl_command(method: str, path: str, data_stdin: bool = False) -> str:
	"""Build the guest-side `curl --unix-socket` invocation. With data_stdin the
	body is read from the SSH stdin stream (--data-binary @-)."""
	parts = [
		"curl",
		"-s",
		"--fail-with-body",
		"--unix-socket",
		ADMIN_SOCKET,
		"-X",
		method,
	]
	if data_stdin:
		parts += ["--data-binary", "@-"]
	parts.append(f"{ADMIN_BASE}{path}")
	return " ".join(shlex.quote(p) for p in parts)


def _proxy_vms_in_region(region: str) -> list[str]:
	"""Every VM marked is_proxy in the region. These are the reconcile targets;
	each gets the full regional map."""
	return frappe.get_all(
		"Virtual Machine",
		filters={"is_proxy": 1, "region": region},
		pluck="name",
	)


def _record_guest_task(
	virtual_machine: str, script: str, variables: dict, stdout: str, stderr: str, exit_code: int
) -> None:
	"""Record one guest-SSH operation as a Task row for the operator's audit
	trail. Unlike host Tasks this isn't a staged script — the `script` is a
	synthetic name and there are no uploads — but the row shape (status, output,
	exit code) is identical, so the operator sees proxy reconciles in the same
	Task list as every other action."""
	task = frappe.get_doc(
		{
			"doctype": "Task",
			"server": frappe.db.get_value("Virtual Machine", virtual_machine, "server"),
			"virtual_machine": virtual_machine,
			"script": script,
			"status": "Success" if exit_code == 0 else "Failure",
			"triggered_by": frappe.session.user if frappe.session else "Administrator",
			"stdout": stdout,
			"stderr": stderr,
			"exit_code": exit_code,
			"ended": frappe.utils.now_datetime(),
		}
	)
	task.variables_dict = variables
	task.insert(ignore_permissions=True)
