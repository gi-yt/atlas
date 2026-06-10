# 02 — Site DocType + routing read API (T1)

> **STATUS: BUILT + unit-green (24/24).** See [DRIFT.md](./DRIFT.md) Phase 02
> (D02-1…D02-5) for what diverged. Key resolutions: region/domain come from the
> single active `Root Domain` (not separate config, D02-1); the golden image is
> resolved via `Atlas Settings.default_bench_snapshot` + `clone_to_new_vm`
> (D02-2); the Phase-03 deploy + http-wait are module seams (`_deploy_site`,
> `_wait_for_http`) fully unit-tested by mocking — **plan 03 must supply
> `atlas.atlas.deploy_site.deploy_site(vm, site)` and
> `wait_for_http(ipv6)`** (D02-3). The "routing read API" was **not** built: the
> standard Frappe `get`/`get_list` + `owner_only` scoping already covers the
> user-facing read, and the proxy gets everything from `Subdomain` — so no
> bespoke endpoint was needed (the plan's own "Don't over-build" note, confirmed).

**Goal.** The missing **user-facing resource**. `Subdomain` is the *proxy map*
(an operator/control-plane row); `Site` is the thing a user owns — "my Frappe
site at `acme.fra1.frappe.dev`". Pure Frappe: a new doctype, owner-scoped
permissions, the state machine that drives provision→deploy→running, and a
whitelisted read endpoint the SPA lists from. Unit-testable in milliseconds; no
host.

**Gates on:** nothing once Contracts A/B/C are frozen ([00](./00-overview.md)).
**Provable once:** unit suite, seconds.

## The model

A `Site` row is the user-owned aggregate that ties together the routing identity,
the backing VM, and the readiness state. It is **not** the `Subdomain` (which it
creates) and **not** the `Virtual Machine` (which it owns/creates).

### Fields (sketch — finalize in the doctype JSON)

| Field | Type | Notes |
| ----- | ---- | ----- |
| `subdomain` | Data | single DNS label, e.g. `acme`; **autoname source** + immutable |
| `name` (autoname) | — | the full FQDN `acme.<region>.<domain>` = the one routing string (Contract A) |
| `region` | Link/Data | which regional wildcard + proxy fleet; immutable |
| `virtual_machine` | Link | the backing VM (filled by the controller, not the user); immutable |
| `status` | Select | `Pending → Provisioning → Deploying → Running` / `Failed` / `Terminated` (Contract B) |
| `owner` | (Frappe built-in) | the verified user (Contract C); ownership scoping key |

- **Autoname = the routing string.** `name` is `{subdomain}.{region}.{domain}`,
  built once at insert. Never transform it afterward — it is simultaneously the
  site-name-on-disk, the proxy Host header, and the doctype key (Contract A).
- **Immutability.** `subdomain`, `region`, `virtual_machine` go in an
  `IMMUTABLE_AFTER_INSERT` tuple guarded in `validate()` — copy the
  [subdomain.py](../../../atlas/atlas/doctype/subdomain/subdomain.py) /
  [virtual_machine.py](../../../atlas/atlas/doctype/virtual_machine/virtual_machine.py)
  idiom exactly.
- **Status is controller-written, read-only on the form.** The values encode
  Contract B: `Running` is reached **only** when 03's HTTP-200 probe fires, not
  when the VM is `Running`.

## Validation (the routing-string contract, item 1–2 of T1)

In `validate()` / `before_insert()`:

1. **Single label, no dots.** Reject a `subdomain` containing `.` (would escape
   the wildcard). Enforce DNS-label rules (lowercase, `[a-z0-9-]`, no leading/
   trailing `-`, length cap).
2. **Reserved-name denylist.** Reject `www admin api proxy app dashboard mail ns
   root …` (freeze the full list here — Contract A). A module-level tuple.
3. **Uniqueness.** The FQDN is the doctype key, so Frappe enforces uniqueness for
   free — but throw a *clean* "subdomain taken" message, not a raw duplicate-key
   error, because a user hits this (it's the signup race in 04).

## State machine & lifecycle hooks

Copy the VM/Subdomain controller skeleton:

- `before_insert()` — fill defaults the user didn't pick: the backing VM's
  server/image via [placement.py](../../../atlas/atlas/placement.py), set
  `status = Pending`. (Do **not** stamp `owner` here — Frappe stamps it from the
  session user; 04 ensures that's the verified user.)
  - **NOTE (from 01, see [DRIFT.md](./DRIFT.md) D01-2):** the golden bench image
    is a **`Virtual Machine Snapshot`**, not a `Virtual Machine Image` row. The
    backing VM is **not** created with `image=<golden>`; it is cloned from the
    snapshot via `Virtual Machine Snapshot.clone_to_new_vm(...)` (which carries
    `source_image` + the grown `disk_gigabytes`). So placement here resolves the
    golden *snapshot* name (an `Atlas Settings` link, e.g.
    `default_bench_snapshot`), and the background entrypoint provisions the VM
    through the clone path — confirm/seed that snapshot exists, fail loud if not.
- `after_insert()` — enqueue the provision→deploy background job (mirrors
  `VirtualMachine.after_insert`). `queue="long"`, because it SSHes.
- Background entrypoint (module-level fn, like `auto_reconcile_region`):
  1. create + provision the backing VM (existing VM machinery),
  2. wait for the VM to boot (existing `wait_for_ssh`),
  3. run `deploy-site.py` in the guest (03),
  4. **wait for HTTP 200** (03's `wait_for_http`) — the readiness gate,
  5. create the `Subdomain` row (this is what makes the proxy route it),
  6. set `status = Running`. On any failure: `status = Failed` (fail loud).

  Steps 3–4 are 03's contract; this plan owns the *orchestration*, 03 owns the
  *script + probe*.

- **Terminate** (`@frappe.whitelist()`) — delete the `Subdomain` (proxy stops
  routing), terminate the backing VM, set `status = Terminated`. Mirror
  `VirtualMachine.terminate()`'s cleanup-then-mark shape.

## Permissions (Contract C must match 04)

- Add the `Atlas User` role to the doctype with `if_owner: 1` for
  create/read/write/delete — same JSON block as `Virtual Machine`.
- Wire `permission_query_conditions["Site"] =
  "atlas.atlas.permissions.owner_only"` in
  [hooks.py](../../../atlas/hooks.py), and add `"Site"` to the `_OWNED_DOCTYPES`
  set in [permissions.py](../../../atlas/atlas/permissions.py). This is the
  list-scoping that makes a user see only their own Sites.
- System Manager (operator) keeps full CRUD.

## The routing read API

The SPA lists Sites with **standard Frappe endpoints** (`frappe.client.get_list`
/ `get`) — the `permission_query_conditions` does the scoping, so *no custom list
API is needed* (same as VMs in the SPA today; spec/11-user-ui.md). The one thing
the source doc calls a "routing read endpoint" is the **proxy/serving read** — a
whitelisted method that returns the routing facts for a subdomain (FQDN →
backing address / readiness), if the proxy or a health check needs to read it
back. Build the minimal version:

- A `@frappe.whitelist()` `routing_for(subdomain)` (or reuse `map_for_region`
  shape) returning the FQDN→address/status the consumer needs. Keep it read-only
  and owner-scoped (or operator-only if it's a control-plane read).
- **Don't over-build.** If the proxy already gets everything from `Subdomain`
  (it does — that's the existing map), this endpoint is only the *user-facing*
  "is my site ready / what's its URL" read, which the standard `get` already
  covers. Confirm whether a bespoke method is actually needed before writing one;
  the source doc lists it but the proxy map may already suffice.

## How it's proven (all unit, milliseconds)

- Label/denylist/uniqueness throws (the Contract-A validation).
- Immutability throws.
- State-machine guards (can't terminate twice, etc.).
- Permission scoping: a second user can't read another's Site (the `owner_only`
  condition) — a unit test with two users.
- The background orchestration's *pure* parts; the host parts (real provision +
  deploy + 200) are proven in the e2e ([05](./05-e2e-proof.md)), not here.

## Spec & docs (slice of [06](./06-spec-and-docs.md))
- New `spec/14-self-serve.md` — document `Site`: fields, state machine, methods.
- [spec/02-doctypes.md](../../../spec/02-doctypes.md) — add `Site` to the
  catalogue + bump the doctype count.
- [spec/11-user-ui.md](../../../spec/11-user-ui.md) — add `Site` to the SPA
  permission table + the screen that lists it.
