#!/bin/bash
# Host-side network for a VM. Invoked by ExecStartPre in the systemd unit
# (must run before the jailer's ExecStart so the namespace + tap exist when the
# jailer joins the netns and Firecracker opens the tap). Reads
# /var/lib/atlas/virtual-machines/$1/network.env. Idempotent.
#
# Approach: each VM gets its OWN network namespace so a jail breakout cannot see
# the host's interfaces, the uplink, or other VMs' taps. The VM's tap lives
# inside that namespace; a veth pair bridges the namespace back to the host.
#
# The server has DigitalOcean's /64 prefix routed to it, but only a /124 is
# *usable* (DO routes the /64 to the droplet; the rest has no route inside DO's
# fabric). So we hand out addresses inside a fixed /124 and use proxy-NDP on the
# uplink to make the upstream router believe each VM address is on-link. The
# guest still uses fe80::1 (on the tap, inside its namespace) as its gateway;
# the only change from the host-netns model is one extra link-local hop across
# the veth, fully inside the host.

set -euo pipefail

virtual_machine_name="${1:?virtual machine name required}"
. "/var/lib/atlas/virtual-machines/${virtual_machine_name}/network.env"

: "${TAP_DEVICE:?missing in network.env}"
: "${VIRTUAL_MACHINE_IPV6:?missing in network.env}"
: "${ATLAS_NETNS:?missing in network.env}"
: "${HOST_VETH:?missing in network.env}"
: "${NAMESPACE_VETH:?missing in network.env}"

uplink="$(ip -j -6 route show default | jq -r '.[0].dev')"

# Idempotent nftables scaffold. Bootstrap creates these on first install, but
# they are not persisted across host reboots; recreating here keeps each VM's
# network self-contained.
sudo nft list table inet atlas >/dev/null 2>&1 || sudo nft add table inet atlas
sudo nft list chain inet atlas forward >/dev/null 2>&1 || \
    sudo nft "add chain inet atlas forward { type filter hook forward priority filter; policy accept; }"

# Sysctls cleared on reboot if not persisted via /etc/sysctl.d. Bootstrap writes
# /etc/sysctl.d/60-atlas.conf, but a defensive re-apply costs nothing. Forwarding
# now also carries traffic across the veth seam.
sudo sysctl -q -w net.ipv6.conf.all.forwarding=1 net.ipv6.conf.all.proxy_ndp=1 || true

# 1. Network namespace. Clean re-create so a restart starts from a known state
#    (deleting the namespace takes its tap + the namespace-side veth with it).
sudo ip netns del "$ATLAS_NETNS" 2>/dev/null || true
sudo ip link del "$HOST_VETH" 2>/dev/null || true
sudo ip netns add "$ATLAS_NETNS"

# 2. veth pair: one end stays on the host, the other moves into the namespace.
sudo ip link add "$HOST_VETH" type veth peer name "$NAMESPACE_VETH"
sudo ip link set "$NAMESPACE_VETH" netns "$ATLAS_NETNS"

# 3. The namespace forwards between the veth (uplink side) and the tap (guest
#    side), so it needs its own forwarding sysctl — namespaces have independent
#    network sysctls and default to forwarding off.
sudo ip netns exec "$ATLAS_NETNS" sysctl -q -w net.ipv6.conf.all.forwarding=1 || true

# 4. Tap inside the namespace. vnet_hdr matches what Firecracker expects; fe80::1
#    is the guest's gateway (unchanged guest contract). Route the VM's /128 to
#    the tap so replies reach the guest.
sudo ip netns exec "$ATLAS_NETNS" ip tuntap add "$TAP_DEVICE" mode tap vnet_hdr
sudo ip netns exec "$ATLAS_NETNS" ip link set "$TAP_DEVICE" up
sudo ip netns exec "$ATLAS_NETNS" ip -6 addr add fe80::1/64 dev "$TAP_DEVICE" nodad
sudo ip netns exec "$ATLAS_NETNS" ip -6 route replace "${VIRTUAL_MACHINE_IPV6}/128" dev "$TAP_DEVICE"

# 5. Bring up both ends of the veth with link-local addressing, and point the
#    namespace's default route at the host end so guest egress flows out the
#    veth toward the uplink.
sudo ip link set "$HOST_VETH" up
sudo ip -6 addr add fe80::2/64 dev "$HOST_VETH" nodad
sudo ip netns exec "$ATLAS_NETNS" ip link set "$NAMESPACE_VETH" up
sudo ip netns exec "$ATLAS_NETNS" ip -6 addr add fe80::3/64 dev "$NAMESPACE_VETH" nodad
sudo ip netns exec "$ATLAS_NETNS" ip -6 route replace default via fe80::2 dev "$NAMESPACE_VETH"

# 6. On the host: route the VM's /128 into the namespace via the veth, and answer
#    NDP for the VM on the uplink so the upstream router delivers its packets here.
sudo ip -6 route replace "${VIRTUAL_MACHINE_IPV6}/128" via fe80::3 dev "$HOST_VETH"
sudo ip -6 neigh replace proxy "$VIRTUAL_MACHINE_IPV6" dev "$uplink"

# 7. Forwarding rules, matching the host-side veth (the tap is no longer in the
#    host namespace to match on).
sudo nft add rule inet atlas forward ip6 daddr "$VIRTUAL_MACHINE_IPV6" oifname "$HOST_VETH" accept
sudo nft add rule inet atlas forward ip6 saddr "$VIRTUAL_MACHINE_IPV6" iifname "$HOST_VETH" accept
