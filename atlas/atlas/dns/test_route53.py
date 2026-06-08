"""Unit tests for the Route 53 DNS provider — exercises the certbot wiring
(`certbot_args`, `credential_env`) without touching AWS. Construction reads
`Route53 Settings`, so we stub the Single read and the secret fetch."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from atlas.atlas.dns import route53


def _provider(access="AKIA123", secret="topsecret", region="us-east-1") -> route53.Route53DnsProvider:
	settings = SimpleNamespace(access_key_id=access, region=region)
	with (
		patch.object(route53.frappe, "get_single", return_value=settings),
		patch.object(route53, "get_secret", return_value=secret),
	):
		return route53.Route53DnsProvider()


class TestRoute53DnsProvider(IntegrationTestCase):
	def test_certbot_authenticator_is_route53(self) -> None:
		self.assertEqual(_provider().certbot_authenticator(), "route53")

	def test_credential_env_carries_aws_keys(self) -> None:
		env = _provider(access="AKIAEXAMPLE", secret="shh", region="eu-west-1").credential_env()
		self.assertEqual(env["AWS_ACCESS_KEY_ID"], "AKIAEXAMPLE")
		self.assertEqual(env["AWS_SECRET_ACCESS_KEY"], "shh")
		self.assertEqual(env["AWS_DEFAULT_REGION"], "eu-west-1")

	def test_region_defaults_when_blank(self) -> None:
		self.assertEqual(_provider(region="").region, "us-east-1")

	def test_authenticate_reports_boto3_missing(self) -> None:
		provider = _provider()
		with patch.dict("sys.modules", {"boto3": None}):
			result = provider.authenticate()
		self.assertFalse(result.ok)
		self.assertIn("boto3", result.error)
