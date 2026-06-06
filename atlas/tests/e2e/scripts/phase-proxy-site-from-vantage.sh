#!/bin/bash
# Proxy e2e (§2.1 release gate — the south-side path that has never been tested):
# from INSIDE the proxy guest, reach a site VM's :80 over public IPv6. This is
# the exact hop nginx makes on every request (`proxy_pass http://[<site-v6>]:80`),
# proved from the proxy's own vantage rather than the controller's.
#
# Per spec/12-proxy.md and the atlas-vm-inbound-ipv6-only memory: a site's :80 is
# reachable only over the public v6 internet (proxy and site are on different
# hosts, no private fabric), and inbound TCP:80 from the proxy's vantage was an
# open release gate. This probe closes it.
#
# Method: host stages this script, SSHes into the PROXY guest over its v6, and
# from there curls the SITE guest's [v6]:80, asserting the site's identity marker
# comes back. Two hops: host -> proxy guest (ssh), proxy guest -> site (curl).
#
# Inputs:
#   PROXY_IPV6     - the proxy guest's /128 (SSH destination, the vantage).
#   SITE_IPV6      - the site guest's /128 (curl target, the south hop).
#   SITE_MARKER    - the unique string the site's :80 echoes (identity assertion).
#   SSH_PRIVATE_KEY - private half of the key Atlas injected into both guests.

set -euo pipefail
{ set +x; } 2>/dev/null

: "${PROXY_IPV6:?}"
: "${SITE_IPV6:?}"
: "${SITE_MARKER:?}"
: "${SSH_PRIVATE_KEY:?}"

key_file="$(mktemp /tmp/atlas-proxy-vantage-XXXXXX.key)"
trap 'rm -f "$key_file"' EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" >"$key_file"
chmod 0600 "$key_file"

# Wait for sshd in the proxy guest.
deadline=$((SECONDS + 90))
while ! ssh \
        -i "$key_file" \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o BatchMode=yes \
        -o ConnectTimeout=5 \
        "root@${PROXY_IPV6}" true 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "proxy guest ssh not ready after 90s at ${PROXY_IPV6}" >&2
        exit 1
    fi
    sleep 3
done

ssh \
    -i "$key_file" \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    "root@${PROXY_IPV6}" bash -s "$SITE_IPV6" "$SITE_MARKER" <<'REMOTE'
set -euo pipefail
site_ipv6="$1"
site_marker="$2"

fail() { echo "FAIL: $*" >&2; exit 1; }

# Poll: the site's tiny :80 server may take a moment to come up. The bracketed
# literal forces the v6 path and needs no DNS — the real proxy_pass target shape.
deadline=$((SECONDS + 60))
while :; do
    body="$(curl -6 --max-time 10 -sS "http://[${site_ipv6}]:80/" 2>/dev/null || true)"
    if printf '%s' "$body" | grep -q "$site_marker"; then
        echo "OK proxy->site over v6: saw ${site_marker}"
        exit 0
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
        fail "proxy guest could not reach site [${site_ipv6}]:80 (marker ${site_marker} not seen); last body: ${body:-<empty>}"
    fi
    sleep 3
done
REMOTE
