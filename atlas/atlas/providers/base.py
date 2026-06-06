"""Provider abstraction.

Server lifecycle: authenticate / discover / provision / describe / destroy.
Reserved-IP lifecycle (the inbound-v4 primitive): allocate_reserved_ip /
assign_reserved_ip / unassign_reserved_ip / list_reserved_ips /
release_reserved_ip. Atlas talks to vendors only through this interface; the
indirection through `atlas.get_provider()` means callers never branch on
`provider_type`.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from enum import Enum
from typing import ClassVar


class Networking(Enum):
	IPV4_ONLY = "ipv4"
	IPV6_ONLY = "ipv6"
	DUAL_STACK = "dual"


@dataclasses.dataclass(frozen=True, slots=True)
class SshKey:
	# Vendor's handle for the key — whatever the provider's create-host call
	# expects to reference a pre-registered key (DigitalOcean: the key's id or
	# fingerprint; AWS: the KeyPair name).
	vendor_id: str | None = None
	# Body, for vendors that upload at provision-time.
	public_key: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ServerNetworking:
	ipv4_address: str | None
	ipv6_address: str | None
	ipv6_prefix: str | None
	ipv6_virtual_machine_range: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class ProvisionRequest:
	title: str
	size: str
	image: str
	ssh_key: SshKey
	networking: Networking = Networking.DUAL_STACK
	tags: tuple[str, ...] = ()
	cloud_init: str | None = None
	# Self-Managed only: operator-supplied networking comes through here.
	prebuilt_networking: ServerNetworking | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ProvisionResult:
	"""Returned by provision() and describe(). Often a partial."""

	provider_resource_id: str
	size: str
	image: str
	ready: bool
	networking: ServerNetworking | None = None
	provider_metadata: dict | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class SizeInfo:
	slug: str
	monthly_cost_usd: int | None
	provider_metadata: dict | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ImageInfo:
	slug: str
	provider_metadata: dict | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Capabilities:
	sizes: tuple[SizeInfo, ...]
	images: tuple[ImageInfo, ...]
	quota: dict | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ReservedIp:
	"""A public IPv4 reserved at the vendor, optionally bound to a host.

	`provider_resource_id` is the vendor's handle (on DigitalOcean the reserved
	IP is keyed by its address, so the two are equal there; other vendors may
	differ). `droplet_resource_id` is the provider_resource_id of the *Server*
	(droplet) the IP is currently assigned to, or None if it's floating —
	enough for `discover()` to map a vendor reserved IP back to a Server row."""

	ip_address: str
	provider_resource_id: str
	droplet_resource_id: str | None = None
	provider_metadata: dict | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class AuthResult:
	ok: bool
	account_label: str | None = None
	rate_limit: int | None = None
	rate_remaining: int | None = None
	missing_scopes: tuple[str, ...] = ()
	error: str | None = None


class Provider(ABC):
	provider_type: ClassVar[str]

	@abstractmethod
	def authenticate(self) -> AuthResult: ...

	@abstractmethod
	def discover(self) -> Capabilities:
		"""Return the vendor's current catalog. Callers upsert `Provider Size`
		/ `Provider Image` rows; slugs missing from the result get disabled."""
		...

	@abstractmethod
	def provision(self, request: ProvisionRequest) -> ProvisionResult:
		"""Allocate the vendor resource. Must return within 30s. `ready=False`
		is fine; `describe()` fills the rest."""
		...

	@abstractmethod
	def describe(self, provider_resource_id: str) -> ProvisionResult:
		"""Read-only, pollable. Authoritative source for Server fields after
		provision. `ready=True` means networking is fully populated."""
		...

	@abstractmethod
	def destroy(self, provider_resource_id: str) -> None:
		"""Release the vendor resource. Idempotent. Called from
		`Server.archive()`."""
		...

	# --- Reserved IPs (the inbound-v4 primitive) -------------------------
	# A reserved IP is allocated to a region, assigned to the *droplet*
	# (host), and host-side 1:1-NATed to the guest by a later Task. The
	# provider owns only the vendor object; the Frappe invariant (one IP, one
	# VM, same Server) lives in `Reserved IP.attach()`/`detach()`.

	@abstractmethod
	def allocate_reserved_ip(self) -> ReservedIp:
		"""Reserve a new public IPv4 in the provider's region, unassigned. The
		caller writes a `Reserved IP` row from the result and assigns it on
		attach. Atlas is single-region, so the provider sources the region."""
		...

	@abstractmethod
	def assign_reserved_ip(self, provider_resource_id: str, droplet_resource_id: str) -> None:
		"""Bind the reserved IP to a Server (droplet). Idempotent — a no-op if
		already assigned there."""
		...

	@abstractmethod
	def unassign_reserved_ip(self, provider_resource_id: str) -> None:
		"""Release the reserved IP from its droplet, leaving it allocated to the
		region. Idempotent."""
		...

	@abstractmethod
	def list_reserved_ips(self) -> tuple[ReservedIp, ...]:
		"""Every reserved IP the account holds, for discover/import. Maps a
		vendor reserved IP back to a Server via `droplet_resource_id`."""
		...

	@abstractmethod
	def release_reserved_ip(self, provider_resource_id: str) -> None:
		"""Destroy the reserved IP at the vendor. Idempotent. Called when a
		`Reserved IP` row is deleted from the pool."""
		...
