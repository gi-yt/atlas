"""WireGuard key material, minted in the controller.

The VPN broker (spec/19-vpn-broker.md) terminates each tunnel on the host with a
**host-side** keypair. We mint it here — X25519 via `cryptography`, encoded in
WireGuard's base64 form — so the keypair is created where Frappe is the source of
truth (stored on the `VPN Tunnel` row, the private half encrypted) and pushed to
the host, rather than generated on the host and scraped back (the principle-#2
rule the Reserved IP anchor is the rare exception to).

The client mints its OWN keypair and sends only its public key, so Atlas never
holds a client private key; `is_valid_public_key` guards that input at the API
boundary. Pure and host-free: unit-testable with bare `cryptography`, no Frappe,
no SSH.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
	Encoding,
	NoEncryption,
	PrivateFormat,
	PublicFormat,
)

# A WireGuard key is a 32-byte value rendered in standard base64 — 44 chars,
# '='-padded, exactly as `wg genkey` / `wg pubkey` emit it.
KEY_BYTES = 32
ENCODED_KEY_LENGTH = 44


@dataclass(frozen=True)
class WireGuardKeypair:
	"""A host-side WireGuard keypair, base64-encoded as `wg` emits it."""

	private_key: str
	public_key: str


def generate_keypair() -> WireGuardKeypair:
	"""Mint a fresh X25519 keypair in WireGuard base64 form."""
	private = X25519PrivateKey.generate()
	return WireGuardKeypair(
		private_key=_encode(private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())),
		public_key=_encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)),
	)


def public_key_for(private_key: str) -> str:
	"""Derive the public key for a base64 WireGuard private key. The inverse check
	for a keypair, and the way a host private key alone reconstructs its public
	half."""
	private = X25519PrivateKey.from_private_bytes(_decode(private_key))
	return _encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def is_valid_public_key(key: str) -> bool:
	"""True iff `key` is a syntactically valid WireGuard public key — a 32-byte
	value in standard base64. Guards the client-supplied public key at the API
	boundary so a malformed key fails loud in the controller, not on the host."""
	if not isinstance(key, str) or len(key) != ENCODED_KEY_LENGTH:
		return False
	try:
		# validate=True rejects non-alphabet characters rather than silently
		# discarding them (which could let a 44-char junk string decode short).
		return len(base64.b64decode(key, validate=True)) == KEY_BYTES
	except ValueError:
		# binascii.Error (bad base64) is a ValueError subclass.
		return False


def _encode(raw: bytes) -> str:
	return base64.standard_b64encode(raw).decode("ascii")


def _decode(key: str) -> bytes:
	return base64.b64decode(key, validate=True)
