#!/bin/bash
# Proxy e2e helper: start a tiny HTTP server on a SITE guest's [::]:80 that echoes
# a unique marker on every path. This is the e2e analog of the compose harness's
# vm-a/vm-b fake upstreams (proxy-design.md §9) — a stand-in Frappe site so the
# proxy has something real to route to over public v6.
#
# Method: host stages this script, SSHes into the SITE guest over its v6, and
# launches a backgrounded `python3 -m http.server`-style listener bound to [::]:80
# that returns the marker. Idempotent: kills any prior instance first, so a re-run
# (retry == re-run) is clean.
#
# Inputs:
#   SITE_IPV6      - the site guest's /128 (SSH destination).
#   SITE_MARKER    - the unique string the listener echoes (proves identity).
#   SSH_PRIVATE_KEY - private half of the key Atlas injected.

set -euo pipefail
{ set +x; } 2>/dev/null

: "${SITE_IPV6:?}"
: "${SITE_MARKER:?}"
: "${SSH_PRIVATE_KEY:?}"

key_file="$(mktemp /tmp/atlas-proxy-site-XXXXXX.key)"
trap 'rm -f "$key_file"' EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" >"$key_file"
chmod 0600 "$key_file"

deadline=$((SECONDS + 90))
while ! ssh \
        -i "$key_file" \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o BatchMode=yes \
        -o ConnectTimeout=5 \
        "root@${SITE_IPV6}" true 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "site guest ssh not ready after 90s at ${SITE_IPV6}" >&2
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
    "root@${SITE_IPV6}" bash -s "$SITE_MARKER" <<'REMOTE'
set -euo pipefail
site_marker="$1"

# Tear down any prior instance (idempotent re-run).
pkill -f atlas-e2e-site-server || true
sleep 1

cat >/tmp/atlas-e2e-site-server.py <<PY
import http.server, socketserver
MARKER = "${site_marker}".encode()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"atlas-e2e-site " + MARKER + b" host=" + self.headers.get("Host", "").encode())
    def log_message(self, *a): pass
class S(socketserver.TCPServer):
    address_family = __import__("socket").AF_INET6
    allow_reuse_address = True
with S(("::", 80), H) as httpd:
    httpd.serve_forever()
PY

# Background it, detached from the SSH session, so it survives this connection.
setsid bash -c 'exec -a atlas-e2e-site-server python3 /tmp/atlas-e2e-site-server.py' \
    </dev/null >/tmp/atlas-e2e-site-server.log 2>&1 &

# Confirm it bound to :80 locally before returning.
deadline=$((SECONDS + 20))
while ! curl -6 --max-time 5 -sS "http://[::1]:80/" 2>/dev/null | grep -q "$site_marker"; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "FAIL: site server never came up on [::]:80; log:" >&2
        cat /tmp/atlas-e2e-site-server.log >&2 || true
        exit 1
    fi
    sleep 2
done
echo "OK site server up on [::]:80 echoing ${site_marker}"
REMOTE
