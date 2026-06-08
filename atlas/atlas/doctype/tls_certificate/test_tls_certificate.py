"""Unit tests for the TLS Certificate controller — the cert→proxy wiring, the
status machine, common_name derivation, and the renewal window.

The two external edges are mocked: the TLS provider's `issue()` (so no certbot)
and `proxy.push_cert` (so no SSH). What's left — the controller's own logic — is
fully exercised: it resolves the domain's providers, records the result, and fans
the PEMs out to exactly the proxy VMs in the domain's region.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas.doctype.tls_certificate import tls_certificate as module
from atlas.atlas.doctype.virtual_machine.test_virtual_machine import (
	_ensure_test_image,
	_ensure_test_server,
	_new_vm,
)
from atlas.atlas.tls.base import IssuedCert


def _ensure_providers() -> None:
	if not frappe.db.exists("Domain Provider", "route53-test"):
		frappe.get_doc(
			{"doctype": "Domain Provider", "provider_name": "route53-test", "provider_type": "Route53"}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("TLS Provider", "letsencrypt-test"):
		frappe.get_doc(
			{"doctype": "TLS Provider", "provider_name": "letsencrypt-test", "provider_type": "Let's Encrypt"}
		).insert(ignore_permissions=True)


def _make_domain(domain: str, region: str):
	_ensure_providers()
	if frappe.db.exists("Root Domain", domain):
		return frappe.get_doc("Root Domain", domain)
	return frappe.get_doc(
		{
			"doctype": "Root Domain",
			"domain": domain,
			"region": region,
			"domain_provider": "route53-test",
			"tls_provider": "letsencrypt-test",
		}
	).insert(ignore_permissions=True)


def _purge_vms() -> None:
	for name in frappe.get_all("Virtual Machine", pluck="name"):
		frappe.delete_doc("Virtual Machine", name, force=1, ignore_permissions=True)


class TestCommonName(IntegrationTestCase):
	def setUp(self) -> None:
		_make_domain("blr1.frappe.dev", "blr1")

	def test_common_name_is_wildcard_of_domain(self) -> None:
		cert = frappe.get_doc({"doctype": "TLS Certificate", "root_domain": "blr1.frappe.dev"}).insert(
			ignore_permissions=True
		)
		self.assertEqual(cert.common_name, "*.blr1.frappe.dev")

	def test_tls_provider_denormalized_from_domain(self) -> None:
		cert = frappe.get_doc({"doctype": "TLS Certificate", "root_domain": "blr1.frappe.dev"}).insert(
			ignore_permissions=True
		)
		self.assertEqual(cert.tls_provider, "letsencrypt-test")


class TestIssueAndPush(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_test_server()
		_ensure_test_image()
		_purge_vms()
		_make_domain("blr1.frappe.dev", "blr1")
		# Two PEM files on disk — issue() records their paths; _push_to_proxies reads them.
		self._tmp = tempfile.TemporaryDirectory()
		self.fullchain = Path(self._tmp.name) / "fullchain.pem"
		self.privkey = Path(self._tmp.name) / "privkey.pem"
		self.fullchain.write_text("FULLCHAIN-PEM")
		self.privkey.write_text("PRIVKEY-PEM")

	def tearDown(self) -> None:
		self._tmp.cleanup()

	def _issued(self) -> IssuedCert:
		return IssuedCert(
			fullchain_path=str(self.fullchain),
			privkey_path=str(self.privkey),
			not_before="2026-06-08 00:00:00",
			not_after="2026-09-06 00:00:00",
		)

	def test_issue_records_result_and_pushes_to_region_proxies(self) -> None:
		# Two proxies in blr1, one in a different region, one non-proxy in blr1.
		blr_proxy_a = _new_vm(is_proxy=1, region="blr1").name
		blr_proxy_b = _new_vm(is_proxy=1, region="blr1").name
		_new_vm(is_proxy=1, region="nyc3")
		_new_vm(is_proxy=0, region="blr1")

		cert = frappe.get_doc({"doctype": "TLS Certificate", "root_domain": "blr1.frappe.dev"}).insert(
			ignore_permissions=True
		)

		fake_tls = patch.object(module.tls, "for_tls_provider")
		fake_dns = patch.object(module.dns, "for_domain_provider")
		fake_push = patch.object(module.proxy, "push_cert")
		with fake_tls as tls_for, fake_dns, fake_push as push:
			tls_for.return_value.issue.return_value = self._issued()
			cert.issue()

		cert.reload()
		self.assertEqual(cert.status, "Active")
		self.assertEqual(cert.fullchain_path, str(self.fullchain))
		self.assertEqual(str(cert.expires_on), "2026-09-06 00:00:00")

		# push_cert called exactly for the two blr1 proxies, with the PEM BYTES.
		pushed_vms = {call.args[0] for call in push.call_args_list}
		self.assertEqual(pushed_vms, {blr_proxy_a, blr_proxy_b})
		for call in push.call_args_list:
			self.assertEqual(call.args[1], "FULLCHAIN-PEM")
			self.assertEqual(call.args[2], "PRIVKEY-PEM")

	def test_issue_failure_flips_status_to_failed(self) -> None:
		cert = frappe.get_doc({"doctype": "TLS Certificate", "root_domain": "blr1.frappe.dev"}).insert(
			ignore_permissions=True
		)
		with patch.object(module.tls, "for_tls_provider") as tls_for:
			tls_for.return_value.issue.side_effect = frappe.ValidationError("boom")
			with self.assertRaises(frappe.ValidationError):
				cert.issue()
		cert.reload()
		self.assertEqual(cert.status, "Failed")

	def test_push_to_proxies_requires_issued_paths(self) -> None:
		cert = frappe.get_doc({"doctype": "TLS Certificate", "root_domain": "blr1.frappe.dev"}).insert(
			ignore_permissions=True
		)
		with self.assertRaises(frappe.ValidationError):
			cert.push_to_proxies()

	def test_one_unreachable_proxy_does_not_block_the_rest(self) -> None:
		good = _new_vm(is_proxy=1, region="blr1").name
		bad = _new_vm(is_proxy=1, region="blr1").name
		cert = frappe.get_doc(
			{
				"doctype": "TLS Certificate",
				"root_domain": "blr1.frappe.dev",
				"fullchain_path": str(self.fullchain),
				"privkey_path": str(self.privkey),
				"status": "Active",
			}
		).insert(ignore_permissions=True)

		def push(vm, fullchain, privkey):
			if vm == bad:
				raise frappe.ValidationError("guest down")

		with patch.object(module.proxy, "push_cert", side_effect=push):
			pushed = cert.push_to_proxies()
		# The good proxy still got the cert; the bad one was logged and skipped.
		self.assertEqual(pushed, [good])


class TestRenewExpiring(IntegrationTestCase):
	def setUp(self) -> None:
		_make_domain("blr1.frappe.dev", "blr1")

	def test_renews_only_certs_inside_the_window(self) -> None:
		soon = frappe.utils.add_days(frappe.utils.now_datetime(), 10)
		far = frappe.utils.add_days(frappe.utils.now_datetime(), 200)
		near = frappe.get_doc(
			{
				"doctype": "TLS Certificate",
				"root_domain": "blr1.frappe.dev",
				"status": "Active",
				"expires_on": soon,
			}
		).insert(ignore_permissions=True)
		distant = frappe.get_doc(
			{
				"doctype": "TLS Certificate",
				"root_domain": "blr1.frappe.dev",
				"status": "Active",
				"expires_on": far,
			}
		).insert(ignore_permissions=True)

		renewed: list[str] = []
		with patch.object(module.TLSCertificate, "renew", lambda self: renewed.append(self.name)):
			module.renew_expiring()

		self.assertIn(near.name, renewed)
		self.assertNotIn(distant.name, renewed)
