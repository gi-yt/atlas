# Phase 04 wireframe — Signup + email verification (Contract C)

Drawn from the actual implementation: the `Site Request` doctype
(`atlas/atlas/doctype/site_request/`), the guest API
(`atlas/atlas/api/signup.py`), the `/signup` + `/verify` www pages
(`atlas/www/signup.{html,py}`, `atlas/www/verify.{html,py}`), the verification
email (`atlas/templates/emails/site_verification.html`), and the shared
Contract-A validators (`atlas/atlas/subdomain_label.py`).

The whole point is the **ordering**: the holding row first, the billable `Site`
only after the email is proven.

## The flow (Contract C — verification precedes the insert)

```
   GUEST                          ATLAS                                MAILBOX
   ─────                          ─────                                ───────
  /signup ──── email+subdomain ──▶ request_site (guest API)
                                    │  validate_email
                                    │  validate_label  (shared, Contract A) ── bad → throw
                                    │  validate_reserved                     ── reserved → throw
                                    │  is_taken?  (live Site)                ── taken → throw
                                    │  pending cap (≤3/email) + rate (5/hr)  ── over → throw
                                    │  insert Site Request{ Pending, token } (ignore_permissions, owner=Guest)
                                    │  sendmail(site_verification) ───────────────────────▶ ✉  "Verify…"
                                    ▼                                                         │  link:
              "check your inbox" ◀──┘                                                         │  /verify?token=…
                                                                                              │
  click link ─────────────────────▶ /verify?token=…  (get_context)                          ◀┘
                                    │  request = by token                ── missing → verify.html "invalid/used"
                                    │  request.verify():
                                    │    status==Fulfilled? → return existing Site  (idempotent)
                                    │    expired?           → status=Expired; throw → verify.html "expired"
                                    │    _ensure_user(email): Website User + Atlas User role (desk_access=0)
                                    │    _insert_site_as(user): set_user(user) → Site.insert()  ⇒ owner=user
                                    │    verified_at=now; site=<fqdn>; status=Fulfilled
                                    │    db_set owner=user   (constant field — db_set, not .save)
                                    │  login_manager.login_as(email)   (session cookie)
                                    ▼
                          redirect 302 → /dashboard   (NOT /dashboard/frontend/…)
                                    │
                                    ▼
                     SPA: the owner's Site is provisioning (Site.after_insert → auto_provision, plan 02)
                          Pending ─▶ Provisioning ─▶ Deploying ─▶ Running
                          on Running: reveal admin_password once  (SPA work; backend = get_password)
```

**No Site / User / VM exists before the verify click** — that is the asserted
invariant (`test_no_site_exists_before_verification`,
`test_creates_pending_request_only`).

## `Site Request` state machine

```
  insert (before_insert: shared label gate, region resolve, token, Pending)
     │
     ▼
  Pending ───── verify() (valid token, not expired) ─────▶ Fulfilled  (links Site, re-owned to user)
     │                                                         ▲
     │                                                         └── verify() again → returns same Site (idempotent)
     └──── verify() after creation+24h ──▶ Expired   (throws "expired"; no Site created)
```

## /signup page (guest)

```
┌─ Create your Frappe site ───────────────────────────┐
│  Pick a subdomain and we'll spin up a Frappe site.   │
│  We'll email you a link to verify before anything is │
│  created.                                            │
│                                                      │
│  Email      [ you@example.com            ]           │
│  Subdomain  [ acme           ] .blr1.frappe.dev      │  ← suffix from active Root Domain
│             lowercase, digits, hyphens; one label    │
│                                                      │
│            [   Create my site   ]                    │
│  ┌────────────────────────────────────────────────┐ │
│  │ (on success) Check your inbox for a verification │ │  ← form replaced inline
│  │ link to finish creating your site.               │ │
│  │ (on error)   <server throw message>              │ │
│  └────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────── ┘
   posts → atlas.atlas.api.signup.request_site  (frappe.call)
```

## /verify page (guest, FAILURE path only)

```
A valid token redirects to /dashboard before the body renders, so this is only
ever the dead-end:

┌─ Verification failed ───────────────────────────────┐
│  This verification link is invalid or has already    │
│  been used.            (or: "…has expired")          │
│            [   Sign up again   ] → /signup           │
└──────────────────────────────────────────────────── ┘
```

## Permissions

```
Site Request
  System Manager : full CRUD
  Atlas User     : if_owner READ only (no create/write — guest API creates it,
                   fulfilment re-owns it to the verified user)
  permission_query_conditions["Site Request"] = owner_only  (∈ _OWNED_DOCTYPES)
```

> Operator prerequisite: outbound email must be configured (an Email Account) for
> the verification mail to actually deliver — with none set up `sendmail` queues a
> no-op. Same class of prerequisite as the TLS controller-host deps.
