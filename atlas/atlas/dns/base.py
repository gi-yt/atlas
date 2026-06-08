"""DNS provider abstraction — the DNS-01 half of certificate issuance.

A `DnsProvider` knows how to prove control of a zone to an ACME server via the
DNS-01 challenge. Atlas never writes TXT records itself; it hands certbot the
provider's plugin flag (`certbot_args()`) and the vendor credentials as env
(`credential_env()`), and certbot's DNS plugin does the record dance. The seam
mirrors the compute `Provider` ABC (`atlas/atlas/providers/base.py`): callers ask
`for_domain_provider(name)` for an instance and never branch on `provider_type`.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import ClassVar


@dataclasses.dataclass(frozen=True, slots=True)
class AuthResult:
	"""Outcome of a credential check — twin of the compute `AuthResult`, trimmed
	to what a DNS account exposes."""

	ok: bool
	account_label: str | None = None
	error: str | None = None


class DnsProvider(ABC):
	provider_type: ClassVar[str]

	@abstractmethod
	def authenticate(self) -> AuthResult:
		"""Verify the credentials can reach the zone (Route 53: GetHostedZone).
		Backs the Domain Provider's **Test Connection** button."""
		...

	@abstractmethod
	def credential_env(self) -> dict[str, str]:
		"""Vendor secrets as the environment certbot's DNS plugin reads (Route 53:
		`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`). Merged into the issue-cert
		subprocess env, never placed in argv (secrets must not show up in `ps`)."""
		...

	@abstractmethod
	def certbot_authenticator(self) -> str:
		"""The certbot DNS authenticator NAME for this vendor (Route 53: `route53`).
		The issue-cert script turns it into the plugin flag (`--dns-route53`); the
		name (never a `--`-prefixed token) is what crosses the typed-CLI boundary,
		so argparse can't mistake a value for an option. No credentials here — those
		go through `credential_env()`."""
		...
