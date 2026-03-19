#!/usr/bin/env python3
"""Verify OpenCROW utility tools."""

from __future__ import annotations

import json
import shutil


SYSTEM_TOOLS = [
    "jq",
    "yq",
    "xxd",
    "tmux",
    "screen",
    "rg",
    "fzf",
]


def main() -> int:
    payload = {
        "system_tools": {tool: shutil.which(tool) is not None for tool in SYSTEM_TOOLS}
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
