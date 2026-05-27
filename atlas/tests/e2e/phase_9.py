"""Phase 9 e2e: DigitalOcean client error and helper branches.

No droplets created. Exercises error paths on the DO API client and the
pure helpers `public_ipv4` / `public_ipv6` / `_network_cidr`.
"""

import time
import traceback

from atlas.atlas.digitalocean import (
	DigitalOceanError,
	_network_cidr,
	public_ipv4,
	public_ipv6,
)
from atlas.tests.e2e._shared import get_client


def run() -> None:
	start = time.monotonic()
	try:
		_check_account()
		_check_get_droplet_bogus()
		_check_delete_droplet_bogus_is_silent()
		_check_wait_for_active_times_out()
		_check_public_ipv4_missing()
		_check_public_ipv6_missing()
		_check_network_cidr_helper()
	except Exception:
		print(f"phase-9: FAIL in {time.monotonic() - start:.0f}s")
		traceback.print_exc()
		raise
	print(f"phase-9: OK in {time.monotonic() - start:.0f}s")


def _check_account() -> None:
	"""Cover the account() endpoint. Tokens scoped without the `account:read`
	right will 403 here — that still exercises the same code path, so we
	accept either an `account` dict or a DigitalOceanError on 403."""
	client = get_client()
	try:
		account = client.account()
		assert "email" in account or "uuid" in account, account
	except DigitalOceanError as exception:
		assert "403" in str(exception) or "forbidden" in str(exception).lower(), str(exception)


def _check_get_droplet_bogus() -> None:
	client = get_client()
	caught = False
	try:
		client.get_droplet(1)  # id=1 will not exist on this account
	except DigitalOceanError:
		caught = True
	assert caught, "get_droplet(1) should have raised DigitalOceanError"


def _check_delete_droplet_bogus_is_silent() -> None:
	# allow_404 path: deleting a non-existent id returns silently.
	client = get_client()
	client.delete_droplet(1)


def _check_wait_for_active_times_out() -> None:
	"""wait_for_active(<bogus>) raises. With a non-existent id, the inner
	get_droplet call raises 404 first; that still drives the entry path of
	wait_for_active and is the production behaviour. The internal timeout
	branch is exercised by a unit test (test_digitalocean.py) where the
	HTTP layer can be mocked."""
	client = get_client()
	caught = False
	try:
		client.wait_for_active(1, timeout_seconds=1)
	except DigitalOceanError:
		caught = True
	assert caught, "wait_for_active(1) should have raised"


def _check_public_ipv4_missing() -> None:
	caught = False
	try:
		public_ipv4({"id": 1, "networks": {"v4": []}})
	except DigitalOceanError:
		caught = True
	assert caught, "public_ipv4 with no v4 should have raised"

	# Also a droplet with v4 entries but none of type "public".
	caught = False
	try:
		public_ipv4({"id": 2, "networks": {"v4": [{"type": "private", "ip_address": "10.0.0.1"}]}})
	except DigitalOceanError:
		caught = True
	assert caught, "public_ipv4 with only private v4 should have raised"


def _check_public_ipv6_missing() -> None:
	caught = False
	try:
		public_ipv6({"id": 1, "networks": {"v6": []}})
	except DigitalOceanError:
		caught = True
	assert caught, "public_ipv6 with no v6 should have raised"


def _check_network_cidr_helper() -> None:
	# Exercises the helper directly (drives lines 130-131 even if a real
	# droplet roundtrip would also cover them).
	cidr = _network_cidr("2604:a880:cad:d0::1", 64)
	assert cidr.endswith("/64"), cidr
	assert cidr.startswith("2604:a880:cad:d0:"), cidr
