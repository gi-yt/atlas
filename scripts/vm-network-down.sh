#!/bin/bash
# Symmetric teardown for vm-network-up.sh. Invoked by ExecStopPost on the
# systemd unit. Idempotent: missing rules, devices and namespaces are not an
# error.

set -euo pipefail

virtual_machine_name="${1:?virtual machine name required}"
network_env="/var/lib/atlas/virtual-machines/${virtual_machine_name}/network.env"

# If the env file is gone (terminate-vm already ran) we still want to do our
# best to clean up. Try to source, but accept absence.
if [ -f "$network_env" ]; then
    . "$network_env"
fi

uplink="$(ip -j -6 route show default | jq -r '.[0].dev' 2>/dev/null || true)"

# Proxy-NDP entry on the uplink.
if [ -n "${VIRTUAL_MACHINE_IPV6:-}" ] && [ -n "$uplink" ]; then
    sudo ip -6 neigh del proxy "$VIRTUAL_MACHINE_IPV6" dev "$uplink" 2>/dev/null || true
fi

# Host-side /128 route into the namespace.
if [ -n "${VIRTUAL_MACHINE_IPV6:-}" ] && [ -n "${HOST_VETH:-}" ]; then
    sudo ip -6 route del "${VIRTUAL_MACHINE_IPV6}/128" dev "$HOST_VETH" 2>/dev/null || true
fi

# The namespace owns the tap and the namespace-side veth; deleting it takes both.
if [ -n "${ATLAS_NETNS:-}" ]; then
    sudo ip netns del "$ATLAS_NETNS" 2>/dev/null || true
fi

# The host-side veth end (its peer went with the namespace, but delete defensively).
if [ -n "${HOST_VETH:-}" ]; then
    sudo ip link del "$HOST_VETH" 2>/dev/null || true
fi

# Delete the two nft rules by handle. Look them up by VM IPv6.
if [ -n "${VIRTUAL_MACHINE_IPV6:-}" ]; then
    handles="$(sudo nft -a list chain inet atlas forward 2>/dev/null \
        | awk -v ip="$VIRTUAL_MACHINE_IPV6" '$0 ~ ip {print $NF}')"
    for handle in $handles; do
        sudo nft delete rule inet atlas forward handle "$handle" 2>/dev/null || true
    done
fi
