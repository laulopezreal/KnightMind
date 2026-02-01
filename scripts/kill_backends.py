#!/usr/bin/env python3
"""Kill processes listening on backend ports 8000 (API) and 8001 (Stockfish)."""
import os
import signal
import subprocess
import sys

PORTS = (8000, 8001)


def main() -> None:
    pids: set[int] = set()
    for port in PORTS:
        try:
            out = subprocess.run(
                ["lsof", "-t", "-i", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                for line in out.stdout.strip().split():
                    if line.isdigit():
                        pids.add(int(line))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    if not pids:
        print("No backend processes found on ports 8000 or 8001.", file=sys.stderr)
        sys.exit(0)
    for pid in sorted(pids):
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to PID {pid}")
        except ProcessLookupError:
            pass
        except PermissionError:
            print(f"Cannot kill PID {pid} (permission denied)", file=sys.stderr)
    print("Done.")


if __name__ == "__main__":
    main()
