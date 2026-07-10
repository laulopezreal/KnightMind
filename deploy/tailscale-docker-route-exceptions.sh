#!/usr/bin/env bash
set -euo pipefail

# Keep claw-home Docker bridge subnets local when Tailscale exit-node routing is active.
# Run as root after Docker and Tailscale are up.
# This mirrors the existing working exception for open-wearables' 172.20.0.0/16.

SUBNETS=(
  "172.17.0.0/16" # default docker0
  "172.18.0.0/16" # knightmind_default
  "172.19.0.0/16" # opik-opik_default
  "172.20.0.0/16" # open-wearables_default, already expected but made idempotent
)

for subnet in "${SUBNETS[@]}"; do
  ip route replace throw "$subnet" table 52
done

ip route flush cache || true

echo "Tailscale table 52 Docker exceptions:"
ip route show table 52 | grep -E 'throw 172\.(17|18|19|20)\.0\.0/16' || true

echo
for target in 172.17.0.2 172.18.0.2 172.19.0.2 172.20.0.2; do
  echo "$target -> $(ip route get "$target" 2>&1 | head -1)"
done
