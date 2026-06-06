# The reverse proxy

A TLS-terminating reverse proxy that fronts many Frappe sites. Each site is a
subdomain of a regional wildcard (`*.<region>.frappe.dev`); each subdomain maps
to exactly one site VM, dialed over public IPv6 on port 80 (plaintext). The map
changes constantly and must update **without reloading nginx**. Atlas is the
source of truth and reconciles each proxy's live map over SSH.

The full architecture, rationale, and the locked design interviews live in
[`llm/proxy-design.md`](../llm/proxy-design.md). This chapter is the durable
spec: what exists, what each piece does, and what is still pending.

## The shape

- **The proxy is an ordinary Atlas Virtual Machine** — operator-owned, marked
  `is_proxy` with a `region` ([02-doctypes.md](./02-doctypes.md#virtual-machine)).
  No infrastructure-VM tier: it is invisible to the user SPA by ownership, and
  inherits the standard Firecracker jail + per-VM netns + cgroup caps as its
  sandbox. It runs the self-built nginx + Lua stack ([`proxy/`](../proxy)) and
  carries an attached `public_ipv4` (the inbound-v4 primitive,
  [06-networking.md](./06-networking.md#ipv4-ingress-reserved-ip)) so it can
  terminate v4 **and** v6 on `:443`.
- **2–3 proxy VMs per region** behind the one regional wildcard (DNS
  round-robin over their v4 + v6), for resiliency and zero-downtime rolling
  updates. Each proxy is independent and holds the **whole** regional map.
- **The live map** is a `lua_shared_dict` inside each proxy guest (the in-process
  source of truth), dumped to a sorted, pretty-printed `map.json` read only at
  start. A map change is an atomic dict write — **zero reload**.

## Desired state: the Subdomain DocType

One [`Subdomain`](./02-doctypes.md#subdomain) row per routing entry: `subdomain`
(unique) → `virtual_machine` (the site VM) → `address` (the VM's `/128`,
denormalized) → `region` + `active`. Standalone and linked (the Reserved IP
idiom), **not** a child grid on a proxy — every proxy holds the whole regional
map, so ownership is per region.

The desired map for a region is `map_for_region(region)` = `{subdomain: address}`
for every active subdomain in the region. Every proxy VM in the region serves
that same full map.

## Control plane: Atlas → guest

`atlas/atlas/proxy.py` is the controller side. It is **not** a host Task (which
stages a script onto a Server and runs it there): it runs on the controller and
SSHes **into the guest** — the second SSH target type,
`connection_for_guest(vm)` ([04-tasks.md](./04-tasks.md#how-it-runs)),
reaching the VM's `/128` as `root` with the same Atlas key already in the guest's
`authorized_keys`. The guest's admin API is a **unix socket only**
(`/run/atlas-proxy/admin.sock`); SSH-to-the-guest is the only way to reach it,
and the socket's file permissions are the gate.

- **`canonical_json(map)`** — the one canonical serialization: sorted keys,
  2-space indent, one key per line, trailing newline. **Byte-identical** to the
  guest's `persist.lua` output, so the reconcile "in sync?" check is a plain
  string compare, not a semantic diff.
- **`reconcile_proxy(vm)` / `reconcile_region(region)`** — for each proxy VM,
  read its live `/map` over the admin socket, byte-compare against the canonical
  desired map, and bulk-declarative `POST /sync` the full map (streamed to the
  guest `curl --data-binary @-` over SSH stdin) on drift. Idempotent,
  self-healing, **rebuild-safe** (a fresh proxy's empty dict refills on the next
  reconcile). A proxy that can't be reached is recorded as a failed Task and
  **skipped** — one wedged guest never wedges the loop; the others still serve.
- **`push_cert(vm, fullchain, privkey)`** — drop the regional wildcard
  cert/key into the guest's per-region cert dir (private key via `tee` from
  stdin, never in an argv) and reload nginx. Cert pushes are rare, so a reload is
  fine here (unlike map changes). The cert is pushed, never baked into the image,
  so one proxy image serves any region and a renewal is a re-push, not a rebuild.

Each guest operation is recorded as a `Task` row (`script` = `proxy-sync` /
`proxy-push-cert`, with the proxy VM) for the operator's audit trail, the same
row shape as every host Task.

## Build & roll = VM lifecycle

The proxy is built the Atlas-native way (no custom rootfs, no host service):
provision an ordinary VM from stock Ubuntu, SSH in and run
[`proxy/build.sh`](../proxy/build.sh) (compiles nginx 1.30.2 + OpenResty
`luajit2` + `lua-nginx-module` + NDK + resty-core/lrucache + lua-cjson +
headers-more from pinned sources, installs the stack + the three Lua modules +
the guest unit), then **snapshot** it — that snapshot is the reusable "proxy
image". Install / update / roll / rollback are the existing VM lifecycle verbs
(provision / rebuild / snapshot / clone), rolled one proxy at a time so DNS keeps
the others serving — a zero-downtime rolling update.

The nginx image's behavior is the **image-level release gate**: the
docker-compose harness under [`proxy/test/`](../proxy/test) exercises the same
`conf/` + `lua/` the in-guest build installs (routing, remap-no-reload, branded
404, bulk `/sync`, canonical-JSON byte-match, restart-reload-from-`map.json`,
HTTP→HTTPS, HTTP/2, socket.io upgrade) — 10/10 green. Nothing is installed on the
dev host.

## Pending (host-bound facts — Atlas e2e)

These prove what only a real droplet can, and are **not yet built** (the design's
Phase D, [`llm/proxy-design.md`](../llm/proxy-design.md) §9.2):

- **inbound-v4 reachability** of the proxy guest's `:443` (the attach primitive's
  e2e is proven for SSH/`:22`; `:443` is the proxy's first real listener).
- **inbound-:80 to a site from the proxy's vantage** — the public-v6 south-side
  release gate that has never been tested
  ([06-networking.md](./06-networking.md), `proxy-design.md` §2.1). A site's `:80`
  is reachable by anyone on the v6 internet; a future per-VM firewall must scope
  it to the proxies and must not drop the proxy hop.
- **guest-SSH map sync end-to-end** — Atlas SSHes a real proxy guest, syncs the
  map, reads it back.
- **rolling rebuild** — rebuild one proxy from a new snapshot, re-push cert,
  re-sync map, confirm it serves while the others stay up.

TLS **grade** (A+) is the one image-gate row not automated (needs a real cert /
`testssl.sh`), so it is a manual/D check.
