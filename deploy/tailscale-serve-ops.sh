#!/usr/bin/env bash
set -euo pipefail

# Expose the KnightMind API on the tailnet so the operator board can reach the
# gated endpoints. `tailscale serve` proxies HTTPS on this node's MagicDNS name
# to the local API and injects identity headers (Tailscale-User-Login, ...),
# which the operator gate (services/api/auth.py) requires.
#
# This path is tailnet-only and NEVER internet-exposed (that's `tailscale
# funnel`, which we deliberately do not use here). The public internet keeps
# reaching the API through the public Caddy at api.guessme.world, which strips
# those identity headers — so /ops/status, the bare /users list, etc. stay
# operator-only.
#
# Run once on the API host (claw-home) as the tailscale-owning user:
#   ./deploy/tailscale-serve-ops.sh
#
# Reach it from any tailnet device at:
#   https://<node>.<your-tailnet>.ts.net/ops/health   (public probe, works)
#   https://<node>.<your-tailnet>.ts.net/ops/status   (operator-gated, works on tailnet)
#
# Point the operator frontend build at that base via apps/web/.env.ops
# (VITE_API_BASE). Optionally pin access to your login:
#   export KNIGHTMIND_OPS_TAILNET_USER="you@github"   # in the API's env

API_PORT="${API_PORT:-8000}"

echo "Enabling tailscale serve -> http://127.0.0.1:${API_PORT} (tailnet-only, HTTPS)"
tailscale serve --bg --https=443 "http://127.0.0.1:${API_PORT}"

echo
echo "Current serve config:"
tailscale serve status

cat <<'NOTE'

Verify from another tailnet device:
  curl -s https://<node>.<tailnet>.ts.net/ops/health            # -> 200 (public probe)
  curl -s -o /dev/null -w '%{http_code}\n' \
       https://<node>.<tailnet>.ts.net/ops/status               # -> 200 on tailnet

Confirm the public path stays locked (from off-tailnet):
  curl -s -o /dev/null -w '%{http_code}\n' https://api.guessme.world/ops/status   # -> 404
  curl -s -o /dev/null -w '%{http_code}\n' \
       -H 'Tailscale-User-Login: spoof@x' https://api.guessme.world/ops/status    # -> 404 (header stripped)

To disable:
  tailscale serve --https=443 off
NOTE
