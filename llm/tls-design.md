# Atlas TLS & Domain Layer — Design / Plan

Status: **approved, pending build**. This is the contract for the domain + TLS
layer above the reverse proxy. Companion to [`proxy-design.md`](./proxy-design.md)
(which this feeds) and the durable spec ([`spec/12-proxy.md`](../spec/12-proxy.md)).

## Context

The reverse proxy ([`spec/12-proxy.md`](../spec/12-proxy.md)) terminates TLS for
`*.<region>.frappe.dev` using a **wildcard cert "acquired out-of-band"** and fed
to `atlas.atlas.proxy.push_cert(vm, fullchain, privkey)` as raw PEM strings —
**there is no source for those PEMs today**. `push_cert` is dead-ended: nothing
in Atlas *produces* a cert.

This change builds the **domain + TLS layer** above the proxy:

1. Track **root domains** (start with one row; multiple later).
2. Hold a **TLS Certificate** per root domain (the regional wildcard).
3. **Issue** that cert via **Let's Encrypt** over **DNS-01**, validated through
   **AWS Route 53**, with a pluggable TLS-provider seam (ZeroSSL / Self-Managed
   later) and a pluggable DNS-provider seam.
4. **Wire** issuance → `push_cert` so a freshly-issued/renewed cert lands on
   every proxy VM in the domain's region and nginx reloads.

## Locked decisions (from the design interview)

- **Root Domain == region wildcard.** One `Root Domain` row == `<region>.frappe.dev`;
  its cert is `*.<region>.frappe.dev`. `Root Domain.region` is the join key to the
  proxy fleet (the existing `Virtual Machine.is_proxy + region`).
- **DNS-01 via Route 53**, credentials in a **dedicated `Domain Provider`** DocType
  (+ per-vendor Settings Single), mirroring the existing compute `Provider` ABC.
- **ACME engine = certbot** driven by a **Task script** (`scripts/issue-cert.py`),
  run **on the Atlas controller host** (not over SSH — issuance is a
  controller-level concern; PEMs land on the controller anyway).
- **Cert PEMs stored on disk**; the DocType stores only the **paths** (private key
  bytes stay out of the DB, mirroring `Atlas Settings.ssh_private_key_path`).
- **Renewals:** daily scheduled job (renew-if-near-expiry → re-push) **plus** a
  manual **Renew/Issue** button.
- A **TLS-provider abstraction** mirrors the compute `Provider` ABC so ZeroSSL /
  Self-Managed slot in without touching callers.

---

## DocTypes (3 regular + 2 Settings Singles + 1 regular cert)

All in module `Atlas`, none submittable, track changes, `System Manager` read —
matching the existing 13 ([`spec/02-doctypes.md`](../spec/02-doctypes.md)).

### 1. `Domain Provider` (regular) — DNS account, mirrors compute `Provider`
Thin link table for DNS vendors, twin of `atlas/atlas/doctype/provider`.

| Field | Type | Notes |
|---|---|---|
| `provider_name` | Data | PK, unique, `set_only_once`. e.g. `route53-prod`. |
| `provider_type` | Select | `Route53` (`AWS Route 53`); `Cloudflare` reserved for later. `set_only_once`. Keys the DNS registry. |
| `is_active` | Check | default 1; `archive()` flips it. |

Buttons: **Test Connection** (`dns_provider.authenticate()` — Route 53
`GetHostedZone`), **Archive**.

### 2. `Route53 Settings` (Single) — Route 53 creds, twin of `DigitalOcean Settings`
| Field | Type | Notes |
|---|---|---|
| `access_key_id` | Data | `set_only_once`. |
| `secret_access_key` | Password | secret via `secrets.get_secret`. |
| `region` | Data | AWS API region (default `us-east-1`; Route 53 is global). |

(No zone-id field: `certbot-dns-route53` discovers the hosted zone by the domain
name at issue time. Minimal, like `Self-Managed Settings`.)

### 3. `Root Domain` (regular) — one wildcard zone == one region
| Field | Type | Notes |
|---|---|---|
| `domain` | Data | PK (autoname `field:domain`), unique, `set_only_once`. e.g. `blr1.frappe.dev`. Wildcard is `*.<domain>`. |
| `region` | Data | Proxy fleet this domain fronts (join key to `Virtual Machine.region`). Explicit, `set_only_once`. |
| `domain_provider` | Link → Domain Provider | DNS account that owns the zone (DNS-01). |
| `tls_provider` | Link → TLS Provider | Who issues the cert. |
| `is_active` | Check | default 1. |

Controller: `issue_certificate()` — create/locate the domain's `TLS Certificate`
and trigger issuance (delegates to it). Button **Issue / Renew Certificate**.
Connections dashboard links its `TLS Certificate` + the region's proxy VMs.

### 4. `TLS Provider` (regular) — issuer account, mirrors compute `Provider`
| Field | Type | Notes |
|---|---|---|
| `provider_name` | Data | PK, unique, `set_only_once`. e.g. `letsencrypt-prod`. |
| `provider_type` | Select | `Let's Encrypt`, `ZeroSSL`, `Self-Managed`. `set_only_once`. Keys the TLS registry. |
| `is_active` | Check | default 1. |

(ZeroSSL/Self-Managed = registry stubs + Select options now; only Let's Encrypt
implemented this iteration.)

### 5. `Let's Encrypt Settings` (Single) — ACME account config
| Field | Type | Notes |
|---|---|---|
| `acme_directory_url` | Data | default LE production; staging URL for testing. |
| `account_email` | Data | ACME registration / expiry notices. `set_only_once`. |
| `agree_tos` | Check | required before issuing. |

### 6. `TLS Certificate` (regular) — the issued wildcard cert
| Field | Type | Notes |
|---|---|---|
| `name` | UUID (`hash`) | PK. |
| `root_domain` | Link → Root Domain | `set_only_once`. `title` shows `*.<domain>`. |
| `status` | Select | `Pending`, `Active`, `Expiring`, `Failed`. Read-only; set by issue/renew + scheduler. |
| `common_name` | Data | `*.<domain>`, read-only, derived. |
| `tls_provider` | Link → TLS Provider | denormalized from the domain; the issuer used. |
| `issued_on` / `expires_on` | Datetime | read-only, parsed from the issued cert. |
| `fullchain_path` / `privkey_path` | Data | read-only on-disk paths (PEM bytes on the controller FS; `0600` privkey, Frappe-user owned). |

Controller methods:
- `issue()` — run the TLS provider's issue flow (Task `issue-cert.py`), record
  paths + `issued_on`/`expires_on`, set `Active`, then `_push_to_proxies()`.
- `renew()` — same flow; idempotent re-issue.
- `_push_to_proxies()` — read PEMs off disk, call
  `atlas.atlas.proxy.push_cert(vm, fullchain, privkey)` for every `is_proxy` VM in
  `root_domain.region`. **This is the wiring to the proxy VM.**

Buttons: **Issue/Renew** (primary), **Push to Proxies** (re-push without re-issue).

### No structural change to `Virtual Machine`
`is_proxy` + `region` already exist. The fleet for a domain is
`is_proxy=1, region=<domain.region>` — the same query
`atlas/atlas/proxy.py:_proxy_vms_in_region` already uses.

---

## Abstractions (mirror `atlas/atlas/providers/`)

Two small registries, each modeled on the compute one
(`providers/__init__.py` `register`/`for_provider` + `providers/base.py` ABC).

### `atlas/atlas/dns/` — DNS provider seam
- `base.py`: `DnsProvider(ABC)` — `authenticate() -> AuthResult`,
  `credential_env() -> dict[str,str]` (Route 53: `AWS_ACCESS_KEY_ID`/
  `AWS_SECRET_ACCESS_KEY`), `certbot_args() -> list[str]` (`--dns-route53`).
- `route53.py`: `@register` `Route53DnsProvider` reading `Route53 Settings`
  (secret via `atlas.atlas.secrets.get_secret`).
- `__init__.py`: `for_domain_provider(name)` — twin of `for_provider`.

### `atlas/atlas/tls/` — TLS issuer seam
- `base.py`: `TlsProvider(ABC)` — `issue(domain, dns_provider) -> IssuedCert`
  (paths + not_before/not_after), `authenticate()`.
- `letsencrypt.py`: `@register` `LetsEncryptProvider` reading `Let's Encrypt
  Settings`; runs `issue-cert.py` with certbot args composed from the DNS
  provider's `certbot_args()` + `credential_env()`.
- `self_managed.py` / `zerossl.py`: registered stubs (Self-Managed = operator
  drops PEMs at the configured paths; ZeroSSL = `frappe.throw("not implemented")`).
- `__init__.py`: `for_tls_provider(name)`.

---

## The issue-cert script (controller-local Task)

`scripts/issue-cert.py` — typed-Python, `--kebab-case` flags in, one
`ATLAS_RESULT=` JSON line out ([`spec/04-tasks.md`](../spec/04-tasks.md),
`scripts/lib/atlas/`). Runs **certbot** non-interactive DNS-01:

```
certbot certonly --non-interactive --agree-tos \
  -m <account_email> --server <acme_directory_url> \
  --dns-route53 \                       # from dns_provider.certbot_args()
  -d '*.<domain>' \
  --config-dir <atlas certs dir>/<domain> --work-dir ... --logs-dir ...
# AWS creds via env (dns_provider.credential_env())
```

Emits `ATLAS_RESULT={"fullchain":"...","privkey":"...","not_before":"...","not_after":"..."}`.
Idempotent (certbot renews-or-skips).

**Execution path:** runs **on the controller**, not over SSH. Add a thin local
runner alongside the SSH transport — reuse `subprocess.run` as
`atlas/atlas/_ssh/transport.py` does — and **still record a `Task` row**
(`script = issue-cert.py`), the way `atlas/atlas/proxy.py:_record_guest_task`
records non-host ops. Reuse the `ATLAS_RESULT=` parse helper in
`atlas/atlas/_ssh/runner.py`.

certbot + `certbot-dns-route53` are a **controller-host dependency** (documented
in the spec; not a server/script-runtime dep, so principle #5's server-side
"stdlib only" rule is intact).

---

## Renewal

- **Manual:** `TLS Certificate.renew()` button + `Root Domain.issue_certificate()`.
- **Scheduled:** enable `scheduler_events` in `atlas/hooks.py` (currently fully
  commented out), `daily` →
  `atlas.atlas.doctype.tls_certificate.tls_certificate.renew_expiring`: find
  `Active` certs with `expires_on` within ~30 days, `renew()` (re-issue **and**
  re-push), flip status. Mirrors the proxy reconcile philosophy.

---

## Wiring summary (the request's 4th bullet)

```
Root Domain ──issue──▶ TLS Certificate.issue()
                          │  (TlsProvider.issue + DnsProvider via certbot Task)
                          ▼
                       PEMs on disk (fullchain_path, privkey_path)
                          │
                          ▼  _push_to_proxies(): for vm in proxies(region)
                       atlas.atlas.proxy.push_cert(vm, fullchain, privkey)   ← EXISTING
                          ▼
                       nginx reload on each proxy guest
```

`push_cert` already exists and is correct; this plan gives it a **producer**.

---

## Files to add / change

**New DocTypes** (each `<doctype>.json` + `.py` + `test_<doctype>.py`) under
`atlas/atlas/doctype/`: `domain_provider/`, `route53_settings/`, `root_domain/`,
`tls_provider/`, `lets_encrypt_settings/`, `tls_certificate/`.

**New modules:**
- `atlas/atlas/dns/{__init__,base,route53}.py` (+ `test_*`)
- `atlas/atlas/tls/{__init__,base,letsencrypt,self_managed,zerossl}.py` (+ `test_*`)
- `scripts/issue-cert.py` (+ `scripts/lib/atlas/...` helper if needed)
- a controller-local runner helper (small; beside
  `atlas/atlas/_ssh/transport.py` or a new `atlas/atlas/local_task.py`).

**Changed:**
- `atlas/hooks.py` — uncomment + populate `scheduler_events.daily`.
- `atlas/atlas/proxy.py` — reuse `push_cert`; maybe expose "proxy VMs for region".
- **Spec:** new `spec/13-tls.md`; bump DocType count + table in
  `spec/02-doctypes.md`; update "First run" order in `spec/README.md`.

**Desk-button coverage:** add the new buttons (Issue/Renew, Push to Proxies, Test
Connection, Archive) to the `desk_buttons` e2e ([`spec/README.md`](../spec/README.md)).

---

## Verification

**Unit (fast, no host — the bulk of coverage):**
- `test_root_domain.py` — autoname/immutability, `*.<domain>` derivation, region join.
- `test_tls_certificate.py` — status machine, path derivation, `renew_expiring`
  window, `_push_to_proxies` fans out to the right proxy VMs (mock `push_cert`).
- registry tests — `for_domain_provider`/`for_tls_provider` resolve & reject
  archived rows (twin of `providers/test_registry.py`);
  `Route53DnsProvider.credential_env()`/`certbot_args()`; `LetsEncryptProvider`
  composes the right certbot argv (mock the local runner — **no real certbot**).
- `scripts/lib/atlas/test_issue_cert.py` — argv construction + `ATLAS_RESULT` parse.

Run: `bench --site atlas.tests.local run-tests --app atlas`.

**Integration (manual, LE staging, no proxy):** configure Route53 Domain Provider
+ `Let's Encrypt Settings` (staging directory); click **Issue/Renew** on a real
`Root Domain` whose zone is in Route 53. Assert PEMs on disk, `expires_on`
populated, status `Active`, a `Task` row recorded.

**E2E (extends `proxy_vm`):** the `proxy_vm` e2e already attaches a reserved v4,
pushes a self-signed wildcard, and does an off-droplet HTTPS request. Add an
optional path: issue via LE staging through this flow + `_push_to_proxies`, then
re-run the `:443` probe — proving producer→`push_cert`→nginx end-to-end. Run:
`bench --site atlas.tests.local execute atlas.tests.e2e.use_cases.proxy_vm.run_smoke`.

**Formatting ([`CLAUDE.md`](../CLAUDE.md)):** keep diffs to changed lines only;
don't let `ruff format` rewrite pre-existing lines; stage with `git add -p`.
