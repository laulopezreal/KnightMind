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

# Public Docker services must reply through the normal public interface, not the
# Tailscale exit-node default in table 52. Route packets entering from Docker
# bridges through the main table before Tailscale's catch-all rule.
declare -A BRIDGES=(
  [docker0]=90
  [br-46f5c9c9df08]=91  # knightmind_default
  [br-3e840af1085f]=92  # opik-opik_default
  [br-85c629823612]=93  # open-wearables_default
  [br-87d9ccbd3b58]=94  # knightmind public-caddy_default
)

for bridge in "${!BRIDGES[@]}"; do
  if ip link show "$bridge" >/dev/null 2>&1; then
    pref="${BRIDGES[$bridge]}"
    while ip rule del pref "$pref" 2>/dev/null; do :; done
    ip rule add pref "$pref" iif "$bridge" lookup main
  fi
done

ip route flush cache || true

echo "Tailscale table 52 Docker exceptions:"
ip route show table 52 | grep -E 'throw 172\.(17|18|19|20)\.0\.0/16' || true

echo
echo "Docker bridge policy rules:"
ip rule show | grep -E 'iif (docker0|br-46f5c9c9df08|br-3e840af1085f|br-85c629823612|br-87d9ccbd3b58).*lookup main' || true

echo
for target in 172.17.0.2 172.18.0.2 172.19.0.2 172.20.0.2 172.21.0.2; do
  echo "$target -> $(ip route get "$target" 2>&1 | head -1)"
done
