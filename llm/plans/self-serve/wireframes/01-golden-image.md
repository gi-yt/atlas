# Phase 01 wireframe — golden bench image (bake flow)

Phase 01 has no user-facing UI; it is host-side image baking. The "wireframe" is
the bake flow and the artifacts it produces, drawn from the actual
implementation (`bench/build.sh`, `atlas/atlas/bench_image.py`,
`atlas/tests/e2e/use_cases/bench_image.py`).

## Bake flow (operator action: bench_image.run)

```
 operator: bench execute …bench_image.run
        │
        ▼
 ┌──────────────────────────────┐
 │ ensure_image_on_server       │  plain ubuntu-24.04 synced to the server
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │ _provision_build_vm          │  plain VM, 2 vCPU / 2 GB / 12 GB disk
 │  trusts ephemeral+atlas keys │  wait_for_vm_running → Running
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐   over guest-SSH (connection_for_guest):
 │ bench_image.build_bench(vm)  │   scp bench/ tree → run build.sh
 │                              │   ├─ apt: ca-certs curl git build-essential
 │  (mirrors proxy.build_proxy) │   ├─ install bench-cli @ pinned commit
 │                              │   ├─ bench new atlas + drop bench.toml
 │                              │   ├─ bench init  ← MariaDB+Redis, uv venv,
 │                              │   │              Frappe clone, node, admin UI
 │                              │   └─ enable mariadb/redis on boot (site-less)
 └──────────────┬───────────────┘   records a `bench-build` Task row
                ▼
 ┌──────────────────────────────┐
 │ _assert_bench_works(vm)      │  ssh: bash -lc 'bench -b atlas list-apps'
 │                              │  → asserts exit 0 and "frappe" present
 └──────────────┬───────────────┘  (the host fact plan 01 exists to prove)
                ▼
 ┌──────────────────────────────┐
 │ vm.stop() → vm.snapshot()    │  Stopped (clean unmount) → snapshot LV
 └──────────────┬───────────────┘
                ▼
        Virtual Machine Snapshot  ◀── "the golden bench image"
        (status Available, rootfs_path = /dev/atlas/atlas-snap-<id>)
                │
                ▼  site VMs clone from it (plan 02 → 03)
        clone_to_new_vm(...)  →  a fresh VM with bench preinstalled
```

## Artifacts produced

| Artifact | Where | Role |
| -------- | ----- | ---- |
| `bench/build.sh` | repo root, beside `proxy/` | authoritative in-guest bake recipe |
| `bench/bench.toml` | repo root | pins Frappe branch + localhost db secret |
| `atlas.atlas.bench_image.build_bench` | controller | upload tree + run build over guest-SSH |
| `bench-build` Task row | DB | audit trail of each bake (Success/Failure) |
| `Virtual Machine Snapshot` (golden-bench) | DB + LVM | the rollable golden image |

## Task list surface (audit trail)

A bake shows up in the operator's existing Task list exactly like a proxy build:

```
 Task                 Script        VM                  Status
 ──────────────────── ───────────── ─────────────────── ────────
 <task-id>            bench-build   golden bench—build  Success
```
