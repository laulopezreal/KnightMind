#!/usr/bin/env python3
"""Start the KnightMind API backend (services/api) with uvicorn on port 8000."""

import os
import subprocess
import sys


def main() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    api_dir = os.path.join(repo_root, "services", "api")
    if not os.path.isdir(api_dir):
        print(f"Not found: {api_dir}", file=sys.stderr)
        sys.exit(1)
    os.chdir(api_dir)
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "main:app", "--reload", "--port", "8000"],
        cwd=api_dir,
    )


if __name__ == "__main__":
    main()
