#!/bin/sh
# Expose a local Manthana server to your team over Tailscale — with automatic HTTPS, no domain,
# no certificates, no open firewall ports. Tailscale is a private network (VPN) that connects
# your machines directly; `tailscale serve` publishes a local port on that private network at a
# stable https://<machine>.<tailnet>.ts.net address only your team can reach.
#
# Prereqs (once):
#   - Install Tailscale + log in on this machine and every engineer's laptop: `tailscale up`
#   - Enable MagicDNS + HTTPS certificates in the Tailscale admin console (Settings → DNS).
#   - Start the server first (loopback is fine — Tailscale fronts it):  manthana-server quickstart
#
# Run:  ./scripts/tailscale_serve.sh      (PORT=8000 by default)
set -eu

PORT="${PORT:-8000}"
if ! command -v tailscale >/dev/null 2>&1; then
  echo "✗ tailscale not found — install it from https://tailscale.com/download" >&2
  exit 1
fi

echo "→ publishing http://127.0.0.1:$PORT on your tailnet with HTTPS…"
tailscale serve --bg "http://127.0.0.1:$PORT"

URL="$(tailscale serve status 2>/dev/null | grep -oE 'https://[^ ]+' | head -1)"
URL="${URL:-https://<this-machine>.<your-tailnet>.ts.net}"
echo "✓ team server: $URL"
echo "  enroll with:  manthana-server enroll acme platform --open --server-url $URL"
echo "  (stop sharing later with: tailscale serve --https=443 off)"
