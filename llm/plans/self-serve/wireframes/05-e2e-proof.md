# Phase 05 wireframe — signup → live-site e2e proof (host-bound)

Drawn from the actual implementation: the e2e use-case module
(`atlas/tests/e2e/use_cases/self_serve_site.py`) and the substrate it reuses —
`proxy_vm.py` (proxy VM + reserved IP), `tls_issuance.py` (real LE-staging
producer + DocType seeding), `bench_image.py` (golden-snapshot bake),
`_droplets.phase()` (shared bootstrapped droplet). It is the **superset**: it
drives the real signup → verify → fulfil → golden-VM clone → deploy → 200 →
subdomain → proxy → off-droplet HTTPS on **v4 AND v6**, and asserts the
Contract-C negative on the real path.

## The run (one shared droplet; a proxy VM; a worker-driven site VM)

```
  CONTROLLER (off-droplet)                 SHARED DROPLET                    OFF-DROPLET PROBES
  ────────────────────────                 ──────────────                    ──────────────────
  run_smoke(reuse, keep)
   │ get_tls_config() ── no atlas_tls_* ─▶ raise MissingConfig (skip clean, nothing billable)
   │ _preflight_controller_deps() ── no certbot/boto3 ─▶ raise (skip clean)
   │
   ├─ phase("self-serve-site (smoke)") ── reuse Active+reachable Server, else provision
   │
   │ PRECONDITIONS (fail clean before any billable site work)
   │ ├ _quiet_other_root_domains(domain)   blr1.frappe.dev → is_active=0   (restore in teardown)
   │ ├ tls_issuance._seed_tls_doctypes()   Domain/TLS Provider + Root Domain(<domain>, active)
   │ └ _resolve_or_bake_golden_snapshot()  Atlas Settings.default_bench_snapshot Available?
   │       │  yes → use it                                     │  no →
   │       │                                          bench_image._bake(server) ─▶ build VM ─▶ snapshot
   │       └──────────────────────────────────────── set default_bench_snapshot ◀┘  (build VM → teardown)
   │
   │ FRONT DOOR (proxy + real cert + reserved v4)
   │ ├ _provision_proxy_vm(region) ─────────────────▶ proxy VM (Running, [::]:443, region=<tls region>)
   │ ├ proxy.build_proxy() ─────────── guest-SSH ────▶ nginx+Lua compiled, unit up
   │ ├ tls_issuance._issue_certificate(domain) ── certbot DNS-01 → Route53 → LE staging → PEMs
   │ ├ TLS Certificate.push_to_proxies() ── guest-SSH ▶ wildcard cert in proxy, nginx reload
   │ └ _allocate_and_attach() ── DO reserved IPv4 ───▶ host 1:1-NAT; reserved_ipv4 known
   │
   │ CONTRACT C — the NEGATIVE (assert no provision before verify)
   │ ├ _request_site(email, sub)   request_site.__wrapped__()  → Site Request{Pending}, sendmail queued
   │ └ _assert_no_provision_yet()  NO Site<fqdn>, NO Virtual Machine{title=fqdn}   ◀── the gate proven
   │
   │ FULFIL  → WORKER drives the whole chain (not inline)
   │ ├ _verify_request()  SiteRequest.verify() → User(owner) + Site.insert()
   │ │                                              └ after_insert → enqueue auto_provision (queue=long)
   │ │                                                       │
   │ │   WORKER JOB ▶ clone golden snapshot ─────────────────┤ → site VM (after_insert auto-provisions)
   │ │   (the same       wait_for_ssh(guest)                  │
   │ │    worker the      deploy_site() ── guest-SSH ─▶ deploy-site.py: bench new-site <fqdn>
   │ │    VM e2e relies      │                                │   + setup production (nginx :80 by Host)
   │ │    on)                db_set admin_password (encrypted)│
   │ │                       wait_for_http(v6, Host=fqdn) ─── 200 /api/method/ping (Contract B)
   │ │                       _create_subdomain() → Subdomain{sub, region, vm, active=1}
   │ │                       db_set status = Running          │
   │ ├ _wait_for_site_running(fqdn)  poll rollback() →────────┤ status: Pending→Provisioning→Deploying
   │ │   (≤1800s; Failed → dump Tasks; deadline → dump Tasks) │           →Running ✔  (returns site VM)
   │ └ _assert_admin_password_set()  site.get_password("admin_password") non-empty
   │
   │ ROUTING (proxy picks up the new Subdomain)
   │ ├ proxy.reconcile_proxy(proxy_vm) ── map_for_region(<region>) ─▶ live /map
   │ └ _assert_live_map({sub: site_vm.ipv6})  read back byte-for-byte over guest-SSH
   │
   │ OFF-DROPLET HTTPS — v4 AND v6  (the idea-doc requirement; LE staging → curl -k)
   │ ├ _assert_inbound_https("-4", reserved_ipv4, fqdn) ──────────────────────▶ curl -4 -k --resolve
   │ │       ext v4 → DO edge → host DNAT(anchor) → proxy :443 → router.lua → site :80 v6 → "pong"
   │ └ _assert_inbound_https("-6", proxy_vm.ipv6, fqdn) ──────────────────────▶ curl -6 -k --resolve
   │         ext v6 → proxy /128 [::]:443 → router.lua → site :80 v6 → "pong"   (NEW vs proxy_vm: v6 in)
   │
   └─ finally: _teardown()  +  _cleanup_tls_doctypes()  +  _restore_root_domains()
```

## Teardown (billable-aware, every step guarded)

```
  _teardown(reserved, proxy_vm, sub, domain, email):
   1. Site.terminate()  → delete Subdomain (proxy stops routing on reconcile) + terminate backing VM
      frappe.delete_doc("Site", fqdn)
   2. delete Site Request(s) for email   (non-transactional rows outlive the run)
      delete User(email)                  (created at fulfilment; persists past txn)
   3. _teardown_proxy(reserved, proxy_vm, proxy_vm)  → release reserved IPv4, terminate proxy VM
      (site VM already gone via step 1; 2nd slot is a guarded no-op)
   4. terminate any build VM baked inline this run   (the golden SNAPSHOT survives — it's the artifact)
  _cleanup_tls_doctypes()   → drop TLS Certificate + Root Domain(<domain>) + the 2 Providers
  _restore_root_domains()   → reactivate blr1.frappe.dev (and any other quieted row)
```

## The four host facts this — and only this — proves

| # | Fact | The assertion in the module |
| - | ---- | --------------------------- |
| 1 | golden image actually serves | `_wait_for_site_running` reaches Running (clone→deploy→200 survives onto a fresh per-site VM) |
| 2 | readiness signal is real | Site flips Running only on the worker's observed 200, not VM status (Contract B) |
| 3 | proxy routes the new subdomain, v4 **and** v6 | `_assert_inbound_https("-4", …)` + `_assert_inbound_https("-6", …)` both return `pong` |
| 4 | verification gates provision | `_assert_no_provision_yet` — no Site, no VM before `verify()` (Contract C) |

## Skip-clean preconditions (raise before anything billable)

```
  no atlas_tls_* config           → MissingConfig          (get_tls_config)
  no certbot / openssl / boto3     → RuntimeError           (_preflight_controller_deps)
  no golden snapshot + can't bake  → (bake raises on the shared droplet)
  >1 active Root Domain at fulfil  → handled: _quiet_other_root_domains makes it unambiguous
```
