#!/usr/bin/env python3
"""Minimal HTTP CONNECT proxy for knightmind container egress.

Why this exists
---------------
knightmind-api's container egresses via the DIRECT Hetzner IP (forced by the
`iif km-bridge lookup main` policy rule, priority 95, that pins bridge traffic
to the main routing table). Cloudflare's edge RESETS connections from that IP,
so api.chess.com (game import) and api.anthropic.com (AI diagnosis) are
unreachable from the container, while they work fine from the host, which
egresses via the Tailscale exit node (an IP Cloudflare accepts).

This proxy runs in the HOST network namespace (`network_mode: host`), so its own
outbound connections use the host's working Tailscale egress. knightmind-api is
pointed at it via HTTPS_PROXY, so its Cloudflare-bound calls borrow the good
path.

Security
--------
CONNECT-only: it blindly tunnels TCP for HTTPS and never terminates TLS, so it
never sees plaintext. The Anthropic API key and all chess data stay end-to-end
encrypted to the origin. Only CONNECT to port 443 is allowed, and it binds to
the internal bridge gateway (172.18.0.1) rather than a public interface, to
keep this from being an open relay.

Stdlib only, no dependencies. Threaded, one thread per tunnel.
"""

import os
import select
import socket
import threading

LISTEN_HOST = os.environ.get("PROXY_LISTEN_HOST", "172.18.0.1")
LISTEN_PORT = int(os.environ.get("PROXY_LISTEN_PORT", "8899"))
ALLOWED_PORTS = {443}
BUFSIZE = 65536
CONNECT_TIMEOUT = 10


def _pump(a: socket.socket, b: socket.socket) -> None:
    """Bidirectionally splice two sockets until either side closes."""
    sockets = [a, b]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [])
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
    try:
        client.settimeout(CONNECT_TIMEOUT)
        # Read just the request line + headers (up to the blank line).
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = client.recv(BUFSIZE)
            if not chunk or len(buf) > 8192:
                client.close()
                return
            buf += chunk

        request_line = buf.split(b"\r\n", 1)[0].decode("latin1")
        parts = request_line.split(" ")
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            client.close()
            return

        host, _, port_str = parts[1].partition(":")
        port = int(port_str) if port_str else 443
        if port not in ALLOWED_PORTS:
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
        _pump(client, upstream)
    except Exception:
        try:
            client.close()
        except OSError:
            pass


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(128)
    print(f"egress CONNECT proxy listening on {LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    while True:
        client, _ = srv.accept()
        threading.Thread(target=_handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
