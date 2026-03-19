#!/usr/bin/env python3
"""Resolve OpenCROW tool selections and export install state."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT_DIR / "scripts" / "tool_catalog.json"


@dataclass(frozen=True)
class Catalog:
    raw: dict[str, Any]
    toolboxes: dict[str, dict[str, Any]]
    tools: dict[str, dict[str, Any]]


def load_catalog() -> Catalog:
    raw = json.loads(CATALOG_PATH.read_text())
    toolboxes = {entry["id"]: entry for entry in raw["toolboxes"]}
    tools = {entry["id"]: entry for entry in raw["tools"]}
    return Catalog(raw=raw, toolboxes=toolboxes, tools=tools)


def catalog_home() -> Path:
    override = os.environ.get("OPENCROW_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home()


def state_path(catalog: Catalog) -> Path:
    return catalog_home() / catalog.raw["state_relpath"]


def quoted_array(values: list[str]) -> str:
    return "(" + " ".join(shlex.quote(value) for value in values) + ")"


def normalize_toolboxes(catalog: Catalog, toolboxes: list[str] | None) -> list[str]:
    if not toolboxes:
        return [entry["id"] for entry in catalog.raw["toolboxes"]]
    missing = [item for item in toolboxes if item not in catalog.toolboxes]
    if missing:
        raise SystemExit(f"Unknown toolbox ids: {', '.join(sorted(missing))}")
    return toolboxes


def normalize_tools(catalog: Catalog, tool_ids: list[str] | None) -> list[str]:
    if not tool_ids:
        return []
    missing = [item for item in tool_ids if item not in catalog.tools]
    if missing:
        raise SystemExit(f"Unknown tool ids: {', '.join(sorted(missing))}")
    return sorted(dict.fromkeys(tool_ids))


def resolve_selection(
    catalog: Catalog,
    *,
    profile: str | None,
    toolbox_ids: list[str] | None,
    tool_ids: list[str] | None,
    mode: str,
) -> dict[str, Any]:
    explicit_tools = normalize_tools(catalog, tool_ids)
    if explicit_tools:
        selected_tools = explicit_tools
    else:
        selected_toolboxes = normalize_toolboxes(catalog, toolbox_ids)
        wanted_profile = profile or "headless"
        selected_tools = sorted(
            tool_id
            for tool_id, tool in catalog.tools.items()
            if tool["toolbox"] in selected_toolboxes and wanted_profile in tool["profiles"]
        )
    if not selected_tools:
        raise SystemExit("Selection resolved to zero tools.")
    return {
        "mode": mode,
        "profile": profile if not explicit_tools else None,
        "toolboxes": sorted({catalog.tools[tool_id]["toolbox"] for tool_id in selected_tools}),
        "tool_ids": selected_tools,
    }


def parse_number_selection(raw: str, upper_bound: int) -> list[int]:
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_raw, end_raw = part.split("-", 1)
            start = int(start_raw)
            end = int(end_raw)
            values.extend(range(start, end + 1))
        else:
            values.append(int(part))
    normalized = sorted(dict.fromkeys(values))
    if not normalized:
        raise ValueError("No items selected.")
    for value in normalized:
        if value < 1 or value > upper_bound:
            raise ValueError(f"Selection {value} is out of range 1..{upper_bound}.")
    return normalized


def interactive_select(catalog: Catalog) -> dict[str, Any]:
    print("OpenCROW installer")
    print("1. Fast install")
    print("2. Personalized")
    while True:
        mode_raw = input("Choose install mode [1-2]: ").strip()
        if mode_raw in {"1", "2"}:
            break
        print("Enter 1 or 2.")

    toolbox_entries = catalog.raw["toolboxes"]
    print("\nAvailable toolboxes:")
    for idx, toolbox in enumerate(toolbox_entries, start=1):
        print(f"{idx}. {toolbox['display_name']} - {toolbox['summary']}")

    while True:
        toolbox_raw = input("Choose toolbox numbers (e.g. 1,3-5): ").strip()
        try:
            toolbox_indexes = parse_number_selection(toolbox_raw, len(toolbox_entries))
            break
        except ValueError as exc:
            print(exc)
    selected_toolboxes = [toolbox_entries[idx - 1]["id"] for idx in toolbox_indexes]

    if mode_raw == "1":
        print("\nProfiles:")
        print("1. Headless")
        print("2. Full")
        while True:
            profile_raw = input("Choose profile [1-2]: ").strip()
            if profile_raw in {"1", "2"}:
                break
            print("Enter 1 or 2.")
        profile = "headless" if profile_raw == "1" else "full"
        return resolve_selection(
            catalog,
            profile=profile,
            toolbox_ids=selected_toolboxes,
            tool_ids=None,
            mode="fast",
        )

    tools = [
        catalog.tools[tool_id]
        for tool_id in sorted(catalog.tools)
        if catalog.tools[tool_id]["toolbox"] in selected_toolboxes
    ]
    print("\nAvailable tools:")
    for idx, tool in enumerate(tools, start=1):
        profiles = "/".join(tool["profiles"])
        print(f"{idx}. [{profiles}] {tool['display_name']} ({tool['toolbox']})")
    while True:
        tools_raw = input("Choose tool numbers (e.g. 1,4-7): ").strip()
        try:
            tool_indexes = parse_number_selection(tools_raw, len(tools))
            break
        except ValueError as exc:
            print(exc)
    tool_ids = [tools[idx - 1]["id"] for idx in tool_indexes]
    return resolve_selection(
        catalog,
        profile=None,
        toolbox_ids=None,
        tool_ids=tool_ids,
        mode="personalized",
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_selection(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def emit_summary(catalog: Catalog, selection: dict[str, Any]) -> str:
    lines = []
    lines.append("Selected OpenCROW tools:")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tool_id in selection["tool_ids"]:
        grouped[catalog.tools[tool_id]["toolbox"]].append(catalog.tools[tool_id])
    for toolbox_id in selection["toolboxes"]:
        toolbox = catalog.toolboxes[toolbox_id]
        lines.append(f"- {toolbox['display_name']}")
        for tool in sorted(grouped[toolbox_id], key=lambda item: item["display_name"].lower()):
            install_kind = tool["install"]["kind"]
            lines.append(f"  - {tool['display_name']} [{install_kind}]")
            lines.append(f"    homepage: {tool['homepage_url']}")
            lines.append(f"    license:  {tool['license_url']}")
    return "\n".join(lines)


def build_plan(catalog: Catalog, selection: dict[str, Any]) -> dict[str, list[str]]:
    apt_packages = set(catalog.raw["base_apt_packages"])
    pip_packages: list[str] = []
    gem_specs: list[str] = []
    direct_handlers: list[str] = []
    manual_tools: list[str] = []

    for tool_id in selection["tool_ids"]:
        tool = catalog.tools[tool_id]
        install = tool["install"]
        apt_packages.update(install.get("apt_dependencies", []))
        kind = install["kind"]
        if kind == "apt":
            apt_packages.add(install["package"])
        elif kind == "pip":
            pip_packages.append(install["package"])
        elif kind == "gem":
            gem_specs.append(f"{install['package']}:{install.get('version', '')}")
        elif kind == "direct":
            direct_handlers.append(install["handler"])
        elif kind == "manual":
            manual_tools.append(tool_id)

    return {
        "selected_tool_ids": selection["tool_ids"],
        "selected_toolboxes": selection["toolboxes"],
        "apt_packages": sorted(apt_packages),
        "pip_packages": sorted(dict.fromkeys(pip_packages)),
        "gem_specs": sorted(dict.fromkeys(gem_specs)),
        "direct_handlers": sorted(dict.fromkeys(direct_handlers)),
        "manual_tool_ids": sorted(dict.fromkeys(manual_tools)),
    }


def export_plan(catalog: Catalog, selection: dict[str, Any]) -> str:
    plan = build_plan(catalog, selection)

    lines = [
        f"STATE_PATH={shlex.quote(str(state_path(catalog)))}",
        f"SELECTED_TOOL_IDS={quoted_array(plan['selected_tool_ids'])}",
        f"SELECTED_TOOLBOXES={quoted_array(plan['selected_toolboxes'])}",
        f"APT_PACKAGES={quoted_array(plan['apt_packages'])}",
        f"PIP_PACKAGES={quoted_array(plan['pip_packages'])}",
        f"GEM_SPECS={quoted_array(plan['gem_specs'])}",
        f"DIRECT_HANDLERS={quoted_array(plan['direct_handlers'])}",
        f"MANUAL_TOOL_IDS={quoted_array(plan['manual_tool_ids'])}",
    ]
    return "\n".join(lines)


def export_verify_table(catalog: Catalog, selection: dict[str, Any]) -> str:
    lines = []
    for tool_id in selection["tool_ids"]:
        tool = catalog.tools[tool_id]
        verify = tool["verify"]
        lines.append(
            "|".join(
                [
                    tool_id,
                    tool["display_name"],
                    tool["install"]["kind"],
                    verify["kind"],
                    verify["value"],
                ]
            )
        )
    return "\n".join(lines)


def save_state(catalog: Catalog, selection: dict[str, Any], env_name: str) -> dict[str, Any]:
    payload = {
        "env_name": env_name,
        "mode": selection["mode"],
        "profile": selection["profile"],
        "toolboxes": selection["toolboxes"],
        "tool_ids": selection["tool_ids"],
    }
    path = state_path(catalog)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)
    return payload


def load_state(catalog: Catalog, path: Path | None) -> dict[str, Any]:
    state_file = path or state_path(catalog)
    if not state_file.exists():
        raise SystemExit(f"State file not found: {state_file}")
    return json.loads(state_file.read_text())


def verify_selection_from_state(catalog: Catalog, state: dict[str, Any], all_tools: bool) -> dict[str, Any]:
    if all_tools:
        return {
            "mode": "all-tools",
            "profile": None,
            "toolboxes": [entry["id"] for entry in catalog.raw["toolboxes"]],
            "tool_ids": sorted(catalog.tools),
        }
    return {
        "mode": state.get("mode", "saved"),
        "profile": state.get("profile"),
        "toolboxes": state["toolboxes"],
        "tool_ids": state["tool_ids"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve-selection")
    resolve.add_argument("--profile", choices=["headless", "full"])
    resolve.add_argument("--toolbox", action="append", dest="toolboxes")
    resolve.add_argument("--tool", action="append", dest="tools")
    resolve.add_argument("--output", type=Path, required=True)
    resolve.add_argument("--mode", default="noninteractive")

    interactive = subparsers.add_parser("interactive-select")
    interactive.add_argument("--output", type=Path, required=True)

    summary = subparsers.add_parser("print-summary")
    summary.add_argument("--selection", type=Path, required=True)

    plan = subparsers.add_parser("export-plan")
    plan.add_argument("--selection", type=Path, required=True)

    verify_table = subparsers.add_parser("export-verify-table")
    verify_table.add_argument("--selection", type=Path, required=True)

    state_write = subparsers.add_parser("save-state")
    state_write.add_argument("--selection", type=Path, required=True)
    state_write.add_argument("--env", default="ctf")

    state_plan = subparsers.add_parser("export-state-plan")
    state_plan.add_argument("--state", type=Path)
    state_plan.add_argument("--all-tools", action="store_true")

    show_state = subparsers.add_parser("state-path")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    catalog = load_catalog()

    if args.command == "resolve-selection":
        selection = resolve_selection(
            catalog,
            profile=args.profile,
            toolbox_ids=args.toolboxes,
            tool_ids=args.tools,
            mode=args.mode,
        )
        write_json(args.output, selection)
        return 0

    if args.command == "interactive-select":
        selection = interactive_select(catalog)
        write_json(args.output, selection)
        return 0

    if args.command == "print-summary":
        print(emit_summary(catalog, read_selection(args.selection)))
        return 0

    if args.command == "export-plan":
        print(export_plan(catalog, read_selection(args.selection)))
        return 0

    if args.command == "export-verify-table":
        print(export_verify_table(catalog, read_selection(args.selection)))
        return 0

    if args.command == "save-state":
        payload = save_state(catalog, read_selection(args.selection), args.env)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "export-state-plan":
        try:
            selection = verify_selection_from_state(
                catalog,
                load_state(catalog, args.state),
                args.all_tools,
            )
        except SystemExit:
            if not args.all_tools:
                selection = {
                    "mode": "default-headless",
                    "profile": "headless",
                    "toolboxes": [entry["id"] for entry in catalog.raw["toolboxes"]],
                    "tool_ids": sorted(
                        tool_id
                        for tool_id, tool in catalog.tools.items()
                        if "headless" in tool["profiles"]
                    ),
                }
            else:
                raise
        print(export_plan(catalog, selection))
        return 0

    if args.command == "state-path":
        print(state_path(catalog))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
