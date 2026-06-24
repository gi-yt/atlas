# User UI — the owner-scoped end-user boundary

> The operator UI is [10-desk-ui.md](./10-desk-ui.md). This document covers the
> *second audience* Atlas serves — end **users** — and the multi-tenant
> permission boundary that scopes them.

> **Status (SPA retired).** Atlas once shipped a frappe-ui single-page app at
> `/dashboard` as the end-user surface. That SPA has been **removed**: Central
> ([16-central.md](./16-central.md)) is now the customer-facing front door for
> the whole platform, so a second in-app UI is redundant. What this chapter still
> describes — and what is still **live** — is the **owner-scoped permission
> model** ([permissions.py](../atlas/atlas/permissions.py)) and the guest
> **signup on-ramp** ([14-self-serve.md](./14-self-serve.md)). Those stay until
> signup itself moves behind Central; the layout/component detail of the deleted
> SPA is preserved only as a historical note at the end.

Atlas has two audiences:

- **Operators** use **Desk** (`/app/atlas`). They own the fleet: providers,
  servers, image sync, ad-hoc tasks, capacity. See
  [10-desk-ui.md](./10-desk-ui.md).
- **Users** are accounts created by self-serve signup. They hold the
  `Atlas User` role and own **only their own** Virtual Machines, Snapshots, SSH
  Keys, and Sites, plus read-only access to shared Images. They never see
  Server, Task, or the Settings Singles. They have no in-Atlas UI of their own —
  Central fronts them; the boundary below is enforced at the API layer so it
  holds regardless of which client calls.

## The permission split

The `Atlas User` boundary is Atlas's multi-tenant boundary. It is enforced at the
**permission layer**, not just hidden in a UI — a user calling the API by hand is
refused.

| DocType                  | Operator (System Manager) | User (`Atlas User`)                         |
| ------------------------ | ------------------------- | ------------------------------------------- |
| Virtual Machine          | all rows, all perms       | **own rows** (`if_owner`): read/write/create/delete |
| Virtual Machine Snapshot | all rows, all perms       | **own rows** (`if_owner`): read/create/delete |
| SSH Key                  | all rows, all perms       | **own rows** (`if_owner`): read/write/create/delete |
| Site                     | all rows, all perms       | **own rows** (`if_owner`): read/write/create/delete |
| Virtual Machine Image    | all rows, all perms       | **read, all rows** (shared base images)     |
| Task                     | all rows (read; no delete)| **read, only Tasks of an owned VM**         |
| Server                   | all rows, all perms       | **no access**                               |
| Provider Size / Image    | all rows                  | **no access**                               |
| Settings (all Singles)   | all                       | **no access**                               |

Mechanics (all in `atlas/atlas/permissions.py`, wired in `hooks.py`):

- **Ownership = Frappe's built-in `owner`.** No owner field is added; Frappe
  stamps `owner` on insert. A user owns the VMs/Snapshots they create.
- **`if_owner: 1`** permission rows on Virtual Machine, Virtual Machine
  Snapshot, SSH Key, and Site for the `Atlas User` role restrict the user to
  their own rows.
- **`permission_query_conditions`** scope list views / `get_list`:
  - Virtual Machine, Virtual Machine Snapshot, SSH Key, Site, Site Request →
    `owner = <user>`.
  - Task → only Tasks whose `virtual_machine` is owned by the user.
  - System Manager → unrestricted (empty condition).
- **`has_permission` on Task** guards single-document reads: a user may read a
  Task only if they own its linked VM. Task has no `if_owner` (Tasks are
  stamped with the system user, not the requesting user), so this hook + the
  query condition together produce "own VM's tasks only".

The `Atlas User` role ships as a `Role` fixture with `desk_access: 0` — these
users have no Desk footprint. Website access is independent of desk access, so an
`Atlas User` can reach the standard `frappe.client.*` endpoints (the contract
the signup flow and any external front door use) without Desk access.

**The one guest-reachable surface.** The public **signup** on-ramp
([14-self-serve.md](./14-self-serve.md)) is the only guest-accessible surface:
the server-rendered `/signup` page + the guest API
`atlas.atlas.api.signup.request_site`, the `/verify` route the emailed link lands
on, and the `/site-status` provisioning view. These are deliberately
guest-accessible (a visitor has no account yet). Verification *creates* the
account — a Website User with the `Atlas User` role, with the produced Site
stamped `owner = user` so the scoping above applies — and logs them in.

## What the end-user boundary does not own

- **It defines no new server-side logic.** Every lifecycle action posts to the
  *existing* whitelisted controller methods on the Virtual Machine
  (`provision`, `start`, `stop`, `restart`, `pause`, `resume`, `snapshot`,
  `rebuild`, `resize`, `terminate`). Clients are clients, not a second
  controller.
- **It defines no new API endpoints.** Standard Frappe endpoints only:
  `frappe.client.get_list` / `get`, document insert/delete, and the lifecycle
  methods via `run_doc_method`. No bespoke REST router.
- **It exposes no *server* placement choice.** A user never picks a server. On
  create they choose the **image** (from the shared, Active Virtual Machine
  Images), and the Virtual Machine controller fills `server` from placement
  (`before_insert`); the operator controls which servers are Active and which
  images exist. "Room" is a vCPU budget: a host's physical vCPU total times
  `Atlas Settings.overprovision_factor` (default 1), minus the vCPUs of its live
  VMs. A host whose size has no known vCPU total counts as unlimited. When the
  user omits an image, `default_image` applies the operator's configured default.

## Testing

The boundary is pinned by unit tests that exercise it as the user, not via a UI.
`test_permissions.py` pins both halves of the contract — an `Atlas User` sees
only their own Virtual Machines / Snapshots / SSH Keys / Sites and only the Tasks
of a VM they own, and is denied another user's rows and all of
Server/global-Task — so a future PR that adds a DocType or relaxes a perms block
can't silently widen access. `test_ssh_key.py` pins key validation + fingerprint.
Both run in milliseconds.

## Deferred (named, not half-built)

- **Team / sharing model** — the boundary is strictly per-`owner`. A `Team`
  doctype (Gameplan/CRM style) is a follow-up if multiple users must share a VM.
  (Central's own team model is expected to supersede this; see
  [16-central.md](./16-central.md).)
- **SSH key rotation on an existing VM** — a key is immutable on the rootfs
  (`ssh_public_key` is `set_only_once`), so adding/removing keys touches the
  *account*, not a running machine. Re-keying a VM means terminate + recreate.

---

## Historical note — the retired `/dashboard` SPA

For one iteration, the end-user surface was a Vue 3 + frappe-ui single-page app
under `atlas/frontend/`, built to `atlas/public/frontend/` and served at
`/dashboard` via a `www/dashboard.html` host page and a `website_route_rules`
entry. It exposed five screens (Machines list + detail, Images, Snapshots, SSH
Keys) and a New Machine dialog, composing standard frappe-ui components on the
library's semantic tokens; it added no server-side logic and no API endpoints,
posting only to the standard controller methods and `frappe.client.*` routes
catalogued above. It was removed when Central became the customer-facing front
door. The permission model it relied on (above) outlived it because self-serve
signup users depend on the same owner-scoping. The SPA's source, built assets,
`www/dashboard.{html,py}` host page, the `/dashboard` route rule, and its coupled
tests (`test_website_route.py`, `test_action_map.py`) were all deleted.
