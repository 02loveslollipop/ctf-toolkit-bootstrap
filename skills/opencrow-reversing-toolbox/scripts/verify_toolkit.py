#!/usr/bin/env python3
"""Verify Python modules and native tools for the OpenCROW reversing toolbox."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PYTHON_MODULES = [
    "angr",
    "claripy",
    "capstone",
    "unicorn",
    "keystone",
    "ropper",
    "r2pipe",
    "lief",
    "qiling",
]

SYSTEM_TOOLS = [
    "ghidra-headless",
    "r2",
    "objdump",
    "strace",
    "ltrace",
    "binwalk",
]

CONDA_TOOLS = [
    "frida-ps",
    "ROPgadget",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the OpenCROW reversing toolbox inside a conda environment."
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
    return {tool: shutil.which(tool) is not None for tool in SYSTEM_TOOLS}


def check_conda_tools(env_name: str) -> dict[str, bool]:
    code = (
        "import json, shutil\n"
        f"tools = {CONDA_TOOLS!r}\n"
        "print(json.dumps({tool: bool(shutil.which(tool)) for tool in tools}))\n"
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


def main() -> int:
    args = build_parser().parse_args()

    try:
        payload = {
            "python_modules": check_python_modules(args.env),
            "system_tools": check_system_tools() | check_conda_tools(args.env),
        }
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or str(exc), file=sys.stderr)
        return exc.returncode or 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
