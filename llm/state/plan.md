# Plan — firecracker-production (jailer)

Run every per-VM Firecracker process under the **jailer** binary: de-privileged
uid/gid, chrooted, cgroup-isolated. Reverses "root everywhere" for the
Firecracker *child process only* (Atlas still SSHes in as root to run Tasks).

Scope, Success, and the four reject-on-sight rules are pinned in
`scratch/active.md` § firecracker-production. This plan resolves the open design
risks into concrete edits and a phase order. **Two standing constraints from the
operator:**

1. **Defer e2e as much as possible.** The live reachability/jail-uid e2e needs a
   bench flip (turn-taking, operator-only). So: write all *static* + *unit* +
   *DocType-validation* coverage inline (runnable with no droplet), and batch the
   one live e2e behind a single `atlas-tree firecracker-production` flip at the
   very end. Do not serialize the bench mid-build.
2. **Don't block on the queue.** This tree depends on **nothing** from the other
   in-flight trees (images / ipv4-egress / hardening / vm-features). It only
   touches `bootstrap-server.sh`, the systemd unit, the VM lifecycle scripts, and
   the spec. The one cross-tree *courtesy* note: `hardening` also edits
   `bootstrap-server.sh`; our edits are additive (append a binary install + a
   user/group create) and do not conflict line-wise, but the two trees must not
   be flipped live on the same droplet simultaneously — that's an operator
   sequencing note, not a code dependency.

---

## Key facts that shape the design (verified against the tree)

- **The jailer binary already arrives in bootstrap.** `bootstrap-server.sh:72-79`
  downloads `firecracker-${VER}-${ARCH}.tgz` and `install`s
  `release-${VER}-${ARCH}/firecracker-${VER}-${ARCH}`. That same tarball contains
  `release-${VER}-${ARCH}/jailer-${VER}-${ARCH}` (confirmed:
  `references/firecracker/tools/release.sh:125` ships `jailer` in `ARTIFACTS`,
  musl static build). **So adding the jailer is one extra `install` line in the
  block we already run — no new download.**
- **Boot path is `--config-file`, not the API socket** (systemd unit `ExecStart`,
  `firecracker-vm@.service:10-12`). Jailer forwards everything after `--` to
  Firecracker (`jailer.md:101-107`), so `--config-file` survives the move; we
  pass it after `--` with paths made jail-relative.
- **The jailer copies `--exec-file` into the jail and chowns the jail root +
  `/dev/kvm` + `/dev/net/tun` to the uid/gid** (`jailer.md:117-144`). It creates
  its own device nodes. So our per-VM **backing files (rootfs RW, kernel RO,
  config) must be present inside the jail with the right ownership** before
  Firecracker is exec'd (`jailer.md:266-270` "Observations").
- **The API socket moves into the jail.** Default jail root is
  `<chroot-base>/<exec_file_name>/<id>/root`; Firecracker creates the socket at
  `<jail_root>/<api-sock>` (`jailer.md:258-262`). Today `pause-vm.sh` /
  `resume-vm.sh` curl `/var/lib/atlas/run/<uuid>.sock` — that path must change to
  the in-jail socket.
- **Scripts already call `sudo` explicitly everywhere** (the `spec/09:77-79`
  hedge). Adding a non-root *Firecracker* actor does not require rewriting the
  scripts' own privilege model — they keep running as root over SSH and only the
  jailed child drops privilege.
- **DO 24.04 is cgroup v2**; jailer defaults to v1 (`jailer.md:39-42`) → must pass
  `--cgroup-version 2`.
- **`scripts/lib/prepare-rootfs.sh`** is the shared rootfs/identity lib used by
  provision + rebuild + clone. The rootfs *contents* are unchanged by jailer;
  only *where the rootfs file lives on host* changes (it must be inside the jail).

---

## Design decisions (the open risks, resolved)

These were flagged in `active.md` Notes (a)–(f). Resolved here so there are **no
open questions** going into implementation (per WORKFLOW.md Plan rule).

### (a) Chroot location — **retarget the jail base into `/var/lib/atlas`, one jail dir per VM, keyed by UUID**
Decision: `--chroot-base-dir /var/lib/atlas/jails`. Jailer then builds
`/var/lib/atlas/jails/firecracker/<uuid>/root/` as the jail root. We do **not**
adopt the default `/srv/jailer` — keeping everything under `/var/lib/atlas`
preserves the `spec/07` invariant ("Everything Atlas puts on a server lives under
`/var/lib/atlas/`. Nothing else.") and the 0700 posture.

The per-VM rootfs/kernel/config move **inside** the jail root:
- `rootfs.ext4` → `<jail_root>/rootfs.ext4` (RW, owned by the jail uid)
- kernel → hard-link the image kernel into `<jail_root>/vmlinux` (RO). Hard-link
  (not copy) so we don't duplicate the kernel per VM; same device, jailer reads
  it post-chroot. If hard-link fails (cross-device — images and jails are both
  under `/var/lib/atlas`, same fs, so it won't), fall back to copy.
- `firecracker.json` → `<jail_root>/firecracker.json`, with **jail-relative**
  paths inside it (`/rootfs.ext4`, `/vmlinux`) since they're resolved by the
  jailed process after chroot.

`provision-vm.sh` writes these into the jail; `terminate-vm.sh` `rm -rf`s the jail
dir. **Consequence for `spec/07`:** the per-VM on-host layout changes from
`virtual-machines/<uuid>/{rootfs.ext4,firecracker.json,...}` to a jail tree. We
keep `virtual-machines/<uuid>/` as the canonical VM dir for `network.env` + logs,
and the jail root becomes a sibling (or nested) path. **Chosen layout** (minimizes
spec churn and keeps logs/network.env where reboot-recovery expects them):

```
/var/lib/atlas/virtual-machines/<uuid>/
├── network.env                      # unchanged — vm-network-up.sh reads this
├── log/firecracker.log              # unchanged
└── jail/                            # NEW — the jailer chroot base for this VM
    └── firecracker/<uuid>/root/
        ├── firecracker               # copied by jailer from --exec-file
        ├── rootfs.ext4               # per-VM disk (was ../rootfs.ext4)
        ├── vmlinux                   # hard-link to the image kernel
        ├── firecracker.json          # jail-relative paths
        └── run/firecracker.socket    # API socket (was /var/lib/atlas/run/<uuid>.sock)
```

i.e. `--chroot-base-dir /var/lib/atlas/virtual-machines/<uuid>/jail`. Snapshots
stay under `virtual-machines/<uuid>/snapshots/` (unchanged — they're host-side
copies, never inside the jail). `snapshot-vm.sh` / `rebuild-vm.sh` read the rootfs
from its new in-jail path.

### (b) API socket path — **derive the in-jail socket path in one shared helper**
The socket is now `…/jail/firecracker/<uuid>/root/run/firecracker.socket`. Both
`pause-vm.sh` and `resume-vm.sh` compute it the same way. To keep "one place,"
add the path derivation to a tiny shared snippet (or inline an identical
`socket="$(atlas_jail_socket "$VIRTUAL_MACHINE_NAME")"`). We pass
`--api-sock run/firecracker.socket` to Firecracker (jail-relative); on host that
resolves under the jail root. The unit's `ExecStartPre=/bin/rm -f …sock` updates
to the new path.

### (e) uid model — **per-VM uid/gid, derived from the UUID** *(operator: solve it)*
FC recommends a *unique* uid/gid per VM so a breakout of one jail can't touch
another VM's files (`jailer.md:99-108`). We do this, and we do it the Atlas way:
**derive the uid deterministically from the VM UUID**, exactly as MAC/tap are
derived (`networking.py::derive_mac`/`derive_tap`). No allocator, no DB field, no
per-VM `useradd` row to track — the uid is a pure function of the UUID, stable
across reboots and re-provisions.

- **Derivation:** `atlas_uid = UID_BASE + (int(uuid.hex[:6], 16) % UID_SPAN)`
  with `UID_BASE = 200000`, `UID_SPAN = 60000` → uids in `[200000, 260000)`,
  well clear of system (`<1000`) and normal-login (`1000–60000`) ranges, and
  inside the typical `subuid` band. gid = uid (a matching per-VM group). Added as
  `networking.py::derive_uid(virtual_machine_name)` next to its siblings, unit-
  tested for determinism + range + collision-spread (same place `derive_mac` is
  tested in `test_networking.py`).
- **Collision handling:** a 24-bit space mod 60000 can collide across many VMs on
  one host. The jail is still isolated by uid *value*; two VMs sharing a uid lose
  only the inter-VM-isolation property between *those two*, not correctness. We
  make collisions loud-but-rare: `provision-vm.sh` checks whether the derived uid
  is already owned by a *different live VM's* jail and, if so, **fails the Task
  with an actionable message** (operator re-rolls by terminating one — astronomic
  in practice; documented, not silently merged). This honors "fail loud at the
  boundary" (Taste 17) over a silent shared-uid fallback.
- **No persistent passwd entry per VM.** We do **not** `useradd` 200k users.
  The jailer takes **numeric** `--uid/--gid` and chowns by number; Linux does not
  require a `/etc/passwd` row for a uid to own files or run a process. So
  `provision-vm.sh` passes the derived numerics straight to the unit (via the
  `jail.env` sidecar, decision (b2) below) and `chown`s the jail tree to the
  numeric uid:gid. Nothing to create in bootstrap, nothing to clean up on
  terminate beyond the `rm -rf` that already removes the jail. This is *simpler*
  than the shared-user design it replaces — no `groupadd/useradd` in bootstrap.

### (d) Resource caps — **per-VM cgroup-v2 caps + rlimits, derived from the VM's own fields** *(operator: solve it)*
`prod-host-setup.md` calls for bounding each Firecracker process's CPU, memory,
disk-IO and fd count. We pass these through the jailer so they are applied
**before guest code runs** (jailer writes them into the per-`<id>` cgroup it
creates, `jailer.md:43-51, 127-135`). Caps are **derived from the VM's existing
`vcpus` / `memory_megabytes` / `disk_gigabytes` fields** — no new DocType fields,
no operator knobs (keeps it legible, reject #5).

- `--cgroup-version 2` (DO 24.04 is unified cgroup v2).
- **Memory:** `--cgroup memory.max=<bytes>` where bytes = `memory_megabytes` +
  a fixed headroom for Firecracker's own VMM/IO threads (the guest RAM is not the
  whole RSS). Decision: `memory.max = (memory_megabytes + 256) MiB` and
  `memory.swap.max = 0` (no swapping guest RAM to host disk — this *also*
  satisfies the prod-host "disable swap / data-remanence" rec at the per-VM
  level, `prod-host-setup.md:242-253`). The +256 MiB headroom is a named constant
  in the script with a one-line rationale; if too tight, FC OOMs visibly (loud).
- **CPU:** `--cgroup cpu.max="<quota> 100000"` = `vcpus * 100000 100000` →
  `vcpus` full cores' worth of CPU bandwidth per 100 ms period. Not cpuset
  pinning (that needs host topology knowledge and NUMA layout we don't model
  yet — pinning stays the one deferred CPU knob, documented). Bandwidth-cap is
  the portable, legible control.
- **fd / file size:** `--resource-limit no-file=1024` and
  `--resource-limit fsize=<disk bytes + headroom>` so a runaway can't fill the
  host past its own disk. (setrlimit, `jailer.md:79-92`.)
- All cap *values* are computed in `provision-vm.sh` from the env vars it already
  receives (`VCPUS`, `MEMORY_MB`, `DISK_GB`) and written to `jail.env`; the unit
  references them. **Unit-testable without a bench:** assert the rendered
  cgroup-arg string for a given (vcpus, mem, disk) triple.

### (c) Networking / netns — **per-VM network namespace, with a veth uplink back to host** *(operator: solve it)*
We move each VM's tap **into its own network namespace** and join the jailer to
it with `--netns`, so a jail breakout cannot see or touch the host's interfaces,
the other VMs' taps, or the uplink directly. This is the FC-recommended model
(`jailer.md:77-78`, `network-for-clones.md`). It is the biggest of the three
changes because our routing currently assumes a host-netns tap; here is the full
design that keeps **IPv6 reachability identical** while gaining the isolation:

- **Per-VM netns:** `atlas-<uuid12>` (derive from UUID like the tap; netns names
  have no IFNAMSIZ limit so we can use more chars for clarity). Created in
  `vm-network-up.sh` (`ip netns add`), deleted in `vm-network-down.sh`
  (`ip netns del`) — idempotent both ways.
- **Tap lives inside the netns.** `ip netns exec <ns> ip tuntap add <tap> mode
  tap vnet_hdr`; the jailer joins via `--netns /var/run/netns/<ns>`, and FC opens
  the tap by `host_dev_name` *inside* that namespace. The tap name no longer has
  to be globally unique (it's namespaced) but we keep the derived name for
  legibility.
- **veth pair bridges netns ↔ host.** `<veth-host>` in the host netns,
  `<veth-ns>` inside the VM netns (named pair, derived). This is the seam that
  carries the VM's IPv6 out to the uplink:
  - Inside the netns: the tap gets `fe80::1/64` (the guest's gateway, unchanged
    contract — the guest still `default via fe80::1`), the VM's `/128` is routed
    to the tap, and a default route points out `<veth-ns>`.
  - On the host: the `/128` is routed into `<veth-host>` (instead of directly to
    the tap, as today), and **proxy-NDP on the uplink still answers for the VM's
    address** (unchanged — the uplink-facing half is identical, only the
    last hop moves from `tap` to `veth-host → netns → tap`).
  - **nft forward rules** move to match `iifname/oifname <veth-host>` instead of
    the tap (the tap is no longer in the host netns to match on). Same two-rule
    shape, same `inet atlas` table.
- **Why this preserves reachability:** the guest still sees `fe80::1` as gateway
  and its own `/128`; the uplink still proxy-NDPs the VM address. The only change
  is an extra link-local hop (veth) between uplink and tap, fully inside the host.
  IPv6 forwarding (already =1, load-bearing — see [[atlas-ipv6-forwarding-required]])
  carries it across the veth.
- **Honest cost:** this is more moving parts than the host-netns tap, and it is
  the part most likely to surface a bench-only bug (NDP/forwarding across the
  veth seam). It is genuinely *more secure* (network isolation of the jail), which
  is why the operator pulled it into scope. The full IPv6-reachability assertion
  stays in the deferred live e2e — this is exactly a "host-bound fact" that only
  the bench can confirm (see [[atlas-e2e-vs-unit-boundary]]); the namespace/veth
  *wiring* (command strings, derivations, teardown symmetry) is unit/`bash -n`
  checkable now.

### (b2) Passing per-VM values to the static unit — **a `jail.env` sidecar**
The unit template must stay static (no UUID/uid/caps baked in). `provision-vm.sh`
writes `/var/lib/atlas/virtual-machines/<uuid>/jail.env` with
`ATLAS_FC_UID`, `ATLAS_FC_GID`, `ATLAS_CGROUP_ARGS` (the assembled `--cgroup …`
flags), `ATLAS_NETNS`, `ATLAS_RESOURCE_ARGS`. The unit does
`EnvironmentFile=/var/lib/atlas/virtual-machines/%i/jail.env` and references
`${ATLAS_FC_UID}` etc. in `ExecStart`. Mirrors the existing `network.env`
sidecar pattern exactly. (systemd splits `EnvironmentFile` values on whitespace
into argv correctly for the multi-flag `ATLAS_CGROUP_ARGS` case — verify in e2e;
fallback is a generated drop-in if word-splitting misbehaves.)

### (f) Restart + jail cleanup — **idempotent jail create + terminate rm covers it**
`Restart=always` (unit) re-execs jailer on crash. Jailer no-ops if the `<id>` jail
dir already exists (`jailer.md:117-119, 199-203`), and Firecracker re-creates the
socket (`ExecStartPre rm -f`). The stale-jail accumulation risk is bounded: one
jail dir per VM UUID, removed by `terminate-vm.sh`. We add an `ExecStartPre` that
clears the jail's old socket (as today) — no extra reaper needed. Verify in the
live e2e that a crash-restart cycle leaves exactly one jail dir.

---

## In scope now (the operator pulled these three out of "deferred")

- **Per-VM uid/gid**, derived from the UUID (decision (e)). Full inter-jail
  user isolation, no allocator, no passwd rows.
- **Per-VM resource caps** — cgroup-v2 `memory.max`/`memory.swap.max`/`cpu.max`
  + rlimit `no-file`/`fsize`, all derived from the VM's own
  `vcpus`/`memory_megabytes`/`disk_gigabytes` (decision (d)). No new fields.
- **Per-VM network namespace** + veth uplink (decision (c)). Network isolation
  of the jail; IPv6 reachability preserved.

## What we are STILL NOT doing (each gets one honest line in spec/09, reject #3)

- **CPU *pinning*** (`cpuset.cpus`/`cpuset.mems`, NUMA). We do CPU *bandwidth*
  capping (`cpu.max`), not affinity — pinning needs host-topology modeling we
  don't have. The one CPU knob deferred.
- No custom seccomp filter work (jailer + FC defaults only — already the
  recommended posture).
- No `--new-pid-ns` (extra PID-namespace isolation; adds a PID-file indirection
  for `KillMode`. `KillMode=mixed` + the jail cgroup already contains the
  process tree; defer the pid-ns, document it).
- No change to the SSH *transport* privilege (Atlas still connects as root to run
  Tasks; only the Firecracker child is de-privileged).
- No cross-host snapshot transfer (separate research, captured to `spec/09`).
- No block/net device rate limiters (the token-bucket `PATCH` API; a tuning knob
  on top of the cgroup IO control we get for free).

---

## Phases

Small, independently verifiable. Static/unit checks after every phase; the live
e2e is the **last** phase and the only one needing a bench flip.

### Phase 0 — derivations + unit tests (pure Python, no bench, no host)
`atlas/atlas/networking.py` — add, next to `derive_mac`/`derive_tap`:
- `derive_uid(virtual_machine_name) -> int` (decision (e); `UID_BASE`/`UID_SPAN`
  module constants).
- `derive_netns(virtual_machine_name) -> str` and `derive_veth_pair(...)` →
  `(host_veth, ns_veth)` names (decision (c); IFNAMSIZ-safe like `derive_tap`).
- `cgroup_args(vcpus, memory_megabytes, disk_gigabytes) -> list[str]` and
  `resource_limit_args(disk_gigabytes) -> list[str]` (decision (d); the
  `MEMORY_HEADROOM_MIB` constant lives here with its rationale comment).
Doing the derivations first means everything downstream (scripts, unit, e2e)
consumes stable, *unit-tested* values. **Verify (no bench):** extend
`atlas/tests/test_networking.py` — determinism, range (`200000 ≤ uid < 260000`),
collision-spread across 10k UUIDs, veth/netns name length ≤ IFNAMSIZ, and exact
cgroup/rlimit arg strings for representative (vcpus, mem, disk) triples.

### Phase 1 — Bootstrap: install the jailer binary
`scripts/bootstrap-server.sh`:
- In the existing Firecracker-install block (`:64-79`), after installing the
  `firecracker` binary, `install -m 0755` the sibling
  `release-${VER}-${ARCH}/jailer-${VER}-${ARCH}` → `/usr/local/bin/jailer`.
  Gate on `/usr/local/bin/jailer --version` matching, mirroring the firecracker
  gate, so re-run is a no-op. (Same tarball — no new download.)
- **No user/group creation** — per-VM uids are numeric and need no passwd entry
  (decision (e)). Bootstrap is unchanged beyond the binary install + JSON field.
- Ensure `virtual-machines/` stays mode 0700 so nested per-VM jails inherit a
  private root (already so at `:97`).
- `bootstrap.json` (`:113-120`): add `jailer_version` alongside
  `firecracker_version`. Update `server.py:_absorb_bootstrap_output` to read it
  (mirror the existing field; record on the `Server` row).
**Verify:** `bash -n bootstrap-server.sh`; unit test that the parser accepts the
new JSON field. No bench.

### Phase 2 — systemd unit: firecracker → jailer (with per-VM uid/caps/netns)
`scripts/systemd/firecracker-vm@.service`:
- Add `EnvironmentFile=/var/lib/atlas/virtual-machines/%i/jail.env` (decision
  (b2)) — carries `ATLAS_FC_UID`, `ATLAS_FC_GID`, `ATLAS_NETNS`,
  `ATLAS_CGROUP_ARGS`, `ATLAS_RESOURCE_ARGS`.
- `ExecStart` becomes:
  ```
  ExecStart=/usr/local/bin/jailer \
      --id %i \
      --exec-file /usr/local/bin/firecracker \
      --uid ${ATLAS_FC_UID} --gid ${ATLAS_FC_GID} \
      --cgroup-version 2 \
      --netns /var/run/netns/${ATLAS_NETNS} \
      $ATLAS_CGROUP_ARGS \
      $ATLAS_RESOURCE_ARGS \
      --chroot-base-dir /var/lib/atlas/virtual-machines/%i/jail \
      -- \
      --api-sock run/firecracker.socket \
      --config-file firecracker.json
  ```
  (`$ATLAS_CGROUP_ARGS` = e.g. `--cgroup memory.max=… --cgroup cpu.max=…`;
  `$ATLAS_RESOURCE_ARGS` = `--resource-limit no-file=1024 --resource-limit
  fsize=…`. Unquoted so systemd word-splits them into argv.)
- `ExecStartPre=/bin/rm -f` → the in-jail socket path.
- `ExecStartPre=vm-network-up.sh %i` runs **before** jailer and now creates the
  netns + veth + in-netns tap (Phase 5) so the namespace exists when jailer joins
  it via `--netns`.
- `KillMode=process` → **`KillMode=mixed`** so the jailed FC (in the unit's
  cgroup) dies with the unit, not just the jailer parent.
**Verify:** `bash`-lint the unit; unit test asserting it references `jailer`,
`--netns`, `${ATLAS_FC_UID}`, and the jail-relative `--config-file`. No bench.

### Phase 3 — provision-vm.sh: build the jail, derive caps/uid, write jail.env
`scripts/provision-vm.sh` + `scripts/lib/prepare-rootfs.sh`:
- `jail_root="/var/lib/atlas/virtual-machines/${VM}/jail/firecracker/${VM}/root"`.
- Lay the per-VM rootfs at `${jail_root}/rootfs.ext4` (`atlas_copy_rootfs` dest
  changes; `atlas_inject_identity` unchanged — it mounts the file wherever it is).
- Hard-link the image kernel to `${jail_root}/vmlinux` (`ln -f`; copy fallback
  cross-device — won't happen, same fs).
- Write `firecracker.json` into `${jail_root}` with **jail-relative** paths
  (`kernel_image_path: "vmlinux"`, `drives[0].path_on_host: "rootfs.ext4"`).
- **Per-VM uid:** the controller passes the `derive_uid` value in as `ATLAS_FC_UID`
  (env var to the script). Collision check: if a *different* live VM's jail on
  this host already owns this uid, fail loud (decision (e)).
  `chown -R ${ATLAS_FC_UID}:${ATLAS_FC_GID} "${jail_root}"` after laying files
  (rootfs RW, kernel RO).
- **Write `jail.env`** with uid/gid, netns name, and the cgroup/resource arg
  strings (the controller derives these from the VM's fields via Phase-0 helpers
  and passes them in, OR the script derives the cgroup strings from `VCPUS`/
  `MEMORY_MB`/`DISK_GB` it already receives — *decision: derive in Python and
  pass in*, so the single source of the cap formula is the unit-tested
  `cgroup_args()`; the script just writes what it's handed).
- `systemctl enable --now` unchanged.
- Idempotency: `atlas_copy_rootfs` no-ops if dest exists; `ln -f`, `chown`,
  `jail.env` install are all idempotent.
**Verify:** `bash -n`; unit test rendering the config (jail-relative paths) and
the `jail.env` contents for a given VM. No bench.

### Phase 4 — pause / resume / terminate / snapshot / rebuild socket+path fixes
- `pause-vm.sh`, `resume-vm.sh`: socket path → in-jail
  `…/jail/firecracker/<uuid>/root/run/firecracker.socket`. **And** the `curl` must
  reach a socket that now lives inside the VM's netns? No — the **API socket is a
  unix socket on the host filesystem** (under the jail root), not a network
  socket, so netns does not affect it; `curl --unix-socket <path>` still works
  from the host. (Confirm in e2e.) Add the shared path derivation (decision (b)).
- `terminate-vm.sh`: `rm -rf "$vm_directory"` already removes the nested jail;
  drop the now-dead `rm -f /var/lib/atlas/run/<uuid>.sock`. Networking teardown
  (`vm-network-down.sh`) now also deletes the netns + veth (Phase 5).
- `snapshot-vm.sh` / `rebuild-vm.sh`: read/write the rootfs at its new in-jail
  path. Snapshot still copies *out* to `snapshots/<snap>/rootfs.ext4` (host-side,
  unchanged location). Rebuild lays the new rootfs back into the jail and re-runs
  identity injection.
**Verify:** `bash -n` each; unit tests for the socket-path derivation. No bench.

### Phase 5 — networking: per-VM netns + veth uplink (decision (c))
`scripts/vm-network-up.sh` (rewrite the device-creation half; the uplink/NDP half
stays in spirit) + `scripts/vm-network-down.sh` (symmetric teardown). Reads the
new `ATLAS_NETNS` / veth names from `network.env` (extended by `provision-vm.sh`).
Sequence in `vm-network-up.sh` (all idempotent — `del`-before-`add` like the
current tap block at `:36-40`):
1. `ip netns add <ns>` (guard: `ip netns list | grep` or `add ... 2>/dev/null`).
2. `ip link add <host_veth> type veth peer name <ns_veth>`; move `<ns_veth>` into
   `<ns>` (`ip link set <ns_veth> netns <ns>`).
3. **Inside `<ns>`:** create the tap (`ip netns exec <ns> ip tuntap add <tap>
   mode tap vnet_hdr`), `fe80::1/64` on the tap (guest gateway, unchanged
   contract), route the VM `/128` to the tap, default route out `<ns_veth>`,
   link-local addressing on `<ns_veth>`.
4. **On the host:** address `<host_veth>`, route the VM `/128` into `<host_veth>`
   (was: directly to the tap), keep the **proxy-NDP entry on the uplink**
   unchanged (`ip -6 neigh replace proxy <ipv6> dev <uplink>`).
5. nft forward rules: match `<host_veth>` instead of `<tap>` (same two-rule
   shape, same table). `vm-network-down.sh` deletes rules by VM-IPv6 lookup (as
   today), then `ip netns del <ns>` (which takes the in-ns tap + ns_veth with it)
   and `ip link del <host_veth>`.
- IPv6 forwarding stays =1 (load-bearing — [[atlas-ipv6-forwarding-required]]);
  it now also forwards across the veth seam.
- **Ordering:** the unit's `ExecStartPre=vm-network-up.sh` must complete before
  jailer's `--netns` join, which it does (ExecStartPre is sequential before
  ExecStart). The netns must exist at jailer exec — it does.
**Verify (no bench):** `bash -n` both; unit test asserting the rendered command
sequence references the derived netns/veth/tap names and that down is the
symmetric inverse. The *actual IPv6 reachability across the veth* is a host-bound
fact → deferred to the live e2e (see [[atlas-e2e-vs-unit-boundary]]). **This phase
carries the highest bench-only risk** (NDP/forwarding across the seam) — flag for
focused attention in the live pass.

### Phase 6 — spec rewrite (reject #3)
- `spec/README.md:28` non-goal "No jailer, no unprivileged user … Root
  everywhere" → rewrite: FC now runs **jailed, chrooted, with a per-VM uid/gid,
  per-VM cgroup caps, and a per-VM network namespace**. Honestly keep deferred:
  root SSH *transport*, CPU pinning, `--new-pid-ns`, custom seccomp.
- `spec/03-bootstrapping.md`: add the jailer-binary install to the bootstrap step
  list + idempotency section; add `jailer_version` to bootstrap.json. (No user
  creation — per-VM uids are numeric.)
- `spec/05-virtual-machine-lifecycle.md`: update the systemd-unit section
  (`:296-308`) — `ExecStart` is jailer with uid/netns/cgroup args; pause/resume
  socket path is in-jail (unix socket, netns-independent); `KillMode=mixed`.
- `spec/06-networking.md`: document the per-VM netns + veth model (the new last
  hop) — the guest contract (`fe80::1` gateway, its `/128`) is unchanged; only the
  host-side path gains the veth seam.
- `spec/07-filesystem-layout.md`: add the per-VM `jail/` tree + `jail.env`; rootfs
  + kernel + config + socket live inside the jail, owned by the per-VM uid.
- `spec/09-roadmap.md`: move the **unprivileged-user/jailer** deferred item
  (`:99-103`) to *done* (jailed + per-VM uid + caps + netns), with the still-
  deferred tail: drop-root-SSH-transport, CPU pinning, `--new-pid-ns`, custom
  seccomp, rate limiters.
**Verify:** prose only; no bench.

### Phase 7 — tests (split: inline now / live deferred)
**Inline now (no bench) — the bulk of coverage lives here (see [[atlas-e2e-vs-unit-boundary]]):**
- `derive_uid` / `derive_netns` / `derive_veth_pair` / `cgroup_args` /
  `resource_limit_args` — determinism, range, collision-spread, IFNAMSIZ, exact
  arg strings (Phase 0; in `test_networking.py`).
- `bash -n` over every edited script. If the repo has no static-shell test, add
  `atlas/tests/test_scripts_static.py` that `bash -n`s every `scripts/**/*.sh`.
- Rendered `firecracker.json` uses jail-relative paths.
- Rendered `jail.env` carries the right uid/gid/netns/cgroup/rlimit strings for a
  given VM.
- bootstrap.json parse accepts `jailer_version` → Server row.
- pause/resume in-jail socket-path derivation.
- `vm-network-up`/`down` reference the derived netns/veth/tap names and are
  symmetric inverses.
- Catalog/permission tests: assert no regression.
**Deferred to one bench flip (the only turn-taking checkpoint)** — host-bound
facts only the droplet can prove. Extend `virtual_machine_provisioning.py` +
`virtual_machine_lifecycle.py`:
- provision → boot → **IPv6 reachable across the veth seam** → SSH-key accepted
  (the netns/veth correctness — highest-risk, Phase 5);
- the running FC process: **uid == `derive_uid(vm)` (not 0)**, chrooted
  (`/proc/<pid>/root` → jail root), and **in the VM's netns**
  (`/proc/<pid>/ns/net` ≠ host's);
- the per-VM **cgroup caps applied**: `memory.max` / `cpu.max` in the unit's
  cgroup match the derived values;
- pause → resume over the in-jail unix socket;
- crash-restart leaves exactly one jail dir + one netns (cleanup).
Write these now (they're code), but **do not run** — they need the live droplet.

---

## Verification strategy (honors "defer e2e")

| Check | When | Needs bench? |
|---|---|---|
| Unit: derive_uid/netns/veth + cgroup/rlimit args | Phase 0 | No |
| `bash -n` all edited scripts | after each phase | No |
| `py_compile` Python edits | after each phase | No |
| Unit: jail-relative config + `jail.env` render | Phase 3 | No |
| Unit: bootstrap.json `jailer_version` parse | Phase 1 | No |
| Unit: in-jail socket-path derivation | Phase 4 | No |
| Unit: vm-network up/down name refs + symmetry | Phase 5 | No |
| Catalog/permission no-regression | Phase 7 | No |
| **Live: provision+boot+IPv6-across-veth+key** | **end, one flip** | **Yes** |
| **Live: FC uid==derive_uid, chrooted, in netns** | **end, same flip** | **Yes** |
| **Live: cgroup memory.max/cpu.max applied** | **end, same flip** | **Yes** |
| **Live: pause/resume over in-jail socket** | **end, same flip** | **Yes** |
| **Live: crash-restart leaves one jail + netns** | **end, same flip** | **Yes** |

All non-bench checks run inline as I build. When the tree is otherwise READY
(scripts edited, spec rewritten, static+unit green, e2e *written*), I stop and
say: *"ready to verify — `atlas-tree firecracker-production` when free."* One flip,
one batched live pass. No mid-build bench serialization.

## Risk register
- **Per-VM netns + veth seam is the highest bench-only risk** (Phase 5). NDP /
  IPv6 forwarding across the extra veth hop can fail in ways unit tests can't
  see. Mitigation: the guest-facing contract (`fe80::1`, the `/128`) and the
  uplink-facing proxy-NDP are *unchanged* — only the middle hop is new, so the
  blast radius is contained to the veth wiring. Focused attention in the live
  pass; if the seam misbehaves, the fallback is host-netns taps (decision (c)
  reverts cleanly — netns is additive to the routing, not entangled with uid/caps).
- **Per-VM uid collision** (24-bit mod 60000). Mitigation: provision fails loud on
  a live-VM uid clash (decision (e)); astronomically rare. If it ever bites in
  practice, widen `UID_SPAN` or hash more UUID bytes — a one-line change to the
  unit-tested `derive_uid`.
- **cgroup `memory.max` headroom too tight** → FC OOM-killed at boot. Mitigation:
  +256 MiB headroom constant, tunable in one place; OOM is loud (unit fails to
  start, Task fails). Validate the headroom empirically in the live pass.
- **systemd word-splitting of `$ATLAS_CGROUP_ARGS`** into argv. Mitigation:
  unquoted `EnvironmentFile` values split on whitespace (decision (b2)); if it
  misbehaves, generate a per-VM drop-in instead. Verify in the live pass.
- **Jailer not in the tarball for the pinned version.** Verified `release.sh`
  ships it for musl (our `x86_64` release is musl). Install-line gate fails loud
  if a future version drops it.
- **Hard-link kernel across fs boundary.** Images + jails both under
  `/var/lib/atlas` (same fs) → `ln` works; copy fallback documented.
- **Migration of existing VMs.** Already-running VMs keep their old non-jailed
  unit until re-provisioned (their `firecracker.json`/paths predate the jail
  layout). Not retro-jailed; terminate+reprovision to adopt the jail, or accept
  they run un-jailed until then. **Note this edge in the spec.**
- **`hardening` tree also edits bootstrap-server.sh.** Additive, non-conflicting
  lines; operator sequences the live flips. No code dependency.

## Open questions
None. (Decisions (a)–(f), (b2) resolve every fork; WORKFLOW.md forbids planning
with open questions.) The per-VM netns is the one decision with a real cost/benefit
tension; resolved toward the FC-recommended isolation per the operator's explicit
"solve netns," with a clean revert path noted in the risk register.
