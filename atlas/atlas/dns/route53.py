"""Route 53 DNS provider — DNS-01 via AWS Route 53.

Reads `Route53 Settings` for the IAM credentials (the secret via
`atlas.atlas.secrets.get_secret`, mirroring how `DigitalOceanProvider` reads its
token). The actual TXT-record dance is certbot's `dns-route53` plugin's job; this
class only supplies the plugin flag and the AWS credential env. `authenticate()`
proves the credentials reach the account by listing hosted zones — the lightest
read that exercises the same `route53:*` permissions issuance needs.
"""

from __future__ import annotations

import frappe

from atlas.atlas.dns import register
from atlas.atlas.dns.base import AuthResult, DnsProvider
from atlas.atlas.secrets import get_secret


@register
class Route53DnsProvider(DnsProvider):
	provider_type = "Route53"

	def __init__(self) -> None:
		settings = frappe.get_single("Route53 Settings")
		self.access_key_id = settings.access_key_id
		self.secret_access_key = get_secret("Route53 Settings", "Route53 Settings", "secret_access_key")
		self.region = settings.region or "us-east-1"

	def authenticate(self) -> AuthResult:
		try:
			import boto3
		except ImportError:
			return AuthResult(ok=False, error="boto3 not installed on the controller")
		client = boto3.client(
			"route53",
			aws_access_key_id=self.access_key_id,
			aws_secret_access_key=self.secret_access_key,
			region_name=self.region,
		)
		try:
			response = client.list_hosted_zones(MaxItems="1")
		except Exception as exception:
			return AuthResult(ok=False, error=str(exception))
		zones = response.get("HostedZones") or []
		label = zones[0]["Name"].rstrip(".") if zones else "no hosted zones"
		return AuthResult(ok=True, account_label=label)

	def credential_env(self) -> dict[str, str]:
		return {
			"AWS_ACCESS_KEY_ID": self.access_key_id,
			"AWS_SECRET_ACCESS_KEY": self.secret_access_key,
			"AWS_DEFAULT_REGION": self.region,
		}

	def certbot_authenticator(self) -> str:
		# `certbot-dns-route53` discovers the hosted zone from the domain name at
		# issue time, so no zone-id is needed — just name the authenticator. The
		# script renders this as `--dns-route53`.
		return "route53"
