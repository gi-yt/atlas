# 07 — Fast deploy: sub-5s signup→live-site (no warm pool)

> **STATUS: PLANNED (2026-06-10).** Not started — gated on the in-flight golden
> re-bake (HANDOFF G1) landing first, because step 1 below changes the SAME
> `build.sh` that re-bake produces. Do **not** start until the other session's
> re-bake + rename-model re-proof (HANDOFF G1/G2) is done and committed, or the two
> `build.sh` edits will collide.

**Goal.** Make a verified signup reach a **live, usable site in a few seconds**
without standing up a warm pool of VMs. The target is a real serving site (the
owner can log in), not just an instant status page — but we get there by removing
fixed cost from the per-signup critical path, not by pre-provisioning whole VMs.

**Why this, not a warm pool (decided 2026-06-10).** The microVM is *not* the
bottleneck. Atlas runs firecracker microVMs on a long-lived Server with **instant
LVM CoW clones** (`virtual_machine_snapshot.clone_to_new_vm` → thin
`lvcreate -s`), so a clone boots in single-digit seconds — there is no
droplet-per-site create. The wall-clock today is dominated by **per-signup work
that produces identical output every time** and by **poll-loop sleep slop**, both
removable without paying for idle VMs. A warm pool is the *next* lever if this
isn't enough; it is explicitly out of scope here (and out of scope per
[00-overview](./00-overview.md) "Scope discipline").

## Where the time actually goes today (measured from the code)

The post-verify `Site.auto_provision`
([site.py](../../../atlas/atlas/doctype/site/site.py)) is six **serial** steps.
Ranked by real wall-clock contribution:

| Rank | Cost | Where | Order of magnitude |
| ---- | ---- | ----- | ------------------ |
| 1 | **`bench setup production`** — regenerates the whole supervisor + nginx config and reloads supervisord, **per signup**, producing identical config every time | `deploy-site.py` `_setup_production` → `_bench("setup","production")`; the controller comment already says *"take minutes"* ([deploy_site.py:97](../../../atlas/atlas/deploy_site.py#L97)) | **tens of seconds → minutes** |
| 2 | **Poll-loop sleep slop** — three stacked waits each sleeping in coarse increments; the work finishes mid-sleep and we wait out the rest of the tick | `_wait_for_vm_running` `poll_seconds=5.0` ([site.py:264](../../../atlas/atlas/doctype/site/site.py#L264)); `wait_for_http` `READINESS_POLL_SECONDS=5` ([deploy_site.py:57](../../../atlas/atlas/deploy_site.py#L57)) | **up to ~10s of pure `sleep()`** |
| 3 | **Per-deploy `scp` of the deploy script** + serial SSH handshakes (`mkdir`, `scp`, then the run) | `deploy_site` ([deploy_site.py:85-104](../../../atlas/atlas/deploy_site.py#L85-L104)) | **seconds** |
| 4 | microVM CoW clone + boot to SSH | `provision-vm.py` (thin snapshot + identity inject + firecracker start) | **2–5s** (smallest real contributor) |
| 5 | RQ pickup latency (the `long` queue) | `Site.after_insert` `frappe.enqueue(queue="long")` ([site.py:72](../../../atlas/atlas/doctype/site/site.py#L72)) | seconds, queue-depth dependent |

The thing you'd expect to dominate — kernel boot — is rank 4. The fixed,
repeatable work (rank 1) and the lazy polling (rank 2) are the wins.

## The three changes (this plan)

### Change 1 — Bake `bench setup production` into the golden image

Move `setup production` out of the **per-signup** path and into the **once-per-bake**
path, so a spawned (cloned) VM is already serving on `:80` and the per-signup
deploy is just *rename + reload*.

- **In `bench/build.sh`** (the golden bake, [01](./01-golden-image.md)): after the
  baked `site.local` is created, run `bench setup production` **and** apply the
  IPv6-listener fix, so the **supervisor + nginx config is generated, installed,
  and running in the snapshot**. `dns_multitenant` (Host-header routing) is enabled
  at bake time. A clone therefore boots with nginx already serving every site dir
  by `server_name` on `:80` — no config regen needed to serve a *new* dir.
- **The v6 listener at bake time.** `_enable_ipv6_listeners`
  ([deploy-site.py:187](../../../bench/deploy-site.py#L187)) must run at bake time
  too (the baked nginx config needs `listen [::]:80;` beside each `listen 80;`),
  AND remain correct after the per-signup rename — see Change 1's open question
  below on whether the rename needs any nginx touch at all.
- **In `bench/deploy-site.py`** `_setup_production` becomes the cheap per-signup
  bring-up: **not** `bench setup production`. Because bench-cli's nginx routes by
  `server_name` == site-dir name under `dns_multitenant`, a freshly-renamed
  `sites/<fqdn>/` dir is matched by the **already-baked** config — but bench-cli
  generates **one vhost file per site** under `config/nginx/sites/`, keyed to the
  baked `site.local`. So the per-signup work is the minimal regen that swaps that
  one vhost to the new `server_name` + reload:
  - **Option 1a (regenerate just nginx, not supervisor):** `bench setup nginx`
    (regenerates only `config/nginx.conf` from the now-renamed site dirs) +
    `_enable_ipv6_listeners` + `nginx -s reload`. Supervisor is untouched (the
    workers are whole-bench, not per-site — they already run from the bake). This
    drops the supervisord regen+reload, the slowest part of `setup production`.
  - **Option 1b (no per-site nginx regen at all):** bake the nginx vhost to match
    on a **wildcard `server_name *.<region domain>;`** (or `dns_multitenant`'s
    catch-all) so ANY renamed dir is served with zero per-signup nginx work — the
    rename alone suffices, only a `nginx -s reload` if even that. Verify on a host
    that bench-cli's multitenant nginx resolves the Host to `sites/<host>/` purely
    by dir presence without a per-site vhost.
  - **Decide 1a vs 1b on a host** (it's a bench-cli behavior fact, like the v6
    bind). 1b is strictly faster and is the real prize; 1a is the safe fallback.
    Land 1a first (provably correct), open 1b as a fast-follow once host-confirmed.

> **Net effect:** the per-signup deploy goes from `rename + full setup production`
> (rank-1 cost) to `rename + at most one nginx reload` — sub-second config work
> instead of supervisord regen.

**Coordination with the in-flight re-bake (HANDOFF G1).** That re-bake is producing
a `site.local`-carrying golden under the CURRENT `build.sh` (which does NOT bake
`setup production`). This change ADDS the bake-time `setup production`. Sequence:
let G1 land + prove the rename model first (so the rename model itself is
host-true), THEN add Change 1 and **re-bake once more**. Do not interleave — two
`build.sh` deltas in flight at once is the collision this STATUS warns about.

### Change 2 — Tighten the poll loops to 1s

The microVM is up in single-digit seconds and HTTP-200 follows within a few more;
5s polling wastes most of a tick on `sleep()`. Drop both to **1s**:

- `_wait_for_vm_running` — `poll_seconds: float = 5.0` → **`1.0`**
  ([site.py:264](../../../atlas/atlas/doctype/site/site.py#L264)). The function
  `rollback()`s before each read, so the read is cheap; 1s polling is fine.
- `wait_for_http` — `READINESS_POLL_SECONDS = 5` → **`1`**
  ([deploy_site.py:57](../../../atlas/atlas/deploy_site.py#L57)). The probe is a
  10s-timeout HTTP GET; on a not-ready guest it returns fast (refused/502), so 1s
  between attempts is safe.
- **Leave the *timeouts* generous** (1500s / 600s). Tightening the *poll interval*
  cuts the common-case latency (the slop) without making a slow-but-fine provision
  spuriously fail. Do **not** also cut the timeouts in this change — that's a
  different risk and would make a legitimately-slow host flap to `Failed`.
- The IDE-selected line `READINESS_POLL_SECONDS` ([deploy_site.py:118] arg default)
  is exactly the knob; both call sites already thread it through, so this is a
  two-constant edit + a unit-test assertion that the loop honors 1s.

> Unit cost: the `wait_for_http` and `_wait_for_vm_running` poll/timeout tests
> ([test_deploy_site.py](../../../atlas/atlas/test_deploy_site.py),
> [test_site.py](../../../atlas/atlas/doctype/site/test_site.py)) mock the probe;
> update any test that pins the old 5s interval, assert the new 1s.

### Change 3 — Pre-bake the deploy script on the image; only execute

Stop `scp`-ing `deploy-site.py` on every deploy. **Bake the script into the golden
image** (it's already a committed, self-contained stdlib-only file —
[deploy-site.py](../../../bench/deploy-site.py)), so the per-signup path is a
single SSH **execute**, not `mkdir + scp + execute`.

- **In `bench/build.sh`:** install `deploy-site.py` to a fixed durable path in the
  guest (e.g. `/usr/local/bin/atlas-deploy-site` or `/root/bench-cli/deploy-site.py`),
  alongside the baked bench-cli — the same way host scripts land durably under
  `/var/lib/atlas/bin/atlas/` (memory: [[atlas-scripts-python-port]]).
- **In `atlas/atlas/deploy_site.py`** `deploy_site`: drop the `mkdir` + `run_scp`
  ([deploy_site.py:88-92](../../../atlas/atlas/deploy_site.py#L88-L92)); run the
  baked path directly:
  ```python
  command = (
      f"python3 {BAKED_DEPLOY_PATH} "
      f"--site-name {shlex.quote(site_name)} "
      f"--admin-password {shlex.quote(admin_password)}"
  )
  stdout, stderr, code = run_ssh(connection, key_path, command, timeout_seconds=600)
  ```
  One SSH round-trip instead of three. The admin password still crosses only as an
  argv flag over the encrypted channel (unchanged — never a guest file).
- **Fallback for old clones.** A clone from a *pre-this-change* snapshot won't have
  the baked script. Two honest options:
  - **Preferred:** gate on the snapshot — this rides the SAME re-bake as Change 1
    (one new golden carries baked `setup production` + baked `deploy-site.py`), and
    `default_bench_snapshot` points at it. No mixed fleet; old snapshots are
    retired (HANDOFF G3 already retires them). State this dependency loudly.
  - If a defensive fallback is wanted: `test -f $BAKED_DEPLOY_PATH` over SSH and
    `scp` only on miss. Costs a probe round-trip; skip unless mixed snapshots are a
    real operational concern (they shouldn't be — keep exactly one golden).

> **Toward the baked CLI (the stated direction).** This is step one of "move to a
> CLI that has all the local actions baked in." Today `deploy-site.py` is one
> baked entrypoint; the durable shape is an `atlas-guest` CLI baked into the image
> exposing `deploy-site`, the readiness self-check, and future per-site actions as
> subcommands — controller-side becomes `ssh … atlas-guest deploy-site --site … `.
> This plan bakes the ONE script; the multi-subcommand CLI is a fast-follow (see
> "Follow-ups"), not this plan's scope — don't build the whole CLI here.

## What this does NOT change (scope fence)

- **No warm pool of VMs.** Deferred by decision. If sub-5s isn't met after these
  three, the pool is the next plan, not a creep into this one.
- **Contract B is untouched.** `wait_for_http`'s **HTTP-200-only** predicate stays
  the single signal that flips a Site to `Running` — Change 2 tightens the *poll
  interval*, never the predicate or the meaning. Do not "optimize" readiness back
  to VM `status == Running`.
- **Contract A / C untouched.** One routing string; verify-before-insert ordering.
- **The orchestration deadlock fix stays.** `auto_provision` still commits after
  the clone before waiting on the separate boot job
  ([site.py:192-199](../../../atlas/atlas/doctype/site/site.py#L192-L199)) — do not
  collapse that; it is load-bearing (DRIFT M-4).

## Build steps (in order)

1. **Wait for HANDOFF G1/G2** — the rename-model golden is baked, wired, and the
   rename flow is host-proven + committed. (This plan's STATUS gate.)
2. **Change 2 first (cheapest, independent).** Two constants → 1s + unit-test
   updates. No host needed; ship it.
3. **Change 1 + Change 3 together** (they share one re-bake): edit `build.sh` to
   bake `setup production` + the v6 listeners + install `deploy-site.py` to the
   durable path; trim `deploy-site.py` `_setup_production` to the cheap per-signup
   bring-up (1a); drop the `scp` in `deploy_site.py`. Re-bake **one** new golden,
   point `default_bench_snapshot` at it, retire the old (HANDOFF G3).
4. **Decide 1b** on the host (wildcard/no-per-site-vhost) — if it holds, drop the
   per-signup nginx regen entirely; re-bake; otherwise keep 1a.
5. **Measure.** Time a fresh signup → first HTTP 200 → `Running` on the real path
   (the `self_serve_site` e2e already does the full chain). Record the number; if
   not under 5s, the residual is rank-2/4/5 (RQ pickup, boot) → that's the
   warm-pool conversation, captured as a follow-up.

## How it's proven

- **Unit (milliseconds):**
  - The 1s poll intervals are honored — `wait_for_http` / `_wait_for_vm_running`
    loop tests assert the new interval (probe mocked).
  - `deploy_site` runs the **baked path** with **no `scp`** — assert the SSH
    transport mock sees a single `run_ssh` execute and **no** `run_scp`
    ([test_deploy_site.py](../../../atlas/atlas/test_deploy_site.py)).
  - `deploy-site.py` `_setup_production` calls the cheap bring-up (1a: `setup
    nginx` + reload), **not** `setup production` — assert via the subprocess seam.
- **Host facts (plan 05 e2e — `self_serve_site`):** the real chain on the rename
  golden, now timed. Asserts (a) a clone serves on `:80` **without** a per-signup
  `setup production` (Change 1 host-true), (b) the deploy runs from the **baked**
  script with no upload (Change 3 host-true), (c) the end-to-end signup→`Running`
  wall-clock. The proxy→site south hop should exercise a **real** bench-cli vhost,
  not the echo stand-in (the gap HANDOFF M-5 / §5 flags — a real-vhost assertion so
  the v6/serve path can't silently regress).

## Spec & docs (slice of [06](./06-spec-and-docs.md))

- **`spec/14-self-serve.md`** — "The in-guest deploy" currently says the per-signup
  deploy runs `bench setup production` ([spec/14:204-215](../../../spec/14-self-serve.md));
  rewrite to: `setup production` is **baked** (golden image), the per-signup deploy
  is **rename + cheap nginx reload**, and the deploy script is **baked, executed
  not uploaded**. State the latency intent (sub-5s) and the poll-interval choice.
- **`spec/08-images.md`** — the golden bake now bakes `setup production` + the v6
  listeners + the deploy script. Add to the "what's baked" list (the rename
  rationale already lives there).
- **`bench/README.md`** — "Serving model": a clone serves immediately; per-signup
  is rename + reload; the deploy CLI is baked.
- **[09-roadmap.md](../../../spec/09-roadmap.md)** — note the fast-deploy hardening
  under the self-serve milestone.
- **DRIFT.md** — record any 1a/1b host findings and the measured wall-clock.

## Follow-ups (explicitly NOT this plan)

- **Warm pool of pre-claimed VMs** — the next lever if sub-5s isn't met. Boot +
  RQ-pickup (ranks 4/5) are the only residuals these three changes can't remove.
- **`atlas-guest` baked CLI** — fold `deploy-site` + the readiness self-check +
  future per-site ops into one baked multi-subcommand CLI (the stated direction;
  Change 3 is its first entrypoint).
- **Push-not-poll readiness** — replace the controller's HTTP poll with a guest
  signal (vsock / status file) so the readiness wait has zero poll slop. Bigger
  surface than a constant change; defer.
