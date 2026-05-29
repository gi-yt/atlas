#!/bin/bash
# Delete all on-host state for a VM. Idempotent.
#
# Inputs:
#   VIRTUAL_MACHINE_NAME

set -euo pipefail

: "${VIRTUAL_MACHINE_NAME:?required}"

unit="firecracker-vm@${VIRTUAL_MACHINE_NAME}.service"
vm_directory="/var/lib/atlas/virtual-machines/${VIRTUAL_MACHINE_NAME}"

sudo systemctl disable --now "$unit" 2>/dev/null || true

# In case the unit failed before ExecStopPost ran, tear down networking
# explicitly. vm-network-down.sh is itself idempotent.
if sudo test -f "${vm_directory}/network.env"; then
    sudo /var/lib/atlas/bin/vm-network-down.sh "$VIRTUAL_MACHINE_NAME" || true
fi

# Removing the VM directory takes the jail tree (rootfs, kernel link, config,
# API socket) with it — they all live under jail/ inside this directory.
sudo rm -rf "$vm_directory"

echo "Deleted ${VIRTUAL_MACHINE_NAME}."
