# 01 — Golden bench image (T-IMG)

> **BUILT (2026-06-09).** Implemented as **build-in-guest + snapshot**, not the
> "bake during sync" decision below — see [DRIFT.md](./DRIFT.md) D01-1..D01-4 for
> the divergence and rationale (operator-confirmed). What shipped:
> `bench/build.sh` + `bench/bench.toml` (committed tree beside `proxy/`),
> `atlas/atlas/bench_image.py` (`build_bench`, mirrors `proxy.build_proxy`),
> `atlas/tests/e2e/use_cases/bench_image.py` (provision → build → snapshot →
> assert `bench` runs), `atlas/atlas/test_bench_image.py` (5 unit tests, green),
> and the `spec/08-images.md` "golden bench image" section. The golden image is a
> **`Virtual Machine Snapshot`**; site VMs clone from it. The bake itself is a
> host fact not yet run on a droplet (DRIFT "Open / to verify"). The original
> sync-flag plan below is kept for context.

**Goal.** A bench-preinstalled image variant so a freshly-provisioned VM already
has `bench-cli`, its `uv` venv, Frappe cloned, and MariaDB/Redis ready — leaving
`deploy-site.py` (03) only the per-site work (`bench new-site` + start), not a
multi-minute install. This is the longest single wall-clock item in the layer
(apt + clone + pip inside a rootfs) and it **blocks nobody** — start it first and
let it bake.

**Gates on:** nothing. **Provable once:** a VM provisioned from it boots and
`bench --version` works over guest SSH.

> Scope guard: this is **one new image variant**, not a general build pipeline
> (spec non-goal "No image build pipeline" still holds). We bake an artifact once
> and register it like any other `Virtual Machine Image`; we do not build a
> reusable image-builder service.

## What exists today

[scripts/sync-image.py](../../../scripts/sync-image.py) bakes a *plain* Ubuntu
image: download squashfs → verify sha → unsquash → mask boot-blockers → install
the network unit → pack a pristine ext4 of `default_disk_gb`. `SyncImageInputs`
is a frozen dataclass of URLs + sha256s. The image is immutable after insert; you
rotate by inserting a new `Virtual Machine Image` row (auto-syncs to Active
servers) and archiving the old one (spec/08-images.md).

The bench tooling is [bench-cli](../../../../references/bench-cli): zero-dep,
stdlib-only, `uv`-managed venv, single `bench.toml`, built-in Procfile runner and
**Admin UI** (`:8002`). Install is `curl … install.sh | bash`; bring-up is
`bench new` → `bench init` → `bench new-site` → `bench start`.

## Decision: bake at sync time, not a separate artifact

Two ways to get a bench-preinstalled rootfs:

1. **Pre-built artifact** — build the rootfs elsewhere, host the squashfs, point a
   new `Virtual Machine Image` row at its URL+sha. Pro: `sync-image.py`
   unchanged. Con: needs an out-of-band build host + somewhere to host the
   squashfs; the "how was this baked" step lives outside the repo.
2. **Bake during sync** — add a *bench-install step* to the rootfs normalization,
   between unsquash and pack-ext4, gated by a new input flag. Pro: the recipe is
   in-repo and reproducible from the same Ubuntu source; no external hosting.
   Con: `sync-image.py` grows a branch; the sync Task runs longer for this
   variant.

**Choose (2)** — bake during sync, behind a `bench_preinstall: bool` (or a
distinct `command`/variant) on `SyncImageInputs`. It keeps the recipe in the repo
(Taste: the source of truth is the code), needs no external infra, and reuses the
existing immutable-image rotation. The plain-Ubuntu path is unchanged when the
flag is false.

## Build steps

### 1. Extend the sync inputs
- Add `bench_preinstall: bool = False` to `SyncImageInputs` (or, if the branch
  gets large, a sibling `BenchImageInputs` with its own `command`).
- When false, `sync-image.py` behaves exactly as today (don't regress the plain
  path — the e2e shared droplet uses it).

### 2. The bench-bake step (new, in the rootfs-normalize phase)
Runs inside the unsquashed rootfs, *before* packing the ext4. Via chroot or
systemd-nspawn into the extracted tree (match whatever the existing normalize
step uses to write files into the rootfs). Bake, in order:

1. Base packages bench-cli needs at *runtime* — MariaDB server, Redis, the
   Python the venv builds against, `curl`, `git`. (bench-cli auto-installs `uv`;
   confirm whether it needs it pre-seeded for an offline first boot.)
2. Install bench-cli itself (clone to a fixed path, put `bench` on PATH) — the
   `install.sh` recipe, but pinned to a commit, not `curl | bash` at boot.
3. `bench init` the Frappe clone + venv so the heavy `uv` install is baked, not
   per-site. Pin the Frappe branch/version.
4. **Leave it stopped and site-less.** Bake the *bench*, not a *site* — sites are
   per-VM and carry the routing identity (Contract A). Ensure MariaDB/Redis are
   enabled to start on boot but no site exists yet.
5. Mask/disable anything that would try to phone home or block boot (same spirit
   as the existing cloud-init/resolved masking).

### 3. Determinism & idempotency
- Keep `sync-image.py` idempotent: if the baked ext4 already exists with a
  matching sha, exit early (existing behavior).
- The bake must be reproducible enough that the resulting ext4 has a *stable*
  identity — pin every version (Ubuntu source, bench-cli commit, Frappe branch).
  Document the pins next to the image row.

### 4. Register the image
- Add the golden variant to the canonical image catalogue — the same place
  `DEFAULT_IMAGE` / `MINIMAL_IMAGE` live ([atlas/bootstrap.py](../../../atlas/bootstrap.py))
  and the e2e `_config.py`. Give it a clear name, e.g. `ubuntu-24.04-bench`.
- Decide whether it becomes `Atlas Settings.default_user_image` (so self-serve
  Sites land on it via [placement.py](../../../atlas/atlas/placement.py)) — it
  should, since a Site needs bench. Confirm that doesn't change the default for
  plain VM creation if those must stay on plain Ubuntu (they likely should — only
  Sites need bench).

## Open questions to resolve while building

- **MariaDB root password / bench.toml secret.** bench-cli's `bench.toml` holds a
  MariaDB password. Baking one secret into a shared image is wrong (every VM gets
  the same). Resolve: generate per-VM at provision (like the existing per-VM
  identity injection in `rootfs.inject_identity`) or at `deploy-site.py` time
  (03). Flag this to 03 — it's a shared decision.
- **Disk size.** A baked bench + Frappe clone + venv is large; bump
  `default_disk_gb` for this variant and confirm it still fits the thin-pool
  defaults.
- **Boot time vs bake time tradeoff.** Anything left for first boot (e.g. `uv`
  resolving) adds to the "few seconds" budget. Bake aggressively.

## How it's proven

- **Host fact (the only one that matters here):** provision a VM from the golden
  image, wait for guest SSH (`wait_for_ssh`, exists), and assert `bench --version`
  (or the bench path) responds over `connection_for_guest`. That proves the bake
  survived unsquash→pack→provision→boot.
- This is a natural addition to the **image-sync** e2e use case
  ([image_sync.py](../../../atlas/tests/e2e/use_cases/image_sync.py)) — it already
  exercises the sync pipeline; add a smoke that syncs *the golden variant* and
  boots it. (Bias toward extending an existing use case — spec README "Testing".)

## Spec & docs (slice of [06](./06-spec-and-docs.md))
- [spec/08-images.md](../../../spec/08-images.md) — document the bench-preinstall
  variant of the sync pipeline and the new input flag; note the pins.
- The new self-serve chapter cross-links here for "where the bench comes from".
