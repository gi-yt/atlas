"""Unit tests for the Root Domain controller — autoname, immutability, the
`*.<domain>` derivation, and the issue_certificate orchestration (find-or-create
the single TLS Certificate, then delegate issuance to it)."""

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas.doctype.tls_certificate import tls_certificate as cert_module


def _ensure_providers() -> None:
	if not frappe.db.exists("Domain Provider", "route53-test"):
		frappe.get_doc(
			{"doctype": "Domain Provider", "provider_name": "route53-test", "provider_type": "Route53"}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("TLS Provider", "letsencrypt-test"):
		frappe.get_doc(
			{"doctype": "TLS Provider", "provider_name": "letsencrypt-test", "provider_type": "Let's Encrypt"}
		).insert(ignore_permissions=True)


def _make(domain: str, region: str):
	_ensure_providers()
	return frappe.get_doc(
		{
			"doctype": "Root Domain",
			"domain": domain,
			"region": region,
			"domain_provider": "route53-test",
			"tls_provider": "letsencrypt-test",
		}
	).insert(ignore_permissions=True)


class TestRootDomain(IntegrationTestCase):
	def setUp(self) -> None:
		for name in frappe.get_all("TLS Certificate", pluck="name"):
			frappe.delete_doc("TLS Certificate", name, force=1, ignore_permissions=True)
		for name in frappe.get_all("Root Domain", pluck="name"):
			frappe.delete_doc("Root Domain", name, force=1, ignore_permissions=True)

	def test_autoname_is_the_domain(self) -> None:
		domain = _make("blr1.frappe.dev", "blr1")
		self.assertEqual(domain.name, "blr1.frappe.dev")

	def test_common_name_is_wildcard(self) -> None:
		domain = _make("nyc3.frappe.dev", "nyc3")
		self.assertEqual(domain.common_name, "*.nyc3.frappe.dev")

	def test_domain_and_region_immutable_after_insert(self) -> None:
		domain = _make("blr1.frappe.dev", "blr1")
		domain.region = "nyc3"
		with self.assertRaises(frappe.ValidationError):
			domain.save(ignore_permissions=True)

	def test_issue_certificate_creates_and_delegates(self) -> None:
		domain = _make("blr1.frappe.dev", "blr1")
		with patch.object(cert_module.TLSCertificate, "issue", lambda self: None) as _:
			cert_name = domain.issue_certificate()
		cert = frappe.get_doc("TLS Certificate", cert_name)
		self.assertEqual(cert.root_domain, "blr1.frappe.dev")
		self.assertEqual(cert.tls_provider, "letsencrypt-test")

	def test_issue_certificate_reuses_existing_cert(self) -> None:
		domain = _make("blr1.frappe.dev", "blr1")
		with patch.object(cert_module.TLSCertificate, "issue", lambda self: None):
			first = domain.issue_certificate()
			second = domain.issue_certificate()
		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count("TLS Certificate", {"root_domain": "blr1.frappe.dev"}), 1)
