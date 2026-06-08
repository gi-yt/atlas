"""Unit tests for the TLS Provider controller — immutability, archive, and the
authenticate delegation. Mirrors the compute `Provider` and Domain Provider tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas.tls.base import AuthResult


def _make(name="le-tp", provider_type="Let's Encrypt"):
	if frappe.db.exists("TLS Provider", name):
		frappe.delete_doc("TLS Provider", name, force=1, ignore_permissions=True)
	return frappe.get_doc(
		{"doctype": "TLS Provider", "provider_name": name, "provider_type": provider_type}
	).insert(ignore_permissions=True)


class TestTLSProvider(IntegrationTestCase):
	def test_provider_type_immutable_after_insert(self) -> None:
		tp = _make()
		tp.provider_type = "ZeroSSL"
		with self.assertRaises(frappe.ValidationError):
			tp.save(ignore_permissions=True)

	def test_archive_flips_is_active(self) -> None:
		tp = _make()
		tp.archive()
		self.assertFalse(frappe.db.get_value("TLS Provider", tp.name, "is_active"))

	def test_authenticate_delegates_to_tls_provider(self) -> None:
		tp = _make()
		fake = MagicMock()
		fake.authenticate.return_value = AuthResult(ok=True, account_label="ops@frappe.dev")
		with patch("atlas.atlas.doctype.tls_provider.tls_provider.tls.for_tls_provider", return_value=fake):
			result = tp.authenticate()
		self.assertTrue(result["ok"])
		self.assertEqual(result["account_label"], "ops@frappe.dev")
