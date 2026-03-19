#!/usr/bin/env python3
"""Verify native tools for the OpenCROW web toolbox."""

from __future__ import annotations

import json
import shutil


SYSTEM_TOOLS = [
    "sqlmap",
    "gobuster",
    "ffuf",
    "dirb",
    "wfuzz",
]


def main() -> int:
    payload = {
        "system_tools": {tool: shutil.which(tool) is not None for tool in SYSTEM_TOOLS}
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
