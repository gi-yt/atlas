"""Unit tests for the TLS provider registry — twin of
`atlas/atlas/providers/test_registry.py`."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas import tls
from atlas.atlas.dns.base import DnsProvider
from atlas.atlas.tls.base import AuthResult, IssuedCert, TlsProvider


class _StubTlsProvider(TlsProvider):
	provider_type = "Stub"

	def authenticate(self) -> AuthResult:
		return AuthResult(ok=True, account_label="stub")

	def issue(self, domain: str, dns_provider: DnsProvider) -> IssuedCert:
		return IssuedCert(
			fullchain_path="/tmp/fullchain.pem",
			privkey_path="/tmp/privkey.pem",
			not_before="2026-01-01 00:00:00",
			not_after="2026-04-01 00:00:00",
		)


class TestTlsProviderRegistry(IntegrationTestCase):
	def setUp(self) -> None:
		tls._REGISTRY["Stub"] = _StubTlsProvider

	def tearDown(self) -> None:
		tls._REGISTRY.pop("Stub", None)

	def test_for_tls_provider_instantiates_active_class(self) -> None:
		row = SimpleNamespace(is_active=1, provider_type="Stub", name="letsencrypt-prod")
		with (
			patch.object(tls, "_load_implementations", lambda: None),
			patch.object(frappe, "get_doc", return_value=row),
		):
			instance = tls.for_tls_provider("letsencrypt-prod")
		self.assertIsInstance(instance, _StubTlsProvider)

	def test_for_tls_provider_throws_on_archived(self) -> None:
		row = SimpleNamespace(is_active=0, provider_type="Stub", name="letsencrypt-prod")
		with (
			patch.object(tls, "_load_implementations", lambda: None),
			patch.object(frappe, "get_doc", return_value=row),
		):
			with self.assertRaises(frappe.ValidationError) as raised:
				tls.for_tls_provider("letsencrypt-prod")
		self.assertIn("archived", str(raised.exception))

	def test_for_tls_provider_throws_on_unknown_type(self) -> None:
		row = SimpleNamespace(is_active=1, provider_type="Unregistered", name="x")
		with (
			patch.object(tls, "_load_implementations", lambda: None),
			patch.object(frappe, "get_doc", return_value=row),
		):
			with self.assertRaises(frappe.ValidationError) as raised:
				tls.for_tls_provider("x")
		self.assertIn("No implementation", str(raised.exception))

	def test_real_implementations_register(self) -> None:
		"""The three shipped issuers resolve their provider_type keys."""
		tls._load_implementations()
		for provider_type in ("Let's Encrypt", "Self-Managed", "ZeroSSL"):
			self.assertIn(provider_type, tls._REGISTRY)
