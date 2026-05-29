#!/bin/bash
# Turn a fresh Ubuntu 24.04 host into a Firecracker host.
# Idempotent. Re-run after editing this file to roll forward.
#
# Inputs (environment variables):
#   FIRECRACKER_VERSION  - e.g. v1.15.1
#   ARCHITECTURE         - e.g. x86_64 (must match `uname -m`)

set -euo pipefail

: "${FIRECRACKER_VERSION:?required}"
: "${ARCHITECTURE:?required}"

if [ "$(uname -m)" != "$ARCHITECTURE" ]; then
    echo "Architecture mismatch: host is $(uname -m), expected $ARCHITECTURE" >&2
    exit 1
fi

# 1. KVM must be present.
if [ ! -r /dev/kvm ] || [ ! -w /dev/kvm ]; then
    echo "/dev/kvm not available. Server must support nested virtualization." >&2
    exit 1
fi

# 2. Install packages.
#    A freshly-booted cloud image still has cloud-init / unattended-upgrades
#    running its own apt for the first minutes, holding the apt locks. apt's
#    `DPkg::Lock::Timeout` does NOT cover the `apt-get update` *lists* lock
#    (/var/lib/apt/lists/lock) on this apt version, so update failed fast with
#    "Could not get lock" and left fresh droplets Broken. Wait for cloud-init
#    to finish and the locks to clear before touching apt at all.
export DEBIAN_FRONTEND=noninteractive

# cloud-init owns the first-boot apt run; block until it's done (best-effort —
# `status --wait` returns promptly if cloud-init isn't present or already done).
sudo cloud-init status --wait >/dev/null 2>&1 || true

# Belt-and-suspenders: poll the apt/dpkg locks in case unattended-upgrades or a
# late apt timer still holds them after cloud-init reports done. Cap the wait so
# a genuinely stuck lock still surfaces as a bootstrap failure rather than hang.
wait_for_apt_locks() {
    local deadline=$(( SECONDS + 300 ))
    while sudo fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock >/dev/null 2>&1; do
        if [ "$SECONDS" -ge "$deadline" ]; then
            echo "apt/dpkg lock still held after 300s; aborting bootstrap" >&2
            return 1
        fi
        echo "waiting for apt/dpkg lock to be released..." >&2
        sleep 5
    done
}
wait_for_apt_locks

sudo apt-get -o DPkg::Lock::Timeout=300 update
sudo apt-get -o DPkg::Lock::Timeout=300 install -y \
    ca-certificates \
    curl \
    e2fsprogs \
    iproute2 \
    jq \
    nftables \
    squashfs-tools

# 3. Install Firecracker + jailer binaries. Both ship in the same release
#    tarball; production runs every VM under the jailer (de-privileged, chrooted,
#    cgroup-isolated), so we install both. Gate on EITHER binary being absent or
#    at the wrong version, so a host bootstrapped before the jailer existed picks
#    it up on re-run.
INSTALLED_FIRECRACKER="$(/usr/local/bin/firecracker --version 2>/dev/null | head -n1 | awk '{print $2}' || true)"
INSTALLED_JAILER="$(/usr/local/bin/jailer --version 2>/dev/null | head -n1 | awk '{print $2}' || true)"
WANTED_VERSION="${FIRECRACKER_VERSION#v}"
if [ "$INSTALLED_FIRECRACKER" != "$WANTED_VERSION" ] || [ "$INSTALLED_JAILER" != "$WANTED_VERSION" ]; then
    cd /tmp
    sudo rm -rf firecracker-install
    mkdir firecracker-install
    cd firecracker-install
    curl -fsSL \
        "https://github.com/firecracker-microvm/firecracker/releases/download/${FIRECRACKER_VERSION}/firecracker-${FIRECRACKER_VERSION}-${ARCHITECTURE}.tgz" \
        | tar -xz
    sudo install -m 0755 "release-${FIRECRACKER_VERSION}-${ARCHITECTURE}/firecracker-${FIRECRACKER_VERSION}-${ARCHITECTURE}" \
        /usr/local/bin/firecracker
    sudo install -m 0755 "release-${FIRECRACKER_VERSION}-${ARCHITECTURE}/jailer-${FIRECRACKER_VERSION}-${ARCHITECTURE}" \
        /usr/local/bin/jailer
    cd /tmp
    rm -rf firecracker-install
fi

# 4. IPv6 forwarding and neighbor proxy.
sudo install -m 0644 /dev/stdin /etc/sysctl.d/60-atlas.conf <<'CONF'
net.ipv6.conf.all.forwarding = 1
net.ipv6.conf.default.forwarding = 1
net.ipv6.conf.all.proxy_ndp = 1
CONF
sudo sysctl --system >/dev/null

# 5. nftables scaffold. Two-shot: create-if-missing, then ensure chains exist.
sudo nft list table inet atlas >/dev/null 2>&1 || sudo nft add table inet atlas
sudo nft list chain inet atlas forward >/dev/null 2>&1 || \
    sudo nft "add chain inet atlas forward { type filter hook forward priority filter; policy accept; }"

# 6. Directories.
sudo install -d -m 0700 /var/lib/atlas
sudo install -d -m 0700 /var/lib/atlas/images
sudo install -d -m 0700 /var/lib/atlas/virtual-machines
sudo install -d -m 0700 /var/lib/atlas/run
sudo install -d -m 0755 /var/lib/atlas/bin

# 7. Helper scripts and systemd unit are uploaded alongside this script by
#    the caller, into /var/lib/atlas/bin/ and /etc/systemd/system/. See
#    spec/03-bootstrapping.md for the exact list. scp preserves source perms,
#    so set the executable bit here to be safe — systemd invokes these
#    directly via ExecStartPost / ExecStopPost.
sudo chmod 0755 /var/lib/atlas/bin/*.sh
sudo systemctl daemon-reload

# 8. Record state for Atlas to pick up. Single JSON file is the canonical
#    source of truth; the trailing `cat` keeps the same bytes on stdout so
#    operators tailing the Task can still see the values.
sudo install -d -m 0755 /var/lib/atlas
sudo jq -nc \
    --arg firecracker_version "$(/usr/local/bin/firecracker --version | head -n1 | awk '{print $2}')" \
    --arg jailer_version "$(/usr/local/bin/jailer --version | head -n1 | awk '{print $2}')" \
    --arg kernel_version "$(uname -r)" \
    --arg architecture "$(uname -m)" \
    '{firecracker_version: $firecracker_version,
      jailer_version: $jailer_version,
      kernel_version: $kernel_version,
      architecture: $architecture}' \
    | sudo tee /var/lib/atlas/bootstrap.json >/dev/null

cat /var/lib/atlas/bootstrap.json
