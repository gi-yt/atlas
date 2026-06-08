"""TLS provider registry — twin of `atlas/atlas/providers/__init__.py`.

Issuers register their `TlsProvider` subclass via `@register`. Callers ask for an
instance via `for_tls_provider(provider_name)`, which looks up the `TLS Provider`
DocType row, checks `is_active`, and instantiates the registered class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe

if TYPE_CHECKING:
	from atlas.atlas.tls.base import TlsProvider


_REGISTRY: dict[str, type["TlsProvider"]] = {}


def register(cls: type["TlsProvider"]) -> type["TlsProvider"]:
	"""Class decorator that records `cls` against its `provider_type`."""
	_REGISTRY[cls.provider_type] = cls
	return cls


def for_tls_provider(provider_name: str) -> "TlsProvider":
	"""Return an instantiated `TlsProvider` for the given `TLS Provider` row.

	Raises `frappe.ValidationError` if the row is archived (`is_active=0`)
	or if its `provider_type` has no registered implementation.
	"""
	_load_implementations()
	row = frappe.get_doc("TLS Provider", provider_name)
	if not row.is_active:
		frappe.throw(f"TLS Provider {provider_name!r} is archived")
	factory = _REGISTRY.get(row.provider_type)
	if factory is None:
		frappe.throw(f"No implementation for provider_type {row.provider_type!r}")
	return factory()


def _load_implementations() -> None:
	"""Import issuer modules so their `@register` decorators run. Idempotent."""
	import atlas.atlas.tls.letsencrypt
	import atlas.atlas.tls.self_managed
	import atlas.atlas.tls.zerossl
