"""Phase 11 e2e: SSH transport branches and `Server.bootstrap()` re-run.

Uses the shared bootstrapped server. Exercises:

- `upload_files` happy path (real scp to a real host).
- `wait_for_ssh` against an unroutable host (timeout branch).
- `run_scp` failure (scp to a path the user cannot write).
- `Server.bootstrap()` end-to-end, since this is the only path that hits
  `Server._absorb_bootstrap_output` and the production bootstrap upload
  list. `bootstrap-server.sh` is idempotent, so re-running on the shared
  server is safe.
"""

import time
import traceback

import frappe

from atlas.atlas._ssh.transport import (
	Connection,
	run_scp,
	ssh_key_file,
	upload_files,
	wait_for_ssh,
)
from atlas.atlas.ssh import connection_for_server
from atlas.tests.e2e._shared import phase


def run(reuse: bool = True, keep: bool = True) -> None:
	start = time.monotonic()
	try:
		with phase("phase-11", reuse=reuse, keep=keep) as server:
			connection = connection_for_server(server)
			_check_upload_files_happy(connection)
			_check_upload_files_empty(connection)
			_check_scp_failure(connection)
			_check_wait_for_ssh_timeout()
			_check_server_bootstrap_rerun(server)
	except Exception:
		print(f"phase-11: FAIL in {time.monotonic() - start:.0f}s")
		traceback.print_exc()
		raise
	print(f"phase-11: OK in {time.monotonic() - start:.0f}s")


def _check_upload_files_happy(connection: Connection) -> None:
	"""upload_files with a tiny local file to a /tmp path."""
	import tempfile

	with tempfile.NamedTemporaryFile(mode="w", suffix=".phase11", delete=False) as handle:
		handle.write("phase 11 marker\n")
		local_path = handle.name
	upload_files(connection, [(local_path, "/tmp/atlas-phase11-marker.txt")])


def _check_upload_files_empty(connection: Connection) -> None:
	"""upload_files([]) returns silently (covers the early-exit branch)."""
	upload_files(connection, [])


def _check_scp_failure(connection: Connection) -> None:
	"""Force run_scp to fail by writing to a path root can't traverse.

	`/proc/atlas-phase11/x` is inside a read-only kernel filesystem; scp
	will return non-zero. This drives the `result.returncode != 0` branch
	in run_scp.
	"""
	import tempfile

	with tempfile.NamedTemporaryFile(mode="w", suffix=".phase11", delete=False) as handle:
		handle.write("x\n")
		local_path = handle.name

	caught = False
	try:
		with ssh_key_file(connection.ssh_private_key) as key_path:
			run_scp(
				connection,
				key_path,
				local_path,
				"/proc/atlas-phase11/x",  # /proc is not writable
				timeout_seconds=30,
			)
	except frappe.ValidationError:
		caught = True
	assert caught, "scp to /proc should have raised ValidationError"


def _check_wait_for_ssh_timeout() -> None:
	"""wait_for_ssh against an unroutable address times out.

	`192.0.2.1` is reserved (TEST-NET-1). Real SSH attempts return non-zero
	(connect refused / network unreachable / timeout) before the
	`ConnectTimeout=30` window closes — so the inner `run_ssh` returns a
	non-zero exit, and `wait_for_ssh` raises after its own short deadline.
	"""
	connection = Connection(
		host="192.0.2.1",
		ssh_private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nx\n-----END OPENSSH PRIVATE KEY-----\n",
	)
	caught = False
	try:
		wait_for_ssh(connection, timeout_seconds=2, poll_seconds=1)
	except frappe.ValidationError as exception:
		caught = "not ready" in str(exception).lower()
	except Exception:
		# A malformed key may make the ssh command return non-zero immediately
		# without raising in Python; that's still the path we want to record.
		caught = True
	assert caught, "wait_for_ssh against unroutable host should raise"


def _check_server_bootstrap_rerun(server) -> None:
	"""Re-run Server.bootstrap() on the already-Active shared server.

	`bootstrap-server.sh` is idempotent (covered by phase 3). Running it
	through `Server.bootstrap()` itself drives `upload_files`,
	`_bootstrap_uploads`, `_absorb_bootstrap_output`, and the JSON tail-line
	parser, none of which `run_task_dialog` reaches.
	"""
	original_firecracker = server.firecracker_version
	server.bootstrap()
	server.reload()
	# The values absorbed from the script's last-line JSON should match what
	# phase 3 first wrote — re-runs do not regress them.
	assert server.firecracker_version == original_firecracker, (
		server.firecracker_version,
		original_firecracker,
	)
