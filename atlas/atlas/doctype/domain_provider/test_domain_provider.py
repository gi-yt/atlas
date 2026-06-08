"""Unit tests for the Domain Provider controller — immutability, archive, and the
authenticate delegation. Mirrors the compute `Provider` tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas.dns.base import AuthResult


def _make(name="route53-dp", provider_type="Route53"):
	if frappe.db.exists("Domain Provider", name):
		frappe.delete_doc("Domain Provider", name, force=1, ignore_permissions=True)
	return frappe.get_doc(
		{"doctype": "Domain Provider", "provider_name": name, "provider_type": provider_type}
	).insert(ignore_permissions=True)


class TestDomainProvider(IntegrationTestCase):
	def test_provider_type_immutable_after_insert(self) -> None:
		dp = _make()
		dp.provider_type = "Cloudflare"
		with self.assertRaises(frappe.ValidationError):
			dp.save(ignore_permissions=True)

	def test_archive_flips_is_active(self) -> None:
		dp = _make()
		dp.archive()
		self.assertFalse(frappe.db.get_value("Domain Provider", dp.name, "is_active"))
		# Archiving twice is refused.
		dp.reload()
		with self.assertRaises(frappe.ValidationError):
			dp.archive()

	def test_authenticate_delegates_to_dns_provider(self) -> None:
		dp = _make()
		fake = MagicMock()
		fake.authenticate.return_value = AuthResult(ok=True, account_label="example.com")
		with patch(
			"atlas.atlas.doctype.domain_provider.domain_provider.dns.for_domain_provider", return_value=fake
		):
			result = dp.authenticate()
		self.assertTrue(result["ok"])
		self.assertEqual(result["account_label"], "example.com")
