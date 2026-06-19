#!/bin/bash
# e2e hygiene: remove a promoted base image's on-host artifacts (the LV + dir).
# A base image LV is PROTECTED in the lifecycle, so this is a deliberate test-only
# force-remove of an e2e-minted promoted image — NOT something the app ever does.
# Idempotent: missing LV / dir is a no-op.
set -euo pipefail

: "${IMAGE_NAME:?}"
: "${IMAGE_LV:?}"

# A base image LV is read-only; lvremove -f removes it regardless.
if sudo lvs --noheadings "atlas/${IMAGE_LV}" >/dev/null 2>&1; then
    sudo lvremove -f "atlas/${IMAGE_LV}"
fi
sudo rm -rf "/var/lib/atlas/images/${IMAGE_NAME}"
echo "removed promoted image ${IMAGE_NAME} (${IMAGE_LV})"
