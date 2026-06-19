#!/bin/bash
# e2e: assert a promoted snapshot looks exactly like a synced base image on host.
#  - the base image LV atlas-image-<IMAGE_NAME> is a READ-ONLY, sized block device
#  - the image dir holds the reused KERNEL (hard-linked from the source image)
#  - the image dir holds the rootfs presence sentinel <ROOTFS_FILENAME>
set -euo pipefail

: "${IMAGE_NAME:?}"
: "${ROOTFS_FILENAME:?}"
: "${KERNEL_FILENAME:?}"

image_lv="/dev/atlas/atlas-image-${IMAGE_NAME}"
image_dir="/var/lib/atlas/images/${IMAGE_NAME}"

# The promoted rootfs is an LV: a block-special device, not a file. Confirm it
# exists, is sized (activated), and is READ-ONLY (lvchange --permission r).
if ! sudo test -b "$image_lv"; then
    echo "promoted base image LV missing or not a block device: ${image_lv}" >&2
    exit 1
fi
size="$(sudo blockdev --getsize64 "$image_lv")"
[ "${size:-0}" -gt 0 ] || { echo "promoted image LV has zero size: ${image_lv}" >&2; exit 1; }
# blockdev --getro prints 1 for a read-only device. The base image must be RO so a
# stray write can't corrupt the shared origin every per-VM disk snapshots from.
ro="$(sudo blockdev --getro "$image_lv")"
[ "${ro}" = "1" ] || { echo "promoted image LV is not read-only: ${image_lv} (getro=${ro})" >&2; exit 1; }

# The reused kernel must be present (provision-vm.py hard-links it into each jail).
sudo test -f "${image_dir}/${KERNEL_FILENAME}" || {
    echo "promoted image kernel missing: ${image_dir}/${KERNEL_FILENAME}" >&2
    exit 1
}
# The rootfs presence sentinel must be present (provision-vm.py step-0 stat-probe).
sudo test -f "${image_dir}/${ROOTFS_FILENAME}" || {
    echo "promoted image rootfs sentinel missing: ${image_dir}/${ROOTFS_FILENAME}" >&2
    exit 1
}
echo "promoted image present: ${image_lv} (${size} bytes, ro=${ro}), kernel + sentinel in ${image_dir}"
