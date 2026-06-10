# Phase 06 wireframe — spec & docs surfaces (cross-cutting)

Phase 06 ships no code — its "wireframe" is the **documentation surface map**:
every spec/doc file the self-serve layer touches, and what each now holds. Drawn
from the actual on-disk state after the Phase-06 audit + the roadmap close.

## The spec surface map (what landed where)

```
  spec/README.md ──────────────── read-order #14 (line 115) + Testing mapping
   │                              (self_serve_site.py) + Entry-points paragraph
   │                              [landed alongside 02–05 — audit ✔ unchanged]
   │
   ├─ spec/14-self-serve.md ★ THE new chapter (the durable home)
   │     # Self-serve sites
   │     ## Contract A — the one routing string   <subdomain>.<region domain>
   │     ## Contract B — the readiness signal      HTTP 200 on :80 → Running
   │     ## Contract C — ownership / verification   verify THEN insert, owner=user
   │     ## signup → verify → fulfil surface  (plan 04)
   │     ## the Site DocType                  (this phase / plan 02)
   │     ## the in-guest deploy (deploy-site.py) (plan 03)
   │     ## the Subdomain it creates / ## Testing
   │     [landed alongside 02–05 — audit ✔]
   │
   ├─ spec/02-doctypes.md ──────── count = 22; #21 Site, #22 Site Request
   │     full Fields/Layout/Perms sections; Atlas Settings.default_bench_snapshot
   │     [landed — audit ✔]
   │
   ├─ spec/08-images.md ────────── ## The golden bench image (self-serve)
   │     build-in-guest + snapshot (D01-1 drift, NOT SyncImageInputs flag)
   │     [landed by 01 — audit ✔]
   │
   ├─ spec/11-user-ui.md ───────── if_owner perm table: Site (row), Site Request
   │     the /signup + /verify guest on-ramp; Atlas User desk_access=0 coupling
   │     [landed by 02/04 — audit ✔]
   │
   ├─ spec/06-networking.md ────── :80 south-hop / readiness probe path
   │     [cross-linked, no new text — as planned]
   │
   └─ spec/09-roadmap.md ★ THE one open gap — CLOSED THIS PHASE
         Changes log was stranded at v0.6; ch.12/13/14 shipped with no entry.
         + v0.7  Reverse proxy        → 12-proxy.md
         + v0.8  TLS & domain layer   → 13-tls.md
         + v0.9  Self-serve sites     → 14-self-serve.md
         [the actual Phase-06 edit; see DRIFT D06-1]
```

## The companion-doc cleanup

```
  llm/self-serve-parallelism.md
     BEFORE: "Tracks to build" (T-IMG/T1/T2/T3) + "Contracts to freeze" (A/B/C)
              — all now built and in spec, so the lists were stale
     AFTER:  pointer header → spec/14 + plans/self-serve/ + DRIFT.md,
              and a "Remaining (host-bound, not yet proven)" section carrying the
              open D01/D03/D05 host items. Trimmed the way proxy-design.md was
              after the proxy shipped. [this phase]
```

## Audit result — planned vs found

The plan's FINDINGS block claimed "most rows already landed; one real gap left."
The audit confirmed every claim and found the gap was **wider** than stated:

| Surface | Plan said | Audit found |
| ------- | --------- | ----------- |
| spec/14-self-serve.md | written, contracts moved in | ✔ on disk, all sections present |
| spec/README.md | read-order + Testing + entry-points | ✔ lines 115, 245, 352–365 |
| spec/02-doctypes.md | count 22, Site + Site Request | ✔ #21/#22 full sections |
| spec/11-user-ui.md | perm rows + signup on-ramp | ✔ lines 41/56/62–67 |
| spec/08-images.md | golden-image section (D01-1) | ✔ § line 226 |
| spec/09-roadmap.md | **add self-serve Changes entry** | gap REAL — **and** proxy/TLS also missing → backfilled all three (operator-approved) |

## What this phase did NOT touch (correctly)

```
  ✗ Operator use-cases table (README)  — self-serve is USER-facing, not operator;
                                          no row added (plan §"user vs operator")
  ✗ spec/10-desk-ui.md                 — no operator Desk surface for self-serve
  ✗ Any code / doctype / test          — 06 is docs-only by definition
  ✗ "Address reuse on archive" roadmap — unrelated; left as-is (still deferred)
```
