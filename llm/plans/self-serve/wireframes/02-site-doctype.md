# Phase 02 wireframe — Site DocType (lifecycle + Desk form)

Phase 02 is the backend `Site` resource. The user-facing SPA *Sites screen* is
Phase 04; what is navigable **now** is the operator's **Desk form** (System
Manager has full CRUD) plus the background state machine the controller drives.
Drawn from the actual implementation (`atlas/atlas/doctype/site/site.{json,py}`,
`atlas/atlas/placement.py`).

## Insert → live-site state machine (auto_provision)

```
 user/operator inserts Site{ subdomain: "acme" }
        │
        ▼
 ┌────────────────────────────────────────────┐
 │ before_insert                              │
 │  • _validate_label  (single dotless DNS    │  bad label →
 │    label, lowercase [a-z0-9-], ≤63, no     │  frappe.throw
 │    leading/trailing '-')                   │  ("single label" / …)
 │  • _validate_reserved (www admin api …)    │  reserved → throw
 │  • _apply_region_default ← active          │  0/many active
 │    Root Domain (blr1.frappe.dev → blr1)    │  Root Domain → throw
 │  • status = Pending                        │
 ├────────────────────────────────────────────┤
 │ autoname:  name = "acme" + "."             │  duplicate FQDN →
 │            + root_domain.domain            │  throw("already taken")
 │          = acme.blr1.frappe.dev            │  (Contract A)
 ├────────────────────────────────────────────┤
 │ validate:  _validate_immutability          │  (on update only)
 ├────────────────────────────────────────────┤
 │ after_insert: enqueue auto_provision       │  queue=long, timeout=1800
 └──────────────┬─────────────────────────────┘  (no-op run in_test)
                │
   ╔════════════▼════════════════════════════════════════════════╗
   ║ auto_provision(site_name)   — background, fail-loud           ║
   ║                                                               ║
   ║  status: Pending ─▶ Provisioning ─▶ Deploying ─▶ Running      ║
   ║                          │              │            ▲        ║
   ║  1. clone backing VM ◀───┘              │            │        ║
   ║     Snapshot(default_bench_snapshot)    │            │        ║
   ║       .clone_to_new_vm(fqdn, fleetkey)  │            │        ║
   ║     → site.virtual_machine = <vm>       │            │        ║
   ║  2. _wait_for_vm_ssh(vm)   (booted)     │            │        ║
   ║  3. _deploy_site(vm, fqdn) ─────────────┘   ◀ plan 03 seam    ║
   ║       bench new-site <fqdn> + :80 bring-up                    ║
   ║  4. _wait_for_http(vm.ipv6)  ◀ plan 03 seam (Contract B)      ║
   ║       blocks on guest HTTP 200 ──────────────────────┐        ║
   ║  5. _create_subdomain(vm) → Subdomain row ───────────┘        ║
   ║       (its after_insert reconciles the proxy fleet)          ║
   ║       site.subdomain_doc = <subdomain>                        ║
   ║  6. status = Running                                          ║
   ║                                                               ║
   ║  any exception ▶ status = Failed (and re-raise)               ║
   ╚═══════════════════════════════════════════════════════════════╝
                │
                ▼
        live at  https://acme.blr1.frappe.dev   (proxy + TLS already built)
```

## terminate()

```
 Site.terminate()
   │  status == Terminated? → throw("already terminated")
   ├─ _delete_subdomain:  db_set(subdomain_doc=None) THEN delete Subdomain
   │                      (clear-before-delete; link guard queries the DB)
   ├─ _terminate_backing_vm:  vm.terminate()  (if not already Terminated)
   └─ status = Terminated; save
```

## Desk form (operator — what's navigable now)

```
┌─ Site:  acme.blr1.frappe.dev ───────────────── [ Terminate ]─┐
│ Overview                                                     │
│                                                              │
│  Subdomain*  [ acme            ]   Status   ( Running )      │
│  (set_only_once)                   (read-only Select)        │
│  Region      [ blr1            ]   ← read-only, resolved     │
│                                                              │
│ ── Backing ───────────────────────────────────────────────  │
│  Virtual Machine  [ <uuid> ]🔗     Routing Entry [ acme ]🔗  │
│  (read-only, cloned)               (subdomain_doc, r/o)      │
└──────────────────────────────────────────────────────────── ┘
   name (key) = subdomain.region-domain = the one routing string
```

> The list view columns are `subdomain`, `region`, `status`; standard filters
> `region`, `status`. The user-facing create-from-subdomain SPA screen is
> Phase 04 — the permission layer (`Atlas User` `if_owner` + `owner_only`) already
> scopes it.
