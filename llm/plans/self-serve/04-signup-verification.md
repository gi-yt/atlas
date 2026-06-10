# 04 — Signup + email verification (T3)

> **STATUS: BUILT + unit-green (2026-06-09).** The signup→verify→fulfil *backend*
> and the guest `/signup` + `/verify` www surface are done and unit-green (15 Site
> Request tests + 7 signup API tests; the 24 Site + 10 permission regressions stay
> green). What's built and how it diverged from this plan is in
> [DRIFT.md](./DRIFT.md) §"Phase 04"; the as-built picture is the
> [wireframe](./wireframes/04-signup-verification.md). **Resolved open questions:**
> signup surface = a www page + guest API (not a Web Form); account model = a
> reusable Website User, one Site per signup, more later via the SPA (both confirmed
> with the operator). **Still deferred (named, not half-built):** the in-SPA Sites
> screen + the shown-once admin-password *reveal* (backend reveal is
> `site.get_password("admin_password")` gated on `Running`) — the remaining plan-04
> frontend slice. The original Phase-03 findings below are kept for context.
>
> **FINDINGS FROM PHASE 03 (read before building).** Phase 03 resolved the
> shared **admin-handoff** open question this plan inherited:
> - The Administrator password is generated per-site by the in-guest deploy and
>   stored **encrypted on `Site.admin_password`** (a `Password` field, built in
>   03). Phase 04's fulfilment/SPA does **not** generate or re-derive it — it
>   **reveals** it. After the Site reaches `Running`, read it with
>   `site.get_password("admin_password")` (server-side) and show it **once** in the
>   SPA (the "dropped into the bench with admin" handoff). Treat it as shown-once:
>   do not echo it in list views or logs.
> - There is **no magic-login link** in 03; the handoff is the admin password +
>   the live URL `https://<fqdn>`. If a magic login is wanted, it is new 04 work,
>   not something 03 left half-built.
> - **Timing the reveal.** The password is written *before* the HTTP readiness
>   wait, so it exists on the row from `Deploying` onward — but only surface it to
>   the user once `status == Running` (Contract B), so they aren't handed creds for
>   a site that may still go `Failed`. The SPA already polls `status` (02); gate the
>   reveal on `Running`.
> - **Contract A validators to share** (unchanged from 02): reuse
>   `Site._validate_label` / `_validate_reserved` / the FQDN uniqueness check so a
>   `Site Request` can't reserve an illegal/taken name — factor them into a shared
>   helper as this plan already says.

**Goal.** The public on-ramp: a signup form (email + subdomain choice), an email
verification link, and — **only after verification** — the `Site` insert that
kicks off everything else. The verified user is stamped `owner` (Contract C).

**Gates on:** 02 (inserts the `Site` doctype it owns). Forms + verification are
buildable in parallel and unit-provable; the end-to-end (verify → live site) is
provable once 02 lands and the proxy routes.

## The ordering is the whole point (Contract C)

```
1. signup form           → collect email + subdomain (+ name)
2. create unverified row  → a `Site Request` (NOT a Site, NOT a VM yet)
3. send verification email → frappe.sendmail with a tokened link
4. user clicks link       → verify the token
5. ONLY NOW: create User (Atlas User role) + insert the `Site` row, owner = user
6. Site.after_insert (02) → provision VM → deploy → 200 → Running
```

**No droplet/site work happens before step 5.** Verification gates the
(billable) provision, so a typo'd or hostile email never triggers compute. This
is the inversion the source doc demands: email verification *precedes* the VM
insert.

## `Site Request` doctype

The pre-verification holding row (the source doc names it explicitly). It is
**not** a `Site` — it carries no VM, no routing, just the intent + the
verification state.

### Fields (sketch)
| Field | Type | Notes |
| ----- | ---- | ----- |
| `email` | Data | the unverified address; the future `owner` |
| `subdomain` | Data | single DNS label — **validate with the same Contract-A rules as `Site`** (02), so a request can't reserve an illegal/taken name |
| `region` | Link/Data | which fleet |
| `token` | Data | `frappe.generate_hash(length=32)`; the verification secret |
| `status` | Select | `Pending → Verified → Fulfilled` / `Expired` |
| `verified_at` | Datetime | for expiry (e.g. token valid 24h) |
| `site` | Link (Site) | set on fulfilment (step 5), the produced Site |

- **Reuse `Site`'s validators.** Factor the label/denylist/uniqueness checks
  (02) into a shared helper so `Site Request` and `Site` enforce the *same*
  Contract-A rules. Otherwise a request could reserve a name `Site` would reject,
  or two requests could both pass and collide at fulfilment.
- **Pre-check availability at request time too** (best-effort): reject a
  subdomain already taken by a live `Site`, so the user learns early — but the
  authoritative uniqueness is still `Site`'s key at step 5 (handle the race with
  a clean "taken" message there).

## The flow pieces

### A. Signup form (public, guest-accessible)
- A public page collecting email + subdomain + region. Either a Frappe **Web
  Form** or a small page under [atlas/www/](../../../atlas/www/) (the dashboard
  SPA lives there; a `signup` route fits). Guest-accessible (no login).
- On submit → a `@frappe.whitelist(allow_guest=True)` method that validates the
  subdomain (shared validator), creates the `Site Request` (status `Pending`,
  with a fresh token), and sends the email. `ignore_permissions=True` for the
  insert (guest can't normally write).
- **Throttle.** Guest-writable + sends email + (eventually) provisions = abuse
  surface. Rate-limit per email/IP (Frappe has `frappe.rate_limiter`); cap
  outstanding unverified requests per email.

### B. Verification email
- `frappe.sendmail(recipients=[email], subject=…, template=…, args={token,
  fqdn})`, link → `/verify?token=<token>`. Use an `Email Template` /
  `atlas/www/templates/` HTML.
- Requires the site's outbound email to be configured — note as an operator
  prerequisite (like the TLS controller-host deps).

### C. Verification route → fulfilment
- A route handler (e.g. `atlas/www/verify.py`) reading `?token=`. Look up the
  `Site Request` by token; reject missing/expired (past `verified_at + TTL`).
- On valid token, **fulfil** (step 5), all server-side:
  1. Create (or fetch) the `User` for `email`, `user_type = Website User`,
     role `Atlas User`, `send_welcome_email = 0` (we send our own).
  2. Insert the `Site` row **as that user** (so Frappe stamps `owner = user` —
     Contract C). Use `frappe.set_user` / explicit owner so the SPA scoping
     (`owner_only`, 02) then shows the Site to exactly this user.
  3. Mark the request `Fulfilled`, link `site`.
  4. Log the user in (or hand them a login link) and redirect to the dashboard
     SPA — `/dashboard/...`, *not* `/dashboard/frontend/...` (the SPA route trap
     from the UI work). The SPA then shows the Site going `Pending → … → Running`
     (02's status), and the admin handoff (03's open question) surfaces here.

## Permissions (must match 02)

- `Site Request`: System Manager full; `Atlas User` `if_owner` read (so a user
  can see their own request status), create via the guest method only. Wire
  `permission_query_conditions["Site Request"]` to `owner_only` if users list
  their requests; the *owner* of a request is the verified user — set it at
  fulfilment to match.
- The `Site` ownership is set in step 5; 02's permission block must already admit
  `Atlas User` `if_owner` (it does — that's 02's contract).

## Open questions to resolve while building

- **Account vs site.** Is signup creating an *account* (reusable, can make more
  sites later) or strictly one-site-per-signup? The idea doc reads as
  account-light ("fill a form → you're in the bench"). Recommend: create a real
  `User` (so they can return to the SPA), one Site per request initially, more
  Sites later through the SPA. Confirm before building the User-exists branch.
- **Admin handoff UX** — shared with 03/02: how the Administrator password / magic
  login reaches the owner after `Running`.
- **Email deliverability** — operator prerequisite; document it, don't solve it
  here.

## How it's proven

- **Unit (milliseconds):** subdomain validation (shared with 02), token
  generation, expiry math, the fulfilment ordering (a `Site` is created *only*
  after verification — assert no `Site`/VM exists pre-verify), owner stamping (the
  verified user owns the Site), the guest-method permission/throttle.
- **e2e (05):** the real verify → live-site path is part of the end-to-end proof;
  the *email send* itself can be asserted via Frappe's outbox in a unit/integration
  test without real SMTP.

## Spec & docs (slice of [06](./06-spec-and-docs.md))
- New `spec/14-self-serve.md` — document the signup→verify→fulfil flow, the
  `Site Request` doctype, and Contract C (ordering + ownership).
- [spec/02-doctypes.md](../../../spec/02-doctypes.md) — add `Site Request`; bump
  the count.
- [spec/11-user-ui.md](../../../spec/11-user-ui.md) — the signup/verify entry is
  the *one* SPA surface reachable by a guest; note it explicitly (the rest of the
  SPA is login-gated).
