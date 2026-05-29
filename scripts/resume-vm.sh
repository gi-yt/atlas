#!/bin/bash
# Resume a Paused VM: unfreeze its vCPUs via Firecracker's API socket.
# Idempotent: resuming an already-running microVM is ignored by Firecracker
# (returns 2xx).
#
# Inputs:
#   VIRTUAL_MACHINE_NAME  - UUID; selects the API socket

set -euo pipefail

: "${VIRTUAL_MACHINE_NAME:?required}"

# The API socket is created by Firecracker inside its jail (a host-filesystem
# unix socket; the VM's network namespace doesn't affect reaching it).
socket="/var/lib/atlas/virtual-machines/${VIRTUAL_MACHINE_NAME}/jail/firecracker/${VIRTUAL_MACHINE_NAME}/root/run/firecracker.socket"
if [ ! -S "$socket" ]; then
    echo "API socket ${socket} not present; is the VM running?" >&2
    exit 1
fi

sudo curl --fail --silent --show-error --unix-socket "$socket" \
    -X PATCH "http://localhost/vm" \
    -H "Content-Type: application/json" \
    -d '{"state": "Resumed"}'

echo "Resumed ${VIRTUAL_MACHINE_NAME}."
