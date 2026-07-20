"""CLI entry point for the benchmark harness.

Usage::

    python -m services.api.benchmarks --scale small
    python -m services.api.benchmarks --scale medium --out /tmp/bench.json
    KNIGHTMIND_DEV_SQLITE=1 python -m services.api.benchmarks --scale small

The ``KNIGHTMIND_DEV_SQLITE=1`` env is only needed so importing the app modules
does not fail fast on a missing ``DATABASE_URL``; the harness itself always binds
its own throwaway SQLite DB regardless.
"""

import argparse
import json
import os
import sys

# Importing services.api.db (transitively) requires a resolvable DB URL. The
# harness never uses this engine (it binds its own), but the import must succeed,
# so opt into the documented SQLite dev fallback if nothing else is configured.
os.environ.setdefault("KNIGHTMIND_DEV_SQLITE", "1")

from services.api.benchmarks.runner import SCALES, format_human, run_all  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KnightMind API benchmarks")
    parser.add_argument(
        "--scale",
        choices=list(SCALES),
        default="small",
        help="fixture scale (default: small)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="override iterations per benchmark (default: scale-dependent)",
    )
    parser.add_argument(
        "--seed", type=int, default=1234, help="fixture RNG seed (default: 1234)"
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="write machine-readable JSON results to this path",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the human-readable summary on stdout",
    )
    args = parser.parse_args(argv)

    payload = run_all(args.scale, iterations=args.iterations, seed=args.seed)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"wrote JSON results -> {args.out}", file=sys.stderr)

    if not args.quiet:
        print(format_human(payload))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
