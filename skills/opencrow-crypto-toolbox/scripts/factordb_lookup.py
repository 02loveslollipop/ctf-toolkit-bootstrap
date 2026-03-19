#!/usr/bin/env python3
"""Query FactorDB for a candidate integer."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query the FactorDB API for an integer.")
    parser.add_argument("integer", help="Integer expression to query.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    query = urllib.parse.urlencode({"query": args.integer})
    url = f"http://factordb.com/api?{query}"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.load(response)
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
