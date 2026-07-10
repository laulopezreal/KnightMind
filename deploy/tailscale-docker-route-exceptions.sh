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
bridge_for_network() {
  local network="$1"
  local explicit id

  explicit="$(docker network inspect -f '{{ index .Options "com.docker.network.bridge.name" }}' "$network" 2>/dev/null || true)"
  if [ -n "$explicit" ] && [ "$explicit" != "<no value>" ]; then
    printf '%s\n' "$explicit"
    return 0
  fi

  id="$(docker network inspect -f '{{ .Id }}' "$network" 2>/dev/null || true)"
  if [ -n "$id" ]; then
    printf 'br-%s\n' "${id:0:12}"
  fi
}

declare -A BRIDGE_PREFS=(
  [docker0]=90
)

declare -A NETWORK_PREFS=(
  [knightmind_default]=91
  [opik-opik_default]=92
  [open-wearables_default]=93
)

for network in "${!NETWORK_PREFS[@]}"; do
  bridge="$(bridge_for_network "$network")"
  if [ -n "$bridge" ]; then
    BRIDGE_PREFS[$bridge]="${NETWORK_PREFS[$network]}"
  fi
done

for bridge in "${!BRIDGE_PREFS[@]}"; do
  if ip link show "$bridge" >/dev/null 2>&1; then
    pref="${BRIDGE_PREFS[$bridge]}"
    while ip rule del pref "$pref" 2>/dev/null; do :; done
    ip rule add pref "$pref" iif "$bridge" lookup main
  fi
done

ip route flush cache || true

echo "Tailscale table 52 Docker exceptions:"
ip route show table 52 | grep -E 'throw 172\.(17|18|19|20)\.0\.0/16' || true

echo
echo "Docker bridge policy rules:"
for bridge in "${!BRIDGE_PREFS[@]}"; do
  ip rule show | grep -F "iif $bridge lookup main" || true
done

echo
for target in 172.17.0.2 172.18.0.2 172.19.0.2 172.20.0.2; do
  echo "$target -> $(ip route get "$target" 2>&1 | head -1 || true)"
done
