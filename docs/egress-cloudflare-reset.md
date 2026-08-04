# Egress incident: Cloudflare resets the container's direct Hetzner IP

**Status:** fixed (host-netns CONNECT proxy). This doc is the durable record and runbook.

## Symptom

From inside `knightmind-api-1`, every outbound HTTPS call to a Cloudflare-fronted
host failed:

- `api.chess.com` (game import, core data source): connection reset during TLS.
- `api.anthropic.com` (AI diagnosis enrichment): connection reset during TLS.

The same calls succeeded from the host shell. In the app this surfaced as
`APIConnectionError` on enrichment and a broken game import, with `enriched: 0`.

## Root cause

Cloudflare's edge **resets connections coming from this box's direct Hetzner
egress IP**. It is not the app, the key, TLS, or MTU:

- Same direct IPv4 path, from the container: `google.com` and `pypi.org` return
  200 in tens of ms; `cloudflare.com` and `1.1.1.1` (Cloudflare) reset. The only
  thing the failures share is Cloudflare.
- MTU is a uniform 1500 end to end, so it is not a fragmentation blackhole.
- From the **host**, chess.com returns 200 and anthropic returns 401 (reached,
  just unauthenticated). Same uplink, same instant; the only variable is the
  egress IP.

The container is affected and the host is not because of the routing split:

- **Host** traffic egresses via the **Tailscale exit node** (ip rule `5270 lookup
  52`), an IP Cloudflare accepts.
- **Container** traffic is forced onto the **direct** path (`enp0s4`, the Hetzner
  public IP) by the policy rule `iif km-bridge lookup main` that
  `deploy/tailscale-docker-route-exceptions.sh` installs to keep bridge return
  traffic symmetric. Cloudflare rejects that IP.

Secondary (real but not the operative cause): the container's **IPv6** egress is
also broken. There is no `iif km-bridge lookup main` override for IPv6, so
container v6 falls through to `lookup 52 -> default dev tailscale0` with no
Tailscale v6 source address, and every v6 connection fails to connect. Cloudflare
hosts are dual-stack, so happy-eyeballs tries v6 first, fails fast, then falls
back to the v4 path that gets reset. Fixing v6 alone would not have solved the
outage; it is worth cleaning up separately.

## The fix

A tiny stdlib **CONNECT proxy** (`deploy/egress-proxy/connect_proxy.py`) runs in
the **host network namespace** (`network_mode: host`), so its own outbound uses
the host's working Tailscale egress. `knightmind-api` is pointed at it via
`HTTPS_PROXY` (`docker-compose.override.yml`). Because it is CONNECT-only it just
tunnels TCP and never terminates TLS, so the Anthropic API key and all chess data
stay end to end encrypted to the origin. It binds to the internal bridge gateway
`172.18.0.1:8899` (not a public interface) and only allows CONNECT to port 443.

### Why a proxy and not a routing change

Sending all container traffic via the exit node (removing/relaxing the
`iif km-bridge lookup main` rule) would likely fix egress but can re-break
inbound: replies to the container's published ports would egress via tailscale0
instead of the interface the request arrived on (asymmetric routing), which is
exactly what that rule exists to prevent, and what took `api.guessme.world` down
once before. The proxy fixes egress without touching host routing, is scoped to
knightmind, and is fully reversible.

## Files

- `docker-compose.override.yml` - the `egress-proxy` service + the `HTTPS_PROXY`/
  `NO_PROXY` env on `api`. Auto-loads (systemd `ExecStart` and the Makefile both
  invoke compose with no `-f`), so it survives restarts and reboots.
- `deploy/egress-proxy/connect_proxy.py` - the proxy itself.

`HTTPS_PROXY` points at `172.18.0.1:8899`, the km-bridge gateway as currently
created. `docker compose up -d` (`make docker-up`) does not recreate the network,
so it is stable; if the network is ever destroyed and recreated on a different
subnet, update both the env and the proxy's `PROXY_LISTEN_HOST` together.

## Verify

```sh
# from the container: both should be reachable (200 / 401), not reset
docker exec knightmind-api-1 sh -c 'curl -sS -o /dev/null -w "%{http_code}\n" https://api.chess.com/pub/player/erik'
docker exec knightmind-api-1 sh -c 'curl -sS -o /dev/null -w "%{http_code}\n" https://api.anthropic.com/v1/models'
docker logs knightmind-egress-proxy   # "egress CONNECT proxy listening on 172.18.0.1:8899"
```

## Rollback

Delete `docker-compose.override.yml`, then `make docker-up`. Back to the prior
state exactly.

## Runbook caveat (diagnostic false green)

`systemctl is-active tailscale-docker-route-exceptions.service` reports `active`
because the unit is `Type=oneshot` with `RemainAfterExit=yes`: that only means the
script ran once, not that the routing is currently correct. Do not lead an egress
or routing diagnosis with it. Check the actual routing facts instead:

```sh
ip rule show | grep km-bridge                 # the iif km-bridge lookup main rule
ip route get 104.16.132.229 from 172.18.0.2 iif km-bridge   # container -> Cloudflare path
```
