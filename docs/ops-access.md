# Operator board access (tailnet-gated)

The `/ops` **Operator Board** (system health, jobs, telemetry, user switcher,
storage parity) is for the operator only. Because the API is public
(`api.guessme.world` — the normal app needs it), the operator surface is gated
to the **Tailscale network** rather than hidden in the (public) frontend.

## Why the old approach didn't work

Previously `/ops` was a `<Navigate to="/">` redirect. That hid the *page* but
not the *data*: the operator endpoints on the public API had no auth, so anyone
could `curl https://api.guessme.world/ops/status` or `/users`. A static frontend
can never be a security boundary — the boundary has to be the API.

## How it works now

Two independent layers; the API layer is the real boundary:

1. **API gate** — `services/api/auth.py::require_operator` protects the operator
   endpoints (`GET /ops/status`, bare `GET /users`; liveness `/ops/ping`,
   `/ops/health`, `/ops/ready` stay public for monitors). It requires the
   `Tailscale-User-Login` header that `tailscale serve` injects for
   authenticated tailnet users. **No header → 404** (fails closed; 404 not 403
   so the endpoint's existence isn't confirmed publicly).
   - Optional: set `KNIGHTMIND_OPS_TAILNET_USER=you@github` to pin access to one
     tailnet identity instead of any authenticated tailnet user.

2. **Public edge strips the header** — `deploy/public-caddy/Caddyfile` removes
   inbound `Tailscale-User-*` headers, so a public client can't forge the
   identity to get through the gate.

3. **Frontend flag** (convenience / defence-in-depth) — the `/ops` route only
   exists when the app is built with `VITE_ENABLE_OPS=true`. The public
   Cloudflare build leaves it unset, so the route redirects home and the Ops
   chunk is never even emitted.

```
Public internet ──▶ Caddy (api.guessme.world) ──strips Tailscale-* ──▶ API ──▶ /ops/status = 404
Operator device ──▶ tailscale serve (<node>.ts.net) ──injects Tailscale-User-Login ──▶ API ──▶ /ops/status = 200
```

## One-time server setup (API host, `claw-home`)

```bash
# 1. Expose the API on the tailnet (HTTPS on the node's MagicDNS name).
./deploy/tailscale-serve-ops.sh

# 2. (optional) pin to your identity — add to the API service env, then restart:
#    KNIGHTMIND_OPS_TAILNET_USER=you@github

# 3. Redeploy the public Caddy so the header-strip takes effect:
docker compose -f deploy/public-caddy/docker-compose.yml up -d --force-recreate
```

## Using the board (operator, on a tailnet device)

```bash
cp apps/web/.env.ops.example apps/web/.env.ops
# edit VITE_API_BASE to your node's https://<node>.<tailnet>.ts.net
cd apps/web && npm run dev -- --mode ops        # or build with --mode ops and serve on the tailnet
# open http://localhost:5173/ops
```

## Security assumptions (what this does and doesn't guarantee)

- **The public API can only be reached through Caddy.** The API container is
  published as `127.0.0.1:${API_PORT}:8000` (loopback-only), so there is no way
  to hit `:8000` directly from the internet and skip the header-strip. If you
  ever change that port publish to `0.0.0.0`, this gate is bypassed — don't.
- **On the tailnet path, `tailscale serve` is the source of truth for identity.**
  It sets `Tailscale-User-Login` from the authenticated tailnet connection. Set
  `KNIGHTMIND_OPS_TAILNET_USER` to pin access to your login so that even another
  member of your tailnet can't reach the board.
- **Out of scope (pre-existing, still public because the normal app needs them):**
  `GET /jobs/{id}` and `GET /import/status?username=` return status for a *known*
  job id / username. They aren't part of the operator gate; gating them would
  break the public app. Flag separately if you want them tightened.

## CORS for the operator build

The operator frontend (e.g. `localhost:5173`) calling the tailnet API base is
**cross-origin**, so the API's `KNIGHTMIND_CORS_ORIGINS` must include that origin
or the browser will block the response. The gate itself doesn't rely on cookies
— `tailscale serve` injects the identity header server-side — so you only need
the origin allow-listed, not credentialed CORS.

## Verify

```bash
# Public path is locked (run from OFF the tailnet):
curl -s -o /dev/null -w '%{http_code}\n' https://api.guessme.world/ops/status                 # 404
curl -s -o /dev/null -w '%{http_code}\n' -H 'Tailscale-User-Login: spoof@x' \
     https://api.guessme.world/ops/status                                                     # 404 (stripped)

# Tailnet path works (run from a tailnet device):
curl -s -o /dev/null -w '%{http_code}\n' https://<node>.<tailnet>.ts.net/ops/status           # 200

# Liveness stays public either way:
curl -s -o /dev/null -w '%{http_code}\n' https://api.guessme.world/ops/health                 # 200 / 503
```
