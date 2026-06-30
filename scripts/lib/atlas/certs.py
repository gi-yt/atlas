"""Pure helpers for the issue-cert task — certbot argv + cert-path layout.

Kept out of issue-cert.py so the argv construction and the on-disk layout are
unit-testable with no certbot and no host (the same split the LVM/network task
libs use: pure string logic here, the one subprocess in the entry point). The
controller-side layout lives under the Atlas home (`~/.atlas/certbot`), a sibling
of the SSH `known_hosts` dir, so all controller-local Atlas state sits together.
"""

from __future__ import annotations

import os

from atlas._run import _substitute


def atlas_home() -> str:
	"""The Atlas controller home, `~/.atlas`. Matches the SSH transport's
	`KNOWN_HOSTS_PATH` parent so controller state is colocated."""
	return os.path.join(os.path.expanduser("~"), ".atlas")


def certbot_config_dir(domain: str) -> str:
	"""certbot's --config-dir for this domain. Per-domain so accounts/renewal
	state never collide across regions."""
	return os.path.join(atlas_home(), "certbot", domain)


def live_dir(domain: str) -> str:
	"""Where certbot writes the live symlinks for `*.<domain>`. certbot names the
	lineage after the first -d, with the leading `*.` stripped to `<domain>`."""
	return os.path.join(certbot_config_dir(domain), "live", domain)


def fullchain_path(domain: str) -> str:
	return os.path.join(live_dir(domain), "fullchain.pem")


def privkey_path(domain: str) -> str:
	return os.path.join(live_dir(domain), "privkey.pem")


def certbot_command(
	domain: str,
	acme_directory_url: str,
	account_email: str,
	dns_authenticator: str,
) -> str:
	"""The full certbot command line to issue (or renew) `*.<domain>`
	non-interactively over DNS-01, rendered as a single auto-quoted string for
	`_run.run`. `dns_authenticator` is the DNS plugin name (e.g. `route53`),
	rendered here as the `--dns-<name>` flag — keeping the `--` spelling on the
	script side means the value crossing the CLI is a plain name argparse can't
	mistake for an option. Credentials travel via the environment, never argv, so
	they never appear in `ps`. Idempotent: certbot renews-or-skips a still-valid
	lineage."""
	config = certbot_config_dir(domain)
	return _substitute(
		"certbot certonly --non-interactive --agree-tos -m {} --server {} {} -d {}"
		" --config-dir {} --work-dir {} --logs-dir {} --keep-until-expiring",
		(
			account_email,
			acme_directory_url,
			f"--dns-{dns_authenticator}",
			f"*.{domain}",
			config,
			os.path.join(config, "work"),
			os.path.join(config, "logs"),
		),
	)


def parse_openssl_dates(stdout: str) -> tuple[str, str]:
	"""Parse `openssl x509 -noout -dates` output into (not_before, not_after) as
	the raw OpenSSL date strings (e.g. `Jun  8 00:00:00 2026 GMT`). The controller
	normalizes these to its Datetime fields via frappe.utils.get_datetime, which
	parses this format. Raises ValueError if either line is missing."""
	not_before = not_after = None
	for line in stdout.splitlines():
		if line.startswith("notBefore="):
			not_before = line[len("notBefore=") :].strip()
		elif line.startswith("notAfter="):
			not_after = line[len("notAfter=") :].strip()
	if not_before is None or not_after is None:
		raise ValueError(f"could not parse notBefore/notAfter from openssl output: {stdout!r}")
	return not_before, not_after
