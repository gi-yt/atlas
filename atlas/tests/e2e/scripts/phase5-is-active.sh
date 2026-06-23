#!/bin/bash
# Phase 5 e2e: assert the VM's systemd unit is active.

set -euo pipefail

: "${VIRTUAL_MACHINE_NAME:?}"

for _ in $(seq 1 30); do
    if systemctl is-active --quiet "firecracker-vm@${VIRTUAL_MACHINE_NAME}.service"; then
        exit 0
    fi

    sleep 1
done

echo "VM never became active"
exit 1
