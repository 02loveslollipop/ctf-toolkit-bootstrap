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
    "opencrow-reversing-mcp",
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


def ghidra_install_dir() -> str | None:
    ghidra_headless = shutil.which("ghidra-headless")
    if not ghidra_headless:
        return None
    resolved = Path(ghidra_headless).resolve()
    if resolved.name == "analyzeHeadless" and resolved.parent.name == "support":
        return str(resolved.parent.parent)
    return str(resolved.parent)


def r2ghidra_dec_available() -> bool:
    if shutil.which("r2") is None:
        return False
    try:
        result = subprocess.run(
            ["r2", "-q", "-e", "scr.color=0", "-c", "L~ghidra", "malloc://1"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


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
        python_modules = check_python_modules(args.env)
        system_tools = check_system_tools() | check_conda_tools(args.env)
        ghidra_dir = ghidra_install_dir()
        capabilities = {
            "base_reversing_stack": all(
                [
                    python_modules.get("capstone", False),
                    python_modules.get("lief", False),
                    system_tools.get("r2", False),
                    system_tools.get("objdump", False),
                ]
            ),
            "decompilation": system_tools.get("ghidra-headless", False) and ghidra_dir is not None,
            "symbolic_execution": python_modules.get("angr", False) and python_modules.get("claripy", False),
            "r2ghidra_dec": r2ghidra_dec_available(),
        }
        payload = {
            "capabilities": capabilities,
            "ghidra_install_dir": ghidra_dir,
            "python_modules": python_modules,
            "system_tools": system_tools,
        }
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or str(exc), file=sys.stderr)
        return exc.returncode or 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
