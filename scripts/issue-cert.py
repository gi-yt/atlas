#!/usr/bin/env python3
# Issue (or renew) the wildcard cert *.<domain> via certbot over an ACME DNS-01
# challenge, then emit the on-disk PEM paths and validity window as the typed
# ATLAS_RESULT= line the controller parses.
#
# UNLIKE every other script here, this one runs on the ATLAS CONTROLLER, not on a
# Server host: ACME issuance is a controller concern (the PEMs land where the
# control plane can read them to push to the proxy fleet), so there is no host to
# stage onto. It is still invoked through the same typed-CLI Task contract — the
# controller-local runner (atlas.atlas.local_task) calls
#   issue-cert.py --domain blr1.frappe.dev --acme-directory-url ... \
#                 --account-email ops@... --certbot-arg --dns-route53
# and the DNS vendor credentials arrive in the environment (e.g. AWS_ACCESS_KEY_ID),
# never in argv. certbot + the DNS plugin are a controller-host dependency, not a
# server-runtime one, so spec principle #5's stdlib-only server rule is intact.
#
# Idempotent: certbot renews-or-skips a still-valid lineage (--keep-until-expiring).

import os
import sys
import typing
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import atlas.certs as certs
from atlas._run import run
from atlas._task import TaskInputs, TaskResult


@dataclass(frozen=True)
class IssueCertInputs(TaskInputs):
	"""Issue *.<domain> via certbot DNS-01 on the controller."""

	command: typing.ClassVar[str] = "issue-cert"
	domain: str  # the wildcard zone, e.g. blr1.frappe.dev → issues *.blr1.frappe.dev
	acme_directory_url: str  # ACME server directory (LE production or staging)
	account_email: str  # ACME registration / expiry-notice email
	# The DNS plugin NAME (e.g. route53), from the DNS provider. The script renders
	# it as the certbot `--dns-<name>` flag — a plain name crosses the CLI, never a
	# `--`-prefixed token argparse would misread as an option.
	dns_authenticator: str


@dataclass(frozen=True)
class IssueCertResult(TaskResult):
	fullchain_path: str
	privkey_path: str
	not_before: str
	not_after: str


def main() -> None:
	inputs = IssueCertInputs.from_args()

	run(
		certs.certbot_command(
			domain=inputs.domain,
			acme_directory_url=inputs.acme_directory_url,
			account_email=inputs.account_email,
			dns_authenticator=inputs.dns_authenticator,
		)
	)

	fullchain = certs.fullchain_path(inputs.domain)
	privkey = certs.privkey_path(inputs.domain)
	if not os.path.isfile(fullchain):
		sys.exit(f"certbot reported success but {fullchain} is missing")

	dates = run("openssl x509 -noout -dates -in {}", fullchain)
	not_before, not_after = certs.parse_openssl_dates(dates)

	IssueCertResult(
		fullchain_path=fullchain,
		privkey_path=privkey,
		not_before=not_before,
		not_after=not_after,
	).emit()
	print(f"Issued *.{inputs.domain}; valid until {not_after}.")


if __name__ == "__main__":
	main()
