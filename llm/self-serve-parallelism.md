# Self-serve bench — remaining work

Status: planning note, 2026-06-05; trimmed to remaining-only 2026-06-08;
**superseded 2026-06-09** — the self-serve site layer is built and in spec.
Companion to [proxy-design.md](./proxy-design.md) and [ideas.md](./ideas.md).

The end-to-end **signup → live Frappe site** flow is built and folded into spec:

- **Spec:** [`spec/14-self-serve.md`](../spec/14-self-serve.md) is the durable
  contract (the three frozen contracts A/B/C, the `Site` / `Site Request`
  DocTypes, the in-guest deploy, the golden bench image, the host-bound e2e).
  Cross-cuts: [`08-images.md` § golden bench image](../spec/08-images.md),
  [`02-doctypes.md`](../spec/02-doctypes.md) (Site #21, Site Request #22),
  [`11-user-ui.md`](../spec/11-user-ui.md) (signup on-ramp + `if_owner` perms),
  and the `v0.9` entry in [`09-roadmap.md`](../spec/09-roadmap.md).
- **Plans:** the build was decomposed into
  [`llm/plans/self-serve/`](./plans/self-serve/00-overview.md) (01 golden image,
  02 Site doctype, 03 deploy-site, 04 signup, 05 e2e, 06 spec/docs), with the
  planned-vs-actual drift tracked in
  [`plans/self-serve/DRIFT.md`](./plans/self-serve/DRIFT.md).

This file's earlier "Tracks to build" + "Contracts to freeze" content is now
fully captured there — the contracts moved into `spec/14-self-serve.md` as their
durable home (the same way `proxy-design.md` was trimmed to rationale +
not-yet-built after the proxy shipped).

---

## Remaining (host-bound, not yet proven on a real droplet)

The code, unit tests, and spec are all done; what is **not** yet done is the
single end-to-end host run that proves it on real infra. Per the plans'
definition-of-done, that run is an operator turn (it is billable). Carried open
items live in [`DRIFT.md`](./plans/self-serve/DRIFT.md):

- **The golden bake** (`bench/build.sh`) has never run on a real droplet — the
  apt/clone/uv/node bake, `bench init` on Python 3.14 in the guest, and the 12 GB
  disk sizing are host facts (D01 open, D05 open).
- **`bench setup production` binding `[::]:80`** — the proxy's south hop is
  v6-only; if bench-cli's nginx binds v4-only the deploy must add an explicit
  `listen [::]:80` (D03 open).
- **`is_setup_complete` persisting** via `bench frappe … execute` (D03 open).
- **The full `self_serve_site` e2e** — imports clean, all reused seams resolve,
  every layer's unit suite is green, but the host facts (golden clone serves,
  worker-driven `Running`, v4+v6 inbound) are proven only by the real run
  (D05 open). Run it with:
  `bench --site atlas.tests.local execute atlas.tests.e2e.use_cases.self_serve_site.run_smoke`
  (worker must be up; grep the log for FAIL).

Trim or delete this file once that host run lands and D01/D03/D05 close.
