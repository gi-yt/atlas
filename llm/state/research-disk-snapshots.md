# firecracker-production — research: production-host setup & disk-snapshot implications

Research-phase notes for the `firecracker-production` idea. Source of truth for
the spec is `spec/`; this file is the working notebook the plan will draw from.
Read before planning. Two questions drove this pass:

1. What does Firecracker's **production host setup** ask for, and what does each
   item mean *for Atlas's disk snapshots* (the only kind we have — a `cp` of
   `rootfs.ext4`, see `spec/05` and `spec/07`)?
2. What are the **future blockers** for the deferred feature
   *Disk-Snapshot transfer to another host* (`spec/09-roadmap.md:139-141`)?

Everything below is cross-checked against the vendored Firecracker docs in
`references/firecracker/docs/` and the live web (sources at the bottom).

---

## 0. The one fact that reframes everything

Atlas snapshots are **disk-only** — a plain `cp` of the per-VM `rootfs.ext4`
taken while the VM is **Stopped** (`spec/05-virtual-machine-lifecycle.md:10-22`,
`spec/07-filesystem-layout.md:43-45`). We deliberately **never** call
Firecracker's `/snapshot/create` or `/snapshot/load`.

That single design choice makes the *hard* cross-host constraints **not apply to
us**. The constraints that kill cross-host *memory-state* snapshots are all about
the serialized CPU/KVM/device state in the `vmstate` + `mem_file`:

- **Not compatible across CPU architectures, nor even across CPU models of the
  same arch** unless the exposed CPU features are held invariant (needs a CPU
  template). [snapshot-support.md "Where can I resume my snapshots?",
  web: DeepWiki snapshot-system]
- **Host-kernel-version-sensitive**: save/restore across different host kernels
  is "unstable" — the saved KVM state can have different semantics on a different
  kernel. The vendored compat table allows only `5.10 → 6.1` on *identical*
  `.metal` instance types, "not recommended in production".
  [snapshot-support.md:658-674]
- arm64 GICv2↔GICv3 restore is impossible; x86 MSR quirks
  (`MSR_IA32_TSX_CTRL`) are lost without a CPU template.
  [snapshot-support.md:119-136]

**A `rootfs.ext4` file carries none of that.** It is just a filesystem image.
So the deferred "cross-server snapshots" item is *not* blocked by the scary
Firecracker hardware/kernel matrix — it is blocked by much more mundane,
fixable, Atlas-side things (§3). This is the headline finding and it should be
written into `spec/09` so nobody mistakenly files cross-host disk-snapshot
transfer under "needs identical hardware".

---

## 1. Firecracker production-host-setup checklist → relevance to Atlas + to disk snapshots

From `references/firecracker/docs/prod-host-setup.md`. Marked by whether it is
already done, a `hardening`-tree concern, a `firecracker-jailer`-tree concern,
or specifically **touches disk snapshots**.

| Prod-host recommendation | Atlas today | Owner | Disk-snapshot relevance |
|---|---|---|---|
| **Jailer** (non-priv uid/gid per VM, cgroup, chroot, namespaces) | not used — "root everywhere" (`spec/README:28`) | `firecracker-jailer` / roadmap unpriv-user | **High** — if each VM runs in its own chroot/uid, snapshot `cp` must read across that boundary; per-VM uid changes who owns the snapshot file. Affects where snapshots can live and who can copy them. |
| **Seccomp** (default filters) | inherited (default) — we don't pass `--no-seccomp` | none (already correct) | none |
| **Disable host swap** (data-remanence: guest RAM must not hit disk) | guest has a 512 MiB `/swapfile` *inside its rootfs*; host swap unmanaged | `hardening` | **Medium** — the rec is about *host* swap leaking *guest memory* to storage. For us, the guest swapfile lives *inside* `rootfs.ext4`, so **a disk snapshot captures the guest's swap contents** — a data-remanence concern when a snapshot is cloned to another tenant. Note for §3 security. |
| **Noisy-neighbour storage contention** (page-cache backlog, block-IO cgroup, rate-limit) | none; plain files, no rate limiter | `hardening` / future | **Medium** — a snapshot `cp` of a multi-GB rootfs is exactly the kind of burst that fills the page cache and stalls other guests' I/O. The `df` pre-flight in `snapshot-vm.sh` guards space, not I/O. Cross-host transfer makes this worse (sustained read of the whole image). |
| **8250 serial / stdout bounding** (unbounded host storage from guest) | unit logs to `journald` per VM (bounded) | mostly done; verify | Low |
| **Disable SMT** (Spectre/MDS cross-tenant leak) | not enforced | `hardening` | none directly |
| **Disable KSM** (page-dedup side channel) | not enforced (DO default likely off) | `hardening` | none directly |
| **kvm-pit CPU overhead / kvm min_timer_period** | not tuned | `hardening` (low value, see reject #4) | none |
| **Linux 6.1 boot-time regression** (`favordynmods` / `kvm.nx_huge_pages=never`) | not tuned; DO 24.04 is 6.8-ish | `hardening` (flag as 26.04/kernel-version watch) | Low |
| **Microcode / kernel patching cadence** | `unattended-upgrades` is a `hardening` item | `hardening` | none |
| **Overwatcher for deadlocked FC processes** (signal-handler deadlock) | `Restart=always` in unit handles crash, not hang | roadmap (health-check job) | Low |
| **Rowhammer / ECC memory** | provider hardware choice | out of scope | none |

**Takeaway:** the bulk of `prod-host-setup.md` is already split across the
`hardening` and `firecracker-jailer` ideas. The slice that is *uniquely* about
disk snapshots is small and concrete: **(a) the guest swapfile inside the rootfs
is captured by every snapshot** (remanence), and **(b) snapshot `cp` / transfer
is an unbounded storage-I/O burst** with no rate limit or quota. Neither is a
hardening-CIS item; both are snapshot-design items.

---

## 2. What "production" demands of the snapshot mechanism itself

Independently of the host hardening, two Firecracker docs put hard obligations on
anyone exposing snapshots as a product feature:

- **Disk-space DoS.** "If the service exposes the snapshot triggers to customers,
  integrators **must** enforce proper disk quotas." (`snapshot-support.md:502-509`)
  Atlas has only a `df` pre-flight floor (`spec/05` Snapshot step 1) and an
  explicit *no-quota* note. `spec/09` already lists **retention / GC / quotas**
  as deferred-before-load. **Production-blocking** the moment snapshots are
  operator- or tenant-triggered at volume.
- **Snapshots cross a trust boundary unprotected.** Firecracker's threat model
  *trusts* snapshot files; it does only a 64-bit CRC for accidental corruption,
  **not** authentication or encryption. "users need to secure snapshot files by
  implementing authentication and encryption schemes … when … moving them across
  the trust boundary … from a repository to a host over the network."
  (`snapshot-support.md:88-106`, `design.md` threat-containment.) This is
  *exactly* the cross-host-transfer feature. See §3.4.

---

## 3. Future blockers for "Disk-Snapshot transfer to another host"

`spec/09-roadmap.md:139-141`: *"A snapshot lives on its VM's server; clone and
restore target the same server. Moving a snapshot to another host … is additive
but unbuilt."* Here is what actually stands in the way, in priority order. None
of these is the Firecracker hardware/kernel matrix (§0).

### 3.1 Snapshots are children of the VM, not first-class host-independent objects — **the structural blocker**
- On disk they live *under* the VM dir:
  `/var/lib/atlas/virtual-machines/<vm-uuid>/snapshots/<snap-uuid>/rootfs.ext4`
  (`spec/07:13-20`). Terminating the VM `rm -rf`s the VM dir and the snapshots
  go with it (`spec/05` Terminate; `spec/02` `on_trash`).
- The DocType hard-binds snapshot→VM→server: `virtual_machine` is `set_only_once`
  and `server` is denormalized read-only from the VM (`spec/02-doctypes.md:504-505`).
- **Implication:** there is no notion of a snapshot that *outlives its VM* or
  *lives on a host other than the VM's*. Cross-host transfer needs a snapshot
  whose location is a mutable property, not a path glued to one VM's UUID dir.
  This is a **DocType + on-disk-layout change** (likely: a top-level
  `/var/lib/atlas/snapshots/<snap-uuid>/` store and a `server` field that can
  differ from the VM's), and the roadmap already half-anticipates it ("adds a
  state and a DocType"). Biggest single piece of work.

### 3.2 No kernel travels with the snapshot
- A disk snapshot is rootfs-only. Clone/restore get the **kernel from
  `source_image`** (`spec/02:507`, `spec/05` Clone: "the kernel still comes from
  the image, so the image must be synced").
- **Implication:** transferring a snapshot to host B is useless unless host B
  also has the matching `Virtual Machine Image` synced (kernel + pristine
  rootfs). Transfer must either (a) require/verify the image is present on the
  target (reuse the existing **Sync to Server** precondition pattern from
  `provision-vm.sh` step 0), or (b) ship the kernel alongside. (a) is the
  cheap, in-grain path. Cheap blocker, but a real precondition.

### 3.3 Full-copy transfer cost — no CoW, no dirty-block tracking — **the performance blocker**
- Atlas deliberately uses **plain `cp`, not overlayfs/CoW/reflinks**
  (`spec/07:51-63`). A snapshot is the *whole* rootfs (≥ pristine ~600 MB,
  grown to `disk_gigabytes`).
- ext4 itself has **no CoW**, confirmed by the wider ecosystem
  (web: FC discussion #3061; ext4 "doesn't support copy-on-write"). So a
  cross-host transfer is a full N-GB stream every time — exactly the 80 GB-disk
  pain the web sources call out for FC migration.
- **How others avoid it (for comparison, not necessarily to adopt now):**
  - **Ubicloud** runs storage on **SPDK** with a `bdev_ubi` **CoW** layer over a
    base image, copies with `spdk_dd`, and on btrfs uses `cp --reflink=auto`
    (metadata-only, sub-second). It has a **`track_written` / streaming sync**
    path on `vm_storage_volume` (the `caught_up?` / `stripes.fetched == source`
    RPC in `references/ubicloud/model/vm_storage_volume.rb`) — i.e. it streams
    blocks to the target and tracks catch-up, rather than a cold full `cp`.
  - **FireCrackManager / Drafter** (web) build custom TCP migration protocols and
    block-level live migration on top of FC snapshots.
- **Implication / decision for the plan:** the roadmap's **overlayfs-backed
  rootfs** item (`spec/09:120-121`) and **LVM/thin-pool** idea (`ideas.md` lvm:
  "faster disk snapshots and snapshot transfers to remote hosts") are the
  enablers. *Cross-host disk-snapshot transfer is cheap to build naively
  (rsync/`cp` over SSH between hosts) and expensive to build well (incremental /
  CoW / thin-pool send).* The naive version is a legitimate first slice and
  stays in-grain (one idempotent script, full-copy, fail-loud). Note this
  explicitly so the plan doesn't over-build.

### 3.4 Transfer crosses a trust boundary with no auth/encryption story
- Per §2: Firecracker trusts snapshot files and does only CRC. Moving bytes
  host→host over the network is *the* moment the docs say you must add
  authentication + encryption.
- Atlas's transport today is **SSH as root** (`spec/04`, `spec/07` SSH keys) —
  host↔host copy would ride either Atlas-mediated SSH (host A → Atlas → host B,
  doubling the bytes through the control node) or a **direct host-A→host-B**
  channel, which Atlas does **not** have today (no host-to-host trust; each host
  only trusts Atlas). Establishing A→B transfer trust is a real design fork
  (Atlas-relayed vs. direct-with-ephemeral-key) and is where the encryption
  obligation lands.
- **Ubicloud comparison:** every volume has its own **KEK** (AES-256-GCM key
  encryption key) in the control DB
  (`references/ubicloud/model/storage_key_encryption_key.rb`); moving/cloning a
  volume means re-wrapping/transferring the DEK, and `spdk_dd` re-encrypts on
  copy. That is the mature shape of "secure the file across the boundary".
  Atlas has **no at-rest encryption** of rootfs/snapshots today — a gap to name,
  not necessarily to close in the first transfer slice, but it *is* the
  production bar Firecracker sets.

### 3.5 Identity & networking on the target host
- **Identity is fine.** Clone already re-derives MAC/tap/host-keys/machine-id
  from a fresh UUID at provision (`spec/05` Clone). A transferred snapshot used
  as a clone source on host B inherits that safe path — **no duplicate-identity
  hazard** because we don't resume memory state (the whole reason we're disk-only).
- **Networking is the catch.** `ipv6_address` is allocated from
  **`Server.ipv6_virtual_machine_range`** — it is *per-server* (`spec/05`
  Provision step 1; `spec/09` "Address reuse on archive"). A VM/clone landing on
  host B **cannot keep its address**; it gets one from B's range. So
  *"transfer a snapshot and stand the VM back up with the same IP on another
  host"* is blocked until the **floating-IP** idea (`ideas.md` floating IP:
  "Carve out IPs from this pool … so we can move VMs between machines for Server
  Maintenance") exists. If transfer only ever feeds **clone-on-B** (new identity,
  new IP), this is a non-issue. If transfer is meant for **VM mobility / host
  maintenance** (same VM, new host, same IP), floating-IP is a hard predecessor.
  **This is the key scope fork for the idea** (see intake).

### 3.6 Concurrency / locking
- Two long copies of the same image-on-server are "a benign race" today; the
  roadmap flags a **Server lock doctype** before more operators
  (`spec/09:93-97`). A multi-minute cross-host transfer is a long mutating Task
  on *two* servers at once — it wants that lock (and the **stuck-task reaper**,
  `spec/09:85-91`) before production. Additive, but on the critical path for
  "production".

---

## 4. Summary: what blocks what

```
Disk-Snapshot transfer to another host
├─ NOT blocked by: CPU model / host-kernel / GIC version compat   ← §0 (we're disk-only)
├─ structural   : snapshots are VM-children, die with the VM       ← §3.1  (DocType + layout change)
├─ precondition : matching image (kernel) must exist on target     ← §3.2  (reuse Sync-to-Server pattern)
├─ performance  : full N-GB cp, no CoW/dirty-tracking              ← §3.3  (overlayfs / LVM-thin / lvm idea)
├─ security     : trust-boundary crossing, no auth/encryption      ← §3.4  (host↔host trust + at-rest crypto)
├─ networking   : per-server IPv6 → VM can't keep its IP on host B  ← §3.5  (floating-IP idea; N/A if clone-only)
└─ ops          : long mutating Task on 2 hosts → wants lock+reaper ← §3.6  (Server lock, stuck-task reaper)
```

**Cheapest viable first slice** (in Atlas grain): clone-only target (fresh
identity, new IP → dodges §3.5), require image already synced on the target
(§3.2), full-copy `cp` over the existing SSH path relayed through Atlas (§3.3
naive, §3.4 rides existing root-SSH trust), guarded by a `df` pre-flight on the
target. Defer overlayfs/LVM, at-rest encryption, floating-IP, and the
Server-lock — but **name each as a deferred blocker in `spec/09`** so the
building block stays honest.

## 5. Spec edits this research implies (regardless of build scope)
1. `spec/09-roadmap.md:139-141` — expand "Cross-server snapshots" into the
   blocker list above; explicitly state it is **not** gated by the FC
   hardware/kernel snapshot matrix (§0), to kill that misconception.
2. `spec/05` / `spec/09` — note the **guest-swapfile-in-rootfs remanence** point
   (§1) wherever snapshot security is discussed.
3. `spec/09` snapshot section — link **quotas/GC** (already there) to the
   Firecracker "must enforce quotas if customer-triggered" obligation (§2).

---

### Sources
- Vendored: `references/firecracker/docs/prod-host-setup.md`,
  `.../snapshotting/snapshot-support.md`, `.../snapshotting/snapshot-editor.md`,
  `.../snapshotting/network-for-clones.md`, `.../design.md`.
- Vendored: `references/ubicloud/model/storage_key_encryption_key.rb`,
  `.../model/vm_storage_volume.rb`.
- Web:
  - https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md
  - https://deepwiki.com/firecracker-microvm/firecracker/5-snapshot-system
  - https://github.com/firecracker-microvm/firecracker/discussions/3061 (ext4 no-CoW / shared rootfs)
  - https://github.com/firecracker-microvm/firecracker/discussions/3119 (live migration)
  - https://github.com/loopholelabs/drafter (custom block-level FC live migration)
  - https://deepwiki.com/dtouzeau/firecrackmanager/3.2-snapshots-export-and-vm-migration (custom TCP migration protocol)
  - https://www.ubicloud.com/blog/building-block-storage-for-cloud-with-spdk-non-replicated
  - https://www.ubicloud.com/blog/ubicloud-block-storage-encryption (per-volume KEK)
  - https://spdk.io/news/2023/03/28/ublk/
