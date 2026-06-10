# 03 — deploy-site.py + readiness gates (T2)

> **STATUS: BUILT + unit-green (2026-06-09).** `bench/deploy-site.py` (in-guest)
> and `atlas.atlas.deploy_site` (`deploy_site` + `wait_for_http`) are implemented,
> wired into `Site.auto_provision`, and unit-tested
> (`atlas/atlas/test_deploy_site.py`, `test_site.py`). The divergences from the
> plan below — serving via bench-cli's own nginx on `:80` (`setup production`, NOT
> a dev `bench start`), the admin password stored encrypted on `Site.admin_password`,
> the readiness probe targeting `/api/method/ping` with the FQDN Host header, and
> the self-contained guest script — are recorded in [DRIFT.md](./DRIFT.md)
> (D03-1…D03-4). Spec: [spec/14-self-serve.md](../../../spec/14-self-serve.md) "The
> in-guest deploy". **Host facts deferred to plan 05** (the real bake + serve):
> bench-cli nginx's IPv6 `:80` bind, the `setup production` install path, and
> `is_setup_complete` persistence — see DRIFT "Open / to verify on a host (Phase
> 03)".

> **FINDINGS FROM PHASE 02 (read before building).** Phase 02 built the `Site`
> orchestration (`atlas.atlas.doctype.site.site.auto_provision`) with the deploy +
> readiness steps as **module seams it already calls**. To plug in, plan 03 must
> create **`atlas/atlas/deploy_site.py`** exposing exactly these two functions
> (the seam imports in `site.py`):
>
> ```python
> def deploy_site(virtual_machine_name: str, site_name: str) -> None: ...
> #   drives deploy-site.py in the guest over connection_for_guest(vm).
> #   site_name is the full FQDN (Contract A) — the bench new-site name on disk.
> def wait_for_http(ipv6_address: str) -> None: ...
> #   blocks until the guest answers HTTP 200 on :80 over its public /128
> #   (bracket the v6 literal); raise frappe.ValidationError on timeout.
> ```
>
> If a different shape fits better, change the two seams in `site.py`
> (`_deploy_site`, `_wait_for_http`) to match — they are thin wrappers, and the
> Site unit tests mock at that boundary so the contract is the only coupling.
> Other Phase-02 facts that constrain this plan:
> - **The backing VM is a *clone* of `Atlas Settings.default_bench_snapshot`**
>   (the golden bench), reached as `root` over guest-SSH with the **fleet** key
>   (`Atlas Settings.ssh_public_key`), exactly like `bench_image.build_bench` /
>   `proxy.build_proxy`. There is no per-Site user SSH key in the guest.
> - **The site name on disk is the FQDN, verbatim** (`acme.blr1.frappe.dev`) — do
>   not transform it (Contract A). It is also the proxy Host header the in-guest
>   Frappe must answer for (`host_name` + `dns_multitenant`, the
>   `vm-inbound-ipv6-only` shape).
> - **`wait_for_http` is the ONLY thing that flips `Site → Running`** (Contract
>   B). Pin the "ready" predicate **past** the setup-wizard gate (memory:
>   `fresh-site-setup-gate` — `is_setup_complete`), or the orchestration will mark
>   a half-installed site Running.
> - The MariaDB root password is **baked** in `bench/bench.toml` (D01-3); generate
>   + return only the per-site **Administrator** password. Decide where it lands
>   for the owner (a `Site` field? shown-once in the SPA?) — coordinate with the
>   Phase-04 signup UX; if it becomes a `Site` field, add it in this plan and note
>   it in `spec/14-self-serve.md`.

**Goal.** The in-guest script that turns a booted golden VM (01) into a serving
Frappe site, plus the `wait_for_http` readiness gate that lets a `Site` (02) flip
to `Running` only on an observed **HTTP 200** (Contract B). This is the one piece
that runs `bench` *inside* the guest.

**Gates on:** 01 (needs a booted golden VM to run against). **Provable once:** 01
exists — run it against any golden VM, independent of 02/04.

## What runs where

Two distinct execution paths, both already in the codebase:

- **`deploy-site.py` runs *in the guest*.** Like the proxy's `build_proxy`, it's
  driven over the **SSH-to-guest** path
  ([connection_for_guest](../../../atlas/atlas/_ssh/runner.py), reaching the VM's
  public IPv6 `/128` as root with the Atlas key). It is *not* a host Task —
  there's no `Server` row for a guest. (Contrast: the host-side scripts in
  [scripts/](../../../scripts/) run via `run_task(server=…)`.)
- **`wait_for_http` runs *on the controller*.** Like
  [wait_for_ssh](../../../atlas/atlas/_ssh/transport.py), it polls from the Atlas
  side. It probes the guest `:80` over the VM's `/128` — the same south-hop path
  the proxy uses — and returns when it sees a 200.

## `deploy-site.py` — the script

A typed-Python script following the [scripts/](../../../scripts/) idiom: a frozen
`DeploySiteInputs(TaskInputs)` dataclass with `--kebab-case` flags via
`from_args`, stdlib-only body, one `ATLAS_RESULT={json}` line via `emit()`. Even
though it's invoked over guest-SSH rather than `run_task`, keep the same shape so
it parses uniformly.

### Inputs
- `site_name` — the full FQDN (Contract A): the site-name-on-disk **is** the
  routing string. `bench new-site acme.fra1.frappe.dev`.
- `admin_password` — the Frappe Administrator password (generated by the
  controller, returned to nobody but stored for the owner — decide where; see
  open questions).
- ~~whatever bench/db secret the golden image deferred~~ — **RESOLVED in 01**
  (see [DRIFT.md](./DRIFT.md) D01-3): the MariaDB root password is baked,
  fixed, localhost-only in `bench/bench.toml`. `deploy-site.py` does **not**
  materialize a db secret; it only generates + returns the per-site **Admin**
  password. `bench new-site` reads the db root password from the baked
  `bench.toml` (the bench it runs in is `~/bench-cli/benches/atlas`).

### Steps (driving bench-cli, preinstalled by 01)
1. **Pre-flight.** Assert bench-cli + the Frappe clone are present (the golden
   image baked them). Fail loud if not — a plain-Ubuntu VM is the wrong image.
2. **Per-VM secret.** Materialize the MariaDB/`bench.toml` secret for this VM if
   01 deferred it to deploy time (don't bake a shared secret — 01's open Q).
3. **`bench new-site <site_name>`** with the admin password. The site name on disk
   is the FQDN, verbatim (Contract A).
4. **Configure the site to answer for its Host header.** The proxy forwards with
   `Host: acme.fra1.frappe.dev`; the in-guest Frappe must serve that host (the
   `host_name` / `dns_multitenant` shape — mirror what the
   `vm-inbound-ipv6-only` work established: site-name == Host + multitenant on).
5. **Start serving on `:80`.** `bench start` (or the production-ish bring-up) so
   the guest answers HTTP on `:80` — the port the proxy's south hop dials and
   `wait_for_http` probes. The bench-cli Admin UI (`:8002`) is the "drop into the
   bench admin" surface from the idea doc — decide if/how it's exposed (likely
   *not* publicly; it's reached through the site or a separate authenticated
   path — flag, don't over-build).
6. **Emit** `ATLAS_RESULT={"site": "...", "admin_ready": true, ...}` — whatever
   02's orchestration needs to record on the `Site` row.

> Keep it idempotent where cheap: re-running on a VM that already has the site
> should not double-create. Mirror the `sync-image.py` "exists → skip" spirit.

## `wait_for_http` — the readiness gate (Contract B)

New helper alongside `wait_for_ssh` in
[atlas/atlas/_ssh/](../../../atlas/atlas/_ssh/) (or a small `readiness.py`):

```
def wait_for_http(host, *, port=80, path="/", timeout_seconds=..., poll_seconds=...) -> None:
    # poll http://[<ipv6>]:80/ until a 2xx (or the documented "ready" status),
    # raise frappe.ValidationError on timeout. Mirror wait_for_ssh's structure:
    # deadline = monotonic()+timeout; loop; sleep; raise on deadline.
```

- **The signal is HTTP 200 from the guest `:80`**, not VM `status == Running`.
  That distinction *is* Contract B — bake it into the docstring so nobody
  "optimizes" it back to the VM status.
- Probe over the VM's public `/128` (bracketed IPv6 — the `scp v6 needs brackets`
  trap from the real-provision work applies to any v6 URL). The controller is
  off-host, so this is an honest end-to-end probe (a host-local probe would skip
  the real network path).
- Decide the exact "ready" predicate: a bare `/` 200 may redirect to setup; a
  more honest probe is a known endpoint that only returns 200 once the site is
  installed and Administrator is set (cf. the `fresh-site-setup-gate` learning —
  a fresh site can be trapped at `/setup-wizard`; the readiness predicate must be
  past that gate, e.g. `is_setup_complete`). Pin this predicate; 02's status
  transition depends on it.

## Open questions to resolve while building

- **Admin password handoff.** The idea doc wants the user "dropped into the bench
  with admin". Where does the Administrator password live so the owner can use
  it? Options: shown once in the SPA after `Running`, stored on the `Site` row
  (encrypted), or a magic-login link. Decide with 02 (it's a `Site` field/method)
  and 04 (it's part of the post-verification UX).
- **`:80` plaintext vs the proxy's TLS.** TLS terminates at the proxy; the south
  hop to the guest `:80` is plaintext over public v6 (accepted limitation, proxy
  design). `deploy-site.py` configures the site for plain `:80`; it does **not**
  run `bench setup production`/certbot in the guest (the proxy owns TLS). This
  *removes* steps 10–11 of the old manual flow (idea doc) — call that out so
  nobody re-adds in-guest TLS.
- **Shared secret with 01.** The MariaDB/`bench.toml` password: bake-per-VM at
  provision vs generate at deploy. One owner, one decision — coordinate.

## How it's proven

- **Host fact:** against a booted golden VM, run `deploy-site.py` over guest-SSH,
  then `wait_for_http` until 200. Assert the site answers on `:80` for its Host
  header. This is the core of the e2e ([05](./05-e2e-proof.md)) but is runnable
  standalone the moment 01 produces a golden VM — it does **not** need 02/04.
- **Unit:** `DeploySiteInputs.from_args` parsing, the result-shape, and
  `wait_for_http`'s timeout/poll logic (mock the probe) — milliseconds.

## Spec & docs (slice of [06](./06-spec-and-docs.md))
- New `spec/14-self-serve.md` — document `deploy-site.py` (the in-guest deploy,
  driven over guest-SSH) and the `wait_for_http` readiness gate; state Contract B
  explicitly.
- Cross-link [spec/06-networking.md](../../../spec/06-networking.md) for the
  `:80` south-hop path and [spec/04-tasks.md](../../../spec/04-tasks.md) for the
  script idiom (noting this one runs in the guest, not via `run_task`).
