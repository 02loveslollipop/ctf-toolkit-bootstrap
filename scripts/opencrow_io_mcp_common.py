#!/usr/bin/env python3
"""Shared helpers for OpenCROW I/O MCP servers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from opencrow_mcp_core import run_command


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BACKEND_FALLBACKS = {
    "nc_async_session.py": REPO_ROOT / "skills" / "netcat-async" / "scripts" / "nc_async_session.py",
    "ssh_async_session.py": REPO_ROOT / "skills" / "ssh-async" / "scripts" / "ssh_async_session.py",
    "minecraft_async.py": REPO_ROOT / "skills" / "minecraft-async" / "scripts" / "minecraft_async.py",
}

SESSION_NAME_ERROR = (
    "Session name must be a single non-empty path segment without '/' or '\\' and cannot be '.' or '..'."
)


def backend_script_path(script_name: str) -> Path:
    direct_path = SCRIPT_DIR / script_name
    if direct_path.exists():
        return direct_path
    fallback_path = BACKEND_FALLBACKS.get(script_name)
    if fallback_path is not None and fallback_path.exists():
        return fallback_path
    return direct_path


def run_backend_script(
    script_name: str,
    args: list[str],
    *,
    cwd: str | Path | None = None,
    timeout_sec: int = 120,
) -> dict[str, Any]:
    command = [sys.executable, str(backend_script_path(script_name)), *args]
    return run_command(command, cwd=cwd, timeout_sec=timeout_sec)


def parse_json_stdout(result: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return json.loads(str(result.get("stdout", "")))
    except json.JSONDecodeError:
        return None


def normalize_session_name(value: object, *, default: str | None = None) -> str:
    name = "" if value is None else str(value).strip()
    if not name and default is not None:
        name = default
    if not name:
        raise ValueError("Session name is required.")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(SESSION_NAME_ERROR)
    return name


def session_artifact_paths(base_dir: str | Path, name: str) -> list[str]:
    root = Path(base_dir) / normalize_session_name(name)
    return [
        str(root),
        str(root / "pid"),
        str(root / "meta.json"),
        str(root / "tx.fifo"),
        str(root / "io.log"),
        str(root / "rx.raw"),
        str(root / "daemon.log"),
    ]
