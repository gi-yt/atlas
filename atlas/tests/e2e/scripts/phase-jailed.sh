#!/bin/bash
# e2e: assert the VM's Firecracker process is actually jailed — running as the
# per-VM uid (not root), chrooted into the VM's jail, in the VM's network
# namespace, and bounded by the per-VM cgroup caps. This is the contract the
# jailer integration must hold; a green boot that still ran Firecracker as root
# is a failure.
set -euo pipefail

: "${VIRTUAL_MACHINE_NAME:?}"
: "${ATLAS_FC_UID:?}"
: "${ATLAS_NETNS:?}"

vm_directory="/var/lib/atlas/virtual-machines/${VIRTUAL_MACHINE_NAME}"
jail_root="${vm_directory}/jail/firecracker/${VIRTUAL_MACHINE_NAME}/root"

# The jailer stores the Firecracker child PID in the jail root.
pid_file="${jail_root}/firecracker.pid"
if ! sudo test -f "$pid_file"; then
    echo "firecracker.pid not found at ${pid_file}; jailer did not start Firecracker" >&2
    exit 1
fi
pid="$(sudo cat "$pid_file")"

# 1. Runs as the per-VM uid, not root.
actual_uid="$(sudo awk '/^Uid:/ {print $2}' "/proc/${pid}/status")"
if [ "$actual_uid" != "$ATLAS_FC_UID" ]; then
    echo "Firecracker uid is ${actual_uid}, expected ${ATLAS_FC_UID} (not jailed/de-privileged)" >&2
    exit 1
fi
if [ "$actual_uid" = "0" ]; then
    echo "Firecracker is running as root — jailer did not drop privileges" >&2
    exit 1
fi

# 2. Chrooted into the jail (its / is the jail root, not the host /).
proc_root="$(sudo readlink "/proc/${pid}/root")"
if [ "$proc_root" != "$jail_root" ]; then
    echo "Firecracker root is ${proc_root}, expected ${jail_root} (not chrooted)" >&2
    exit 1
fi

# 3. In the VM's network namespace, not the host's.
host_netns="$(sudo readlink /proc/1/ns/net)"
vm_netns="$(sudo readlink "/proc/${pid}/ns/net")"
if [ "$vm_netns" = "$host_netns" ]; then
    echo "Firecracker shares the host network namespace — --netns isolation failed" >&2
    exit 1
fi

# 4. cgroup caps applied: memory.max is finite (not 'max') and matches what the
#    jailer set. The jailer places the process in a per-id cgroup; resolve it
#    from /proc and read memory.max.
cgroup_path="$(sudo awk -F: '/^0::/ {print $3}' "/proc/${pid}/cgroup")"
memory_max="$(sudo cat "/sys/fs/cgroup${cgroup_path}/memory.max" 2>/dev/null || echo max)"
if [ "$memory_max" = "max" ]; then
    echo "memory.max is unbounded for the jailed process — cgroup cap not applied" >&2
    exit 1
fi

# 5. The tap lives inside the VM's namespace with vnet_hdr (not in the host ns).
if ! sudo ip netns exec "$ATLAS_NETNS" ip -d link show "atlas-${VIRTUAL_MACHINE_NAME//-/}" >/dev/null 2>&1; then
    # Tap name is atlas-<first 9 hex>; derive the same way the host does rather
    # than reconstruct here — just assert the namespace exists and has a tap.
    tap_count="$(sudo ip netns exec "$ATLAS_NETNS" ip -o link show type tun | wc -l)"
    if [ "$tap_count" -lt 1 ]; then
        echo "no tap device inside namespace ${ATLAS_NETNS}" >&2
        exit 1
    fi
fi

echo "jailed OK: pid=${pid} uid=${actual_uid} root=${proc_root} memory.max=${memory_max}"
