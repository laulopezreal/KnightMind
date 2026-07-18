"""Operator-only access gate for the ops/admin surface.

The API is public (api.guessme.world) because the normal app needs it, so the
operator endpoints can't just live on a private port. Instead they are gated to
the Tailscale network: `tailscale serve` proxies the API on the tailnet and
injects a `Tailscale-User-Login` header carrying the authenticated tailnet
identity. Requests that arrive through the public ingress never carry a trusted
value for that header because the public Caddy strips any inbound
`Tailscale-User-*` headers before proxying (see deploy/public-caddy/Caddyfile).

The gate fails closed: no identity header -> 404. We return 404 rather than 403
so the public internet can't even confirm the operator endpoints exist.

Set KNIGHTMIND_OPS_TAILNET_USER to pin access to one tailnet login (e.g.
"you@github"); leave it unset to allow any authenticated tailnet identity.
"""

import os

from fastapi import Header, HTTPException


def require_operator(
    tailscale_user_login: str | None = Header(default=None),
) -> str:
    """FastAPI dependency that allows only authenticated tailnet operators.

    Raises 404 for any request lacking a valid Tailscale identity header.
    """
    if not tailscale_user_login:
        # No tailnet identity -> treat the endpoint as if it does not exist.
        raise HTTPException(status_code=404, detail="Not Found")

    expected = os.environ.get("KNIGHTMIND_OPS_TAILNET_USER")
    if expected and tailscale_user_login != expected:
        raise HTTPException(status_code=404, detail="Not Found")

    return tailscale_user_login
