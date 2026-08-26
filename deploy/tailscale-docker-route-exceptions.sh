#!/usr/bin/env bash
set -euo pipefail

# Keep local Docker bridge subnets local when Tailscale exit-node routing is active.
# Run as root after Docker and Tailscale are up.
#
# Why this exists:
# Tailscale can install a broad default route in table 52. On this host that can
# catch Docker bridge destinations such as 172.18.0.0/16, making host ->
# container and Caddy/Tailscale-Serve -> API traffic hang. The fix is to add
# throw routes for every local Docker bridge subnet in table 52, plus policy
# rules that force packets arriving from Docker bridges back through main.

if [ "${EUID}" -ne 0 ]; then
  echo "ERROR: must run as root" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker command not found" >&2
  exit 1
fi

if ! command -v ip >/dev/null 2>&1; then
  echo "ERROR: ip command not found" >&2
  exit 1
fi

# Static fallbacks cover the bridge subnets historically used on claw-home even
# if Docker is temporarily slow during boot.
declare -A SUBNETS=(
  ["172.17.0.0/16"]=1
  ["172.18.0.0/16"]=1
  ["172.19.0.0/16"]=1
  ["172.20.0.0/16"]=1
)

declare -A BRIDGES=(
  [docker0]=1
)

# Discover active and inactive Docker bridge networks dynamically so the repair
# survives recreated networks and new project bridges.
while IFS= read -r network_id; do
  [ -n "$network_id" ] || continue

  driver="$(docker network inspect -f '{{ .Driver }}' "$network_id" 2>/dev/null || true)"
  [ "$driver" = "bridge" ] || continue

  explicit_bridge="$(docker network inspect -f '{{ index .Options "com.docker.network.bridge.name" }}' "$network_id" 2>/dev/null || true)"
  if [ -n "$explicit_bridge" ] && [ "$explicit_bridge" != "<no value>" ]; then
    BRIDGES["$explicit_bridge"]=1
  else
    full_id="$(docker network inspect -f '{{ .Id }}' "$network_id" 2>/dev/null || true)"
    if [ -n "$full_id" ]; then
      BRIDGES["br-${full_id:0:12}"]=1
    fi
  fi

  while IFS= read -r subnet; do
    [ -n "$subnet" ] || continue
    SUBNETS["$subnet"]=1
  done < <(docker network inspect -f '{{range .IPAM.Config}}{{if .Subnet}}{{println .Subnet}}{{end}}{{end}}' "$network_id" 2>/dev/null || true)
done < <(docker network ls -q 2>/dev/null || true)

# Discover the real bridge device names FIRST. `ip -o link show type bridge`
# is the reliable filter; `ip -o -4 addr show type bridge` is NOT on this
# iproute2 — it returns every interface's addresses (lo, enp8s0, tailscale0
# included), which would add throw-routes for the host's public IP, the
# Tailscale IP and loopback into table 52.
while IFS= read -r bridge; do
  [ -n "$bridge" ] || continue
  BRIDGES["$bridge"]=1
done < <(ip -o link show type bridge 2>/dev/null | awk -F': ' '{print $2}' | cut -d'@' -f1)

# Then read each real bridge's own subnet, catching bridges whose Docker
# metadata is absent but the interface remains. Only actual bridge devices
# are queried, so table 52 never sees a non-Docker address.
for bridge in "${!BRIDGES[@]}"; do
  if ! ip link show "$bridge" >/dev/null 2>&1; then
    continue
  fi
  while IFS= read -r cidr; do
    [ -n "$cidr" ] || continue
    SUBNETS["$cidr"]=1
  done < <(ip -o -4 addr show dev "$bridge" 2>/dev/null | awk '{print $4}' | python3 -c '
import ipaddress, sys
seen=set()
for line in sys.stdin:
    line=line.strip()
    if not line:
        continue
    try:
        net=str(ipaddress.ip_interface(line).network)
    except ValueError:
        continue
    if net not in seen:
        seen.add(net)
        print(net)
')
done

for subnet in "${!SUBNETS[@]}"; do
  ip route replace throw "$subnet" table 52
done

# Public Docker services must reply through the normal public interface, not the
# Tailscale exit-node default in table 52. Route packets entering from Docker
# bridges through the main table before Tailscale's catch-all rule.
pref=90
for bridge in $(printf '%s\n' "${!BRIDGES[@]}" | sort); do
  if ip link show "$bridge" >/dev/null 2>&1; then
    while ip rule del pref "$pref" 2>/dev/null; do :; done
    ip rule add pref "$pref" iif "$bridge" lookup main
    pref=$((pref + 1))
  fi
done

ip route flush cache || true

echo "Tailscale table 52 Docker exceptions:"
for subnet in $(printf '%s\n' "${!SUBNETS[@]}" | sort -V); do
  ip route show table 52 | grep -F "throw $subnet" || true
done

echo
echo "Docker bridge policy rules:"
for bridge in $(printf '%s\n' "${!BRIDGES[@]}" | sort); do
  ip rule show | grep -F "iif $bridge lookup main" || true
done

echo
echo "Route probes:"
for subnet in $(printf '%s\n' "${!SUBNETS[@]}" | sort -V); do
  probe="${subnet%0/16}2"
  echo "$probe -> $(ip route get "$probe" 2>&1 | head -1 || true)"
done
