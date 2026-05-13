#!/usr/bin/env python3
"""Compatibility shims for legacy OpenCROW terminal entrypoints."""

from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SEARCH_PATHS = [SCRIPT_DIR, SCRIPT_DIR.parent]
for candidate in SEARCH_PATHS:
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from constellation.client import ConstellationAPIClient, ConstellationAPIError
from constellation.config import load_client_settings


IGNORED_DIRS = {".git", ".hg", ".svn", ".opencrow-constellation", "__pycache__", ".venv", "node_modules"}


def read_description(workspace: Path) -> str:
    path = workspace / "DESCRIPTION.md"
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace").strip()
    return f"Dashboard-managed OpenCROW challenge created from `{workspace}`."


def workspace_zip(workspace: Path) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix=f"opencrow-{workspace.name}-", suffix=".zip", delete=False)
    zip_path = Path(handle.name)
    handle.close()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in workspace.rglob("*"):
            rel = path.relative_to(workspace)
            if any(part in IGNORED_DIRS for part in rel.parts):
                continue
            if path.is_file():
                archive.write(path, rel.as_posix())
    return zip_path


def create_dashboard_challenge(
    *,
    mode: str,
    workspace: Path,
    title: str,
    description: str,
    category: str,
    challenge_type: str,
    model: str | None,
    runtime_id: str | None,
    upload_workspace: bool,
    dry_run: bool,
) -> int:
    settings = load_client_settings()
    payload: dict[str, Any] = {
        "title": title,
        "description": description,
        "category": category,
        "challenge_type": challenge_type,
        "runtime_id": runtime_id,
        "handoff_urls": [],
        "settings": {"model": model} if model else None,
        "start_agent": not upload_workspace,
    }
    if dry_run:
        print("mode=" + mode)
        print("api_base_url=" + settings.api_base_url)
        print("challenge_payload=")
        print(ConstellationAPIClient.pretty_json({key: value for key, value in payload.items() if value is not None}))
        print(f"upload_workspace={upload_workspace}")
        return 0

    client = ConstellationAPIClient(settings)
    try:
        result = client.create_challenge(**{key: value for key, value in payload.items() if value is not None})
        challenge = result["challenge"]
        if upload_workspace:
            archive = workspace_zip(workspace)
            client.upload_challenge_files(challenge["id"], [archive])
            role = "solo" if challenge_type == "single_agent" else "master"
            client.create_agent(
                challenge["id"],
                role=role,
                display_name=f"{challenge['title']} {role}",
                runtime_id=challenge.get("runtime_id"),
                model=model,
            )
        print(f"Created dashboard challenge: {challenge['title']} ({challenge['id']})")
        print(f"Open it in Constellation: /challenges/{challenge['id']}")
        return 0
    except ConstellationAPIError as exc:
        print(f"opencrow runtime shim failed: {exc}", file=sys.stderr)
        return 1


def autosetup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Create a dashboard-managed reconnaissance challenge.")
    parser.add_argument("--category", default=None)
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--model")
    parser.add_argument("--runtime-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--disable-sandbox", action="store_true")
    parser.add_argument("--ack-missing-description", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    workspace = Path(args.output_dir).expanduser().resolve()
    return create_dashboard_challenge(
        mode="autosetup",
        title=workspace.name,
        workspace=workspace,
        description=read_description(workspace),
        category=(args.category or "misc").lower(),
        challenge_type="single_agent",
        model=args.model,
        runtime_id=args.runtime_id,
        upload_workspace=not args.no_upload,
        dry_run=args.dry_run,
    )


def exploit(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Create a dashboard-managed solve challenge.")
    parser.add_argument("--model")
    parser.add_argument("--runtime-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--full-auto", action="store_true")
    parser.add_argument("--disable-sandbox", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    workspace = Path.cwd().resolve()
    return create_dashboard_challenge(
        mode="exploit",
        title=workspace.name,
        workspace=workspace,
        description=read_description(workspace),
        category="misc",
        challenge_type="single_agent",
        model=args.model,
        runtime_id=args.runtime_id,
        upload_workspace=not args.no_upload,
        dry_run=args.dry_run,
    )


def join(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Create a dashboard-managed Constellation challenge from a topic.")
    parser.add_argument("topic")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--agent-name")
    parser.add_argument("--model")
    parser.add_argument("--runtime-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--full-auto", action="store_true")
    parser.add_argument("--disable-sandbox", action="store_true")
    parser.add_argument("--no-watcher", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    workspace = Path(args.workspace).expanduser().resolve()
    settings = load_client_settings()
    client = ConstellationAPIClient(settings)
    description = read_description(workspace)
    title = args.topic
    category = "misc"
    try:
        topic = client.get_topic(args.topic).get("topic", {})
        title = str(topic.get("title") or title)
        description = str(topic.get("description") or description)
        category = str(topic.get("category") or category)
    except Exception:
        pass
    return create_dashboard_challenge(
        mode="join",
        title=title,
        workspace=workspace,
        description=description,
        category=category,
        challenge_type="constellation",
        model=args.model,
        runtime_id=args.runtime_id,
        upload_workspace=not args.no_upload,
        dry_run=args.dry_run,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shim", choices=("autosetup", "exploit", "join"))
    args, rest = parser.parse_known_args()
    if args.shim == "autosetup":
        return autosetup(rest)
    if args.shim == "exploit":
        return exploit(rest)
    return join(rest)


if __name__ == "__main__":
    raise SystemExit(main())
