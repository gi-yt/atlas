# Phase 03 wireframe — deploy-site.py + readiness gate (serving model)

Phase 03 is backend/infra: the in-guest deploy script + the HTTP readiness gate.
There is no new user UI; the navigable surface is the operator's **`deploy-site`
Task row** (audit trail) and the new **Credentials** section on the Site Desk
form. Drawn from the actual implementation
(`bench/deploy-site.py`, `atlas/atlas/deploy_site.py`,
`atlas/atlas/doctype/site/site.py`).

## Where it slots into auto_provision (steps 3–4, now built)

```
 auto_provision(site)   …Provisioning → Deploying → Running…
   2. _wait_for_vm_ssh(vm)        (clone booted)
   3. admin_pw = _deploy_site(site, vm) ───────────┐  ◀ plan 03, BUILT
        deploy_site(vm, fqdn) over guest-SSH       │
        └─ returns generated admin password ───────┤
      site.db_set("admin_password", admin_pw)  ◀───┘  (encrypted, stored
                                                        BEFORE the http wait)
   4. _wait_for_http(site, vm) ────────────────────┐  ◀ plan 03, BUILT
        wait_for_http(vm.ipv6, fqdn)               │  Contract B
        blocks until guest :80 → HTTP 200 ─────────┘
   5. _create_subdomain …   6. status = Running
```

## deploy_site(vm, fqdn) — what runs IN THE GUEST

```
controller (atlas.atlas.deploy_site)            guest (clone of golden snapshot)
──────────────────────────────────             ─────────────────────────────────
 admin_pw = frappe.generate_hash(24)
 scp  bench/deploy-site.py ───────────────────▶ /tmp/atlas-deploy-site/deploy-site.py
 ssh  python3 deploy-site.py \
        --site-name acme.blr1.frappe.dev \
        --admin-password <admin_pw> ──────────▶ ┌─ deploy-site.py (root) ───────────┐
                                                │ 1 pre-flight: bench-cli + baked    │
                                                │   bench present? else fail loud    │
 (admin_pw over the encrypted SSH channel,      │ 2 bench -b atlas new-site <fqdn>    │
  never a guest file)                           │     --admin-password <pw>          │
                                                │ 3 mark setup complete:             │
                                                │   bench frappe --site <fqdn> execute│
                                                │   frappe.db.set_value(Installed     │
                                                │   Application … is_setup_complete=1)│
                                                │ 4 bench -b atlas setup production:  │
                                                │   • dns_multitenant = 1 (Host route)│
                                                │   • generate + reload bench's OWN   │
                                                │     nginx + supervisor → serve :80  │
                                                │ (idempotent: site exists → skip 2-3)│
                                                │ emit ATLAS_RESULT={site,created,…}  │
                                                └────────────────────────────────────┘
 ◀── stdout/stderr/exit ── record Task(deploy-site, Success|Failure)
 exit≠0 → frappe.throw (→ Site Failed);  exit 0 → return admin_pw
```

## Serving model — who answers :80 / :443

```
        user ──HTTPS :443──▶  EDGE PROXY (spec/12)        ◀── TLS terminates HERE
                              Host: acme.blr1.frappe.dev      (wildcard cert)
                                       │
                              proxy_pass http://[<vm-v6>]:80   ◀── plaintext south hop,
                                       │                            public v6
                                       ▼
                              GUEST: bench-cli's OWN nginx :80   ◀── dns_multitenant,
                              server_name acme.blr1.frappe.dev       NO in-guest TLS
                                       │
                              supervisor → gunicorn / socketio / workers
                                       ▼
                              Frappe site  acme.blr1.frappe.dev
```

## wait_for_http — the readiness gate (Contract B)

```
 wait_for_http(ipv6, host_header=fqdn, path="/api/method/ping", timeout=600, poll=5)
   loop:
     GET http://[ipv6]:80/api/method/ping   Host: acme.blr1.frappe.dev
       ├─ 200  {"message":"pong"}  → return        ◀ ONLY this ends the wait
       ├─ conn refused / reset (nginx not up)  ┐
       ├─ 502 (nginx up, supervisor not yet)   ├─ "not ready" → sleep, retry
       └─ any OSError/HTTPException             ┘
     deadline passed? → raise frappe.ValidationError("… not seen after 600s")
```

- Probes the FQDN **Host header** so the bench's multitenant nginx routes to THIS
  site (a Host-less `/` would hit the default vhost — a false signal).
- `/api/method/ping` is 200 once the web server is up AND the site DB resolves —
  independent of the setup-wizard (that only gates `/`, handled in step 3 above).

## Site Desk form — the new Credentials section

```
┌─ Site:  acme.blr1.frappe.dev ───────────────── [ Terminate ]─┐
│ … Overview / Backing (unchanged from Phase 02) …             │
│ ── Credentials ─────────────────────────────────────────────│
│  Administrator Password  [ •••••••••••••• ]  (Password, r/o) │
│    generated per-site by deploy-site.py; stored encrypted;   │
│    shown once to the owner in the SPA (plan 04)              │
└──────────────────────────────────────────────────────────── ┘
```

> No user-facing UI ships in Phase 03. The `deploy-site` Task row (under the VM /
> Server) is the operator's audit trail; the SPA reveal of `admin_password` is
> Phase 04.
