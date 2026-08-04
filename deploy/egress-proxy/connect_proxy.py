#!/usr/bin/env python3
"""Minimal HTTP CONNECT proxy for knightmind container egress.

Why this exists
---------------
knightmind-api's container egresses via the DIRECT Hetzner IP (forced by the
`iif km-bridge lookup main` policy rule that pins bridge traffic to the main
routing table). Cloudflare's edge RESETS connections from that IP, so
api.chess.com (game import) and api.anthropic.com (AI diagnosis) are unreachable
from the container, while they work from the host, which egresses via the
Tailscale exit node (an IP Cloudflare accepts).

This proxy runs in the HOST network namespace (`network_mode: host`), so its own
outbound connections use the host's working Tailscale egress. knightmind-api is
pointed at it via HTTPS_PROXY, so its Cloudflare-bound calls borrow the good
path.

Security
--------
This box is multi-tenant: other containers share km-bridge and can reach the
bind, so gating only the PORT would make this an open :443 relay for the whole
box. It therefore gates the DESTINATION too, via `ALLOWED_HOST_SUFFIXES` (the
app's real egress deps), and only allows CONNECT to port 443. It binds the
internal bridge gateway (172.18.0.1), not a public interface.

CONNECT-only: it blindly tunnels TCP for HTTPS and never terminates TLS, so it
never sees plaintext. The Anthropic API key and all chess data stay end-to-end
encrypted to the origin.

Stdlib only, no dependencies. Threaded, one thread per tunnel, with an idle
timeout so a forgotten tunnel cannot pin a thread and two fds forever.
"""

import os
import select
import socket
import threading

LISTEN_HOST = os.environ.get("PROXY_LISTEN_HOST", "172.18.0.1")
LISTEN_PORT = int(os.environ.get("PROXY_LISTEN_PORT", "8899"))
ALLOWED_PORTS = {443}
# Destination allowlist: the app's real egress dependencies. Suffix match covers
# subdomains (api.chess.com / www.chess.com / explorer.lichess.ovh /
# api.anthropic.com). Update this list if the app gains a new external host.
ALLOWED_HOST_SUFFIXES = ("chess.com", "anthropic.com", "lichess.ovh")
BUFSIZE = 65536
MAX_REQUEST_BYTES = 8192
CONNECT_TIMEOUT = 10
IDLE_TIMEOUT = 300  # tear down an established tunnel idle this many seconds


def _host_allowed(host: str) -> bool:
    h = host.strip().lower()
    if not h:
        return False
    return any(h == s or h.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


def _pump(a: socket.socket, b: socket.socket) -> None:
    """Bidirectionally splice two sockets until either closes or the tunnel idles."""
    sockets = [a, b]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], IDLE_TIMEOUT)
            if not readable:
                return  # idle timeout
            for src in readable:
                dst = b if src is a else a
                data = src.recv(BUFSIZE)
                if not data:
                    return
                dst.sendall(data)
    except OSError:
        return
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def _handle(client: socket.socket) -> None:
    upstream: socket.socket | None = None
    try:
        client.settimeout(CONNECT_TIMEOUT)
        # Read the request line + headers (up to the blank line), bounded.
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = client.recv(BUFSIZE)
            if not chunk:
                client.close()
                return
            buf += chunk
            if len(buf) > MAX_REQUEST_BYTES:
                client.sendall(b"HTTP/1.1 431 Request Header Fields Too Large\r\n\r\n")
                client.close()
                return

        head, _, rest = buf.partition(b"\r\n\r\n")
        request_line = head.split(b"\r\n", 1)[0].decode("latin1")
        parts = request_line.split(" ")
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            client.close()
            return

        host, _, port_str = parts[1].partition(":")
        port = int(port_str) if port_str else 443
        if port not in ALLOWED_PORTS or not _host_allowed(host):
            client.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            client.close()
            return

        try:
            upstream = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            client.close()
            return

        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        client.settimeout(None)
        # Forward any bytes the client pipelined after the CONNECT headers (e.g.
        # a TLS ClientHello coalesced into the same segment) before splicing.
        if rest:
            upstream.sendall(rest)
        _pump(client, upstream)
    except Exception:
        for s in (client, upstream):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(128)
    print(
        f"egress CONNECT proxy on {LISTEN_HOST}:{LISTEN_PORT} "
        f"(allow {', '.join(ALLOWED_HOST_SUFFIXES)} :443)",
        flush=True,
    )
    while True:
        try:
            client, _ = srv.accept()
        except OSError:
            continue  # one bad accept must not kill the listener
        threading.Thread(target=_handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
