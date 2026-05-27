"""End-to-end tests for Atlas.

`run_all()` is the cheap regression entry point: one shared droplet, every
phase that takes a server runs against it, and the droplet is cleaned up
when the last phase exits. Phases 2 and 3 still own their dedicated-droplet
flows (DO client smoke test, fresh-provision); they are not run by
`run_all` to keep the cost at exactly one billable droplet.
"""

import time
import traceback

from atlas.tests.e2e import (
	phase_4,
	phase_5,
	phase_6,
	phase_7,
	phase_8,
	phase_9,
	phase_10,
	phase_11,
)
from atlas.tests.e2e._shared import (
	cleanup_droplet,
	ensure_bootstrapped_server,
	get_client,
	sweep_old_droplets,
)


def run_all() -> None:
	"""Run every phase that takes a Server against one shared droplet.

	The droplet is created once (or reused if an Active+reachable one already
	exists), every phase runs against it with `keep=True`, and the last phase
	flips `keep=False` so the `finally` block deletes it.

	Phases 1 and 2 (SSH primitive in isolation; DigitalOcean client smoke
	test) are not orchestrated here — they own their own droplet semantics.
	"""
	overall_start = time.monotonic()
	client = get_client()
	sweep_old_droplets(client)

	server, _client, created_now = ensure_bootstrapped_server(reuse=True, keep=True)

	phases = [
		("phase-4 (image sync)", phase_4.run),
		("phase-5 (vm provision)", phase_5.run),
		("phase-6 (vm lifecycle)", phase_6.run),
		("phase-7 (run task + reboot)", phase_7.run),
		("phase-8 (validation paths)", phase_8.run),
		("phase-10 (sync background)", phase_10.run),
		("phase-11 (ssh transport + bootstrap)", phase_11.run),
	]

	results: list[tuple[str, str, float]] = []
	try:
		for label, runner in phases:
			phase_start = time.monotonic()
			try:
				runner(reuse=True, keep=True)
				results.append((label, "OK", time.monotonic() - phase_start))
			except Exception:
				results.append((label, "FAIL", time.monotonic() - phase_start))
				traceback.print_exc()
				break
	finally:
		if created_now and server.provider_resource_id:
			cleanup_droplet(client, int(server.provider_resource_id))

	total = time.monotonic() - overall_start
	print("")
	print("=" * 60)
	for label, outcome, seconds in results:
		print(f"{label:<32} {outcome} in {seconds:.0f}s")
	print(f"Total: {total:.0f}s. One droplet used{' + cleaned up' if created_now else ' (reused)'}.")
	print("=" * 60)

	failed = [label for label, outcome, _ in results if outcome != "OK"]
	if failed:
		raise AssertionError(f"failures: {', '.join(failed)}")


def run_all_coverage() -> None:
	"""Run everything that contributes to e2e coverage in a single bench call.

	Cost: three billable droplets. The shared droplet (used by phases 4-7,
	10, 11), the phase-2 throwaway, and the phase-3 fresh-provision. Phase 3
	is the only path that exercises `Server Provider.provision_server` and
	`finish_provisioning`, so it must run if those modules are to be covered.
	"""
	from atlas.tests.e2e import phase_2, phase_3

	# Phase 9 needs no droplet at all; run it first so a transient DO outage
	# fails fast before we burn an hour bootstrapping.
	print("--- phase 9 (DO client error paths) ---")
	phase_9.run()

	# Phase 2 uses its own droplet.
	print("--- phase 2 (DO smoke test) ---")
	phase_2.run()

	# Phase 3 also uses its own droplet (fresh provision).
	print("--- phase 3 (fresh server provision) ---")
	phase_3.run()

	# The shared-droplet phases.
	run_all()
