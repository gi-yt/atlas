# 05 — signup → live-site e2e proof (Phase 7)

> **BUILT (import + preflight green; host run = operator turn).** The e2e module
> is `atlas/tests/e2e/use_cases/self_serve_site.py` — see DRIFT D05-1…D05-6 and
> the wireframe `wireframes/05-e2e-proof.md` for what was built vs planned. In
> brief: it reuses `proxy_vm` / `tls_issuance` / `bench_image` helpers (D05-1),
> resolves-or-bakes the golden snapshot (D05-2), adds the first **inbound v6**
> assertion (D05-3), waits on the **worker-driven** `auto_provision` chain (D05-4),
> quiets other active Root Domains so `active_root_domain()` is unambiguous
> (D05-5), and probes `/api/method/ping`→`pong` off-droplet on v4 + v6 (D05-6). It
> skips clean (`MissingConfig` / preflight) before anything billable. **Not yet
> run on a real droplet** — host facts are an operator turn:
> `bench --site atlas.tests.local execute atlas.tests.e2e.use_cases.self_serve_site.run_smoke`.

> **FINDINGS FROM PHASE 04 (read before building the e2e).** The signup→verify
> backend is built + unit-green; this e2e drives the *real* path on top of it:
> - **Drive signup with the real API**, `atlas.atlas.api.signup.request_site(email,
>   subdomain)` (guest-callable). It inserts a `Site Request` (Pending) and queues
>   the verification email — it does **not** create a Site/VM (that's the whole
>   Contract-C point; assert the negative here).
> - **To skip SMTP**, fulfil directly: look up the `Site Request` by email/token
>   and call `SiteRequest.verify()` (the same method the `/verify` route calls). It
>   creates the User, inserts the `Site` as owner, returns the `Site`. That `Site`'s
>   `after_insert` enqueues `auto_provision` — so in a *real* (non-`in_test`) run on
>   the droplet, fulfilment kicks off the golden-VM clone → deploy → 200 → Subdomain
>   chain automatically. (In unit tests `enqueue` is suppressed; on the e2e host it
>   runs — confirm the worker is up.)
> - **The admin-password handoff is backend-only today.** After `Running`, read it
>   with `site.get_password("admin_password")` — there is no SPA reveal yet (the
>   Sites screen is deferred plan-04 SPA work, see DRIFT D04-6/deferred). The e2e can
>   assert the password is non-empty + that a login with `Administrator` + that
>   password against `https://<fqdn>` succeeds, without any SPA.
> - **Token expiry is 24h from `creation`** (`SiteRequest.TOKEN_TTL_HOURS`); an
>   expired-token negative is unit-covered, so the e2e need not wait 24h.
> - **Teardown also drops the `Site Request` + the created `User`** (a real User row
>   persists past the transaction — the e2e is non-transactional). Add both to the
>   `finally`.

**Goal.** One host-bound integration test that proves the whole flow: a signup,
an email verification, and a few seconds later a Frappe site live at
`acme.<region>.<domain>` over **both IPv4 and IPv6** through the proxy. It
consumes 01–04 plus the already-built proxy + TLS layers, so it's last by
definition.

**Gates on:** 01, 02, 03, 04 — and the live proxy/TLS infra. **Provable:** only
end-to-end on real droplets.

## What only this can prove (host facts)

Everything below 02's validators and 03/04's pure logic is unit-covered; this
e2e exists for the facts a real droplet + real DNS + real ACME prove and nothing
else can (spec README "Host facts vs unit-covered logic"):

1. **The golden image actually serves.** A VM from 01's image, after 03's
   `deploy-site.py`, answers HTTP 200 on `:80` for its Host header — the full
   bake→provision→boot→deploy chain survives.
2. **The readiness signal is real.** `wait_for_http` observes a genuine 200 from
   *off the droplet* (the controller's honest vantage), and the `Site` flips to
   `Running` on *that*, not on VM-status.
3. **The proxy routes the new subdomain end-to-end.** Once 02 creates the
   `Subdomain`, an **off-droplet** request to `https://acme.<region>.<domain>`
   reaches the site through the proxy — TLS terminated at the proxy with the
   pushed wildcard cert, south hop to the guest `:80`. Prove it over **both** the
   reserved IPv4 (proxy's attached v4) **and** public IPv6 (the idea doc's
   "works on IPv4 and IPv6" requirement).
4. **Verification gates provision.** Asserting the *negative*: an unverified
   `Site Request` creates **no** VM and **no** Site (Contract C) — billable work
   only after the token is consumed.

## Where it lives

A new e2e use-case module — recommend
[atlas/tests/e2e/use_cases/](../../../atlas/tests/e2e/use_cases/)`self_serve_site.py`
— with `run()` + `run_smoke()`, mirroring the existing host-bound modules. The
closest templates to copy:

- [proxy_vm.py](../../../atlas/tests/e2e/use_cases/proxy_vm.py) — builds a stack in
  a guest, routes a stand-in site, proves the south hop + inbound `:443` from
  off-droplet, with a `finally` teardown (release reserved IP, terminate VMs).
- [tls_issuance.py](../../../atlas/tests/e2e/use_cases/tls_issuance.py) — the real
  LE→DNS-01→certbot→push chain; skips cleanly (`MissingConfig`) without the
  `atlas_tls_*` config keys, before any billable provision.

This module is the **superset**: a real signup → verify → golden VM → deploy →
200 → subdomain → proxy → off-droplet HTTPS on v4 *and* v6.

## Shape (reusing the substrate)

- Reuse the **shared bootstrapped droplet** where possible
  ([_droplets.py](../../../atlas/tests/e2e/), the `phase()` context manager) so we
  don't pay a fresh provision per run — except the *site VM* itself, which must
  boot from 01's golden image (that's the point).
- Needs the live proxy + wildcard cert in the region (the `tls_issuance` /
  `proxy_vm` preconditions). Gate on the same config keys and **skip cleanly**
  (raise before any billable provision) when they're absent — match
  `tls_issuance`'s preflight discipline so CI on a bare site doesn't try to
  provision.
- **`finally` teardown** is mandatory and billable-aware: terminate the site VM,
  delete the `Subdomain`, release any reserved IP, delete the `Site` /
  `Site Request` rows. Tag every created droplet `atlas-e2e` (the harness sweep
  relies on it).

## Watch-outs (carried from prior host-bound work)

- **macOS worker fork crash** — a worker lacking
  `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` hangs provisioning (the
  `atlas-macos-worker-fork-crash` learning). Same trap will bite this run.
- **Stale host key on recycled DO IPs** — `ssh-keygen -R` the known_hosts before
  trusting a reused IP (the real-provision learning).
- **v6 URLs need brackets** — every IPv6 HTTP/scp target is `[<addr>]` (the
  real-provision learning); applies to 03's `wait_for_http` and this test's v6
  request.
- **Py3.14 vs older remote Python** — script syntax that's valid on the 3.14
  controller can be a hard `SyntaxError` on the droplet; clear stale `.pyc` and
  don't trust a local import as proof the guest will run it (the `py314-except`
  learning). Relevant to `deploy-site.py`.
- **`run_smoke` exits 0 even on FAIL** — grep the log, don't trust the exit code
  (the LVM-traps learning).

## Smoke vs full

- `run_smoke()` — the host facts only: real signup→verify (or a direct verified
  fulfilment to skip SMTP), golden VM boot, deploy, 200, off-droplet HTTPS on v4
  + v6. This is the dev-loop slice.
- `run()` — smoke plus the unit-redundant validation throws under one umbrella
  (Contract-A label/denylist, Contract-C ordering negative path), per the spec's
  full-vs-smoke convention.
- Wire into the runners per spec README "Entry points": this module owns its
  flow; decide whether it joins `run_all_smoke` (it provisions a *second* VM from
  a special image, so it may be invoked directly like `tls_issuance`, not folded
  into the shared-droplet smoke — lean that way to keep `run_all_smoke` cheap).

## Spec & docs (slice of [06](./06-spec-and-docs.md))
- New `spec/14-self-serve.md` — the "Host-bound facts — the `self_serve_site`
  e2e" section (mirror proxy/TLS chapters' e2e sections).
- [spec/README.md](../../../spec/README.md) — add the e2e module to the test
  mapping list; if signup is treated as a user use case, note it where the SPA /
  user-facing tests are catalogued (see [06](./06-spec-and-docs.md) on
  user-facing-vs-operator).
