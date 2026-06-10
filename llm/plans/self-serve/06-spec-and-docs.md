# 06 — Spec & docs surfaces (cross-cutting)

> **FINDINGS AFTER PHASE 05 (what's left for 06).** Most rows below already
> landed alongside their plan; the audit after building the e2e found only one
> real gap. State as of Phase 05:
> - **`spec/14-self-serve.md`** — written; contracts A/B/C moved in; the
>   "Host-bound facts — the `self_serve_site` e2e" section is filled (this phase).
> - **`spec/README.md`** — chapter 14 is in "Read this in order" (line 115); the
>   Testing mapping now lists `self_serve_site.py` and the Entry-points section
>   describes it as a standalone (not-in-`run_all_smoke`) superset use case.
> - **`spec/02-doctypes.md`** — count is 22; `Site` + `Site Request` catalogued
>   and sectioned.
> - **`spec/11-user-ui.md`** — `Site` / `Site Request` in the `if_owner` perm
>   table; the `/signup` + `/verify` guest on-ramp documented.
> - **`spec/08-images.md`** — updated by 01 (note the build-in-guest + snapshot
>   drift, D01-1, not the planned `SyncImageInputs` flag).
> - **CLOSED (2026-06-09):** the `spec/09-roadmap.md` gap is fixed — and it was
>   wider than stated: the Changes log was stranded at `v0.6` with proxy (ch.12)
>   and TLS (ch.13) *also* unentered, so all three were backfilled (`v0.7` proxy,
>   `v0.8` TLS, `v0.9` self-serve; operator-approved). `self-serve-parallelism.md`
>   was trimmed to a pointer + host-bound-remaining. See DRIFT **D06-1/2/3** and
>   the [Phase-06 wireframe](./wireframes/06-spec-and-docs.md).
> - **Definition-of-done caveat:** by the rule below, 05 is "done" only after its
>   e2e slice is *proven*, which here means an actual host run (operator turn). The
>   code + spec/doc slices are done; the host proof is pending (DRIFT D05 open).
>   **All 06 doc surfaces are now landed + audited; the only thing standing
>   between this layer and fully-done is that one billable host run.**


**Goal.** The spec is the source of truth ([spec/README.md](../../../spec/README.md)):
nothing in 01–05 is "done" until its slice of the spec is updated. This plan is
the **checklist of every spec/doc surface** the self-serve layer touches, so
nothing is missed. It is not a phase you run at the end — each plan updates its
slice as it lands; this file is the master list.

## The new chapter: `spec/14-self-serve.md`

`spec/13-tls.md` is taken, so the new chapter is **`spec/14-self-serve.md`**.
Match the house style of [12-proxy.md](../../../spec/12-proxy.md) /
[13-tls.md](../../../spec/13-tls.md):

```
# Self-serve site creation
[2–4 sentence intro: the problem, the solution shape]

## The shape
[the flow diagram from 00-overview, the four-role routing string]

## The one routing string            ← Contract A (freeze it here)
## The readiness signal              ← Contract B
## Ownership / verification ordering ← Contract C

## Desired state: the Site DocType        ← from 02 (fields table, state machine)
## Signup: the Site Request DocType        ← from 04 (fields, the ordering)
## In-guest deploy: deploy-site.py         ← from 03 (driven over guest-SSH)
## The golden bench image                  ← from 01 (cross-link 08-images.md)

## First-run / operator setup
[email/SMTP config, default_user_image = golden, region wildcard prereq]

## Host-bound facts — the `self_serve_site` e2e   ← from 05

## Deferred
[teams, quotas, multi-label/custom-domain, account model — the 00 scope guard]
```

The three contracts (A/B/C) move **out of** [00-overview.md](./00-overview.md)
and [self-serve-parallelism.md](../../self-serve-parallelism.md) and **into** this
chapter as their durable home, once 02/03/04 freeze them in code.

## Existing spec files to update (when each plan lands)

| File | Change | Driven by |
| ---- | ------ | --------- |
| [spec/README.md](../../../spec/README.md) | Add `14. [Self-serve site creation](./14-self-serve.md)` to **Read this in order** (line ~114). Add the e2e module to the **Testing** mapping. Decide whether signup is an operator use case (it is **not** — it's user-facing; see below) and place it accordingly. | 02–05 |
| [spec/02-doctypes.md](../../../spec/02-doctypes.md) | Bump the doctype **count** in the opening line. Add `Site` and `Site Request` to the top numbered catalogue. Add a full `## Site` and `## Site Request` section each (Fields / Form layout / List view / Buttons / Controller methods / Permissions) in the house style. | 02, 04 |
| [spec/08-images.md](../../../spec/08-images.md) | Document the **bench-preinstall variant** of the sync pipeline + the new `SyncImageInputs` flag + the version pins. | 01 |
| [spec/11-user-ui.md](../../../spec/11-user-ui.md) | Add `Site` (and `Site Request`) to the SPA **permission table** (`if_owner` rows). Document the Sites screen + the **one guest-reachable surface** (signup/verify). | 02, 04 |
| [spec/09-roadmap.md](../../../spec/09-roadmap.md) | **Move** self-serve from a deferred/next-steps entry into a **Changes** entry (shipped). Remove it from the deferred sections. | on landing |
| [spec/06-networking.md](../../../spec/06-networking.md) | Cross-link from 14 for the `:80` south-hop / readiness probe path (likely no new text, just the link). | 03 |

## User-facing vs operator-facing (how the spec frames this)

Self-serve is **user-facing, not operator-facing** — like user VM-creation in the
SPA. The spec already has the pattern (spec README, spec/11):

- The **"Operator use cases"** table is operator-only — do **not** add "create a
  site" there (a user does it, not the operator). The closest *operator* touch is
  none beyond first-run config; if a terminate-from-Desk or similar operator
  action emerges, *that* row goes in the table.
- User-facing behavior is specced in [11-user-ui.md](../../../spec/11-user-ui.md)
  (the SPA chapter) + the new 14, and tested via a user-flow e2e module — mirror
  how user VM-creation is handled. The 05 e2e module is that test.
- This avoids the second exploration agent's "Option A/B" ambiguity: it's
  Option A (user-facing), unless/until an operator approval step is added (it is
  **not**, per the 00 scope guard — verification is the only gate).

## The companion design docs to retire/trim

- [self-serve-parallelism.md](../../self-serve-parallelism.md) — once these plans
  exist and the work lands, trim it to remaining-only (the same way
  [proxy-design.md](../../proxy-design.md) was trimmed to rationale + not-yet-built
  after the proxy shipped). Its "Tracks to build" + "Contracts to freeze" content
  is now superseded by this `plans/self-serve/` directory and (on landing) by
  `spec/14-self-serve.md`.
- [proxy-design.md](../../proxy-design.md) / [ideas.md](../../ideas.md) — no
  change; they already point here as companions.

## Definition of done (per plan, not just at the end)

A plan in 01–05 is **done** only when:
1. its code is built and proven (unit and/or its e2e slice), **and**
2. its row(s) in the table above are updated, **and**
3. any contract it froze (A/B/C) is written into `spec/14-self-serve.md`, not
   left only in the planning docs.

Keep diffs tight per the repo's formatting rule
([CLAUDE.md](../../../CLAUDE.md)): touch only the lines you change; don't let
`ruff format` reflow whole spec/code files.
