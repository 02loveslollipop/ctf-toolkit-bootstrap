#!/usr/bin/env python3
"""Verify Python modules for the OpenCROW crypto toolbox."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


PYTHON_MODULES = [
    "z3",
    "fpylll",
    "Crypto",
]

SYSTEM_TOOLS = [
    "hashcat",
    "john",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the OpenCROW crypto toolbox inside a conda environment."
    )
    parser.add_argument(
        "--env",
        default="ctf",
        help="Conda environment to use. Default: ctf.",
    )
    return parser


def check_python_modules(env_name: str) -> dict[str, bool]:
    code = (
        "import importlib.util as u, json\n"
        f"mods = {PYTHON_MODULES!r}\n"
        "print(json.dumps({m: bool(u.find_spec(m)) for m in mods}))\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as handle:
        handle.write(code)
        temp_path = Path(handle.name)
    try:
        result = subprocess.run(
            ["conda", "run", "-n", env_name, "python", str(temp_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        temp_path.unlink(missing_ok=True)
    return json.loads(result.stdout.strip())


def check_system_tools() -> dict[str, bool]:
    import shutil

    return {tool: shutil.which(tool) is not None for tool in SYSTEM_TOOLS}


def main() -> int:
    args = build_parser().parse_args()

    try:
        payload = {
            "python_modules": check_python_modules(args.env),
            "system_tools": check_system_tools(),
        }
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or str(exc), file=sys.stderr)
        return exc.returncode or 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
