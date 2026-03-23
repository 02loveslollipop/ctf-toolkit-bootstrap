#!/usr/bin/env python3
"""Basic stdio MCP handshake probe for OpenCROW servers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def write_message(stream: Any, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\nContent-Type: application/json\r\n\r\n".encode("utf-8")
    stream.write(header)
    stream.flush()
    stream.write(body)
    stream.flush()


def read_message(stream: Any) -> dict[str, Any]:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            raise RuntimeError("Unexpected EOF from MCP server.")
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("utf-8").strip()
        name, value = decoded.split(":", 1)
        headers[name.lower()] = value.strip()
    content_length = int(headers["content-length"])
    body = stream.read(content_length)
    return json.loads(body.decode("utf-8"))


def parse_tool_envelope(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Missing tool result payload: {response}")
    content = result.get("content")
    if not isinstance(content, list) or not content:
        raise RuntimeError(f"Missing tool content payload: {response}")
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "text":
        raise RuntimeError(f"Unexpected tool content payload: {response}")
    text = first.get("text")
    if not isinstance(text, str):
        raise RuntimeError(f"Missing tool text payload: {response}")
    return json.loads(text)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_mcp_server.py /absolute/path/to/server", file=sys.stderr)
        return 2

    server_path = Path(sys.argv[1]).expanduser().resolve()
    proc = subprocess.Popen(
        [str(server_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    try:
        write_message(
            proc.stdin,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "opencrow-check", "version": "0.1.0"},
                },
            },
        )
        init_result = read_message(proc.stdout)
        if init_result.get("result", {}).get("serverInfo", {}).get("name") is None:
            raise RuntimeError(f"Missing serverInfo in initialize response: {init_result}")

        write_message(proc.stdin, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        write_message(
            proc.stdin,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "toolbox_self_test", "arguments": {}},
            },
        )
        self_test_result = read_message(proc.stdout)
        envelope = parse_tool_envelope(self_test_result)
        if not envelope.get("ok"):
            raise RuntimeError(f"toolbox_self_test failed: {self_test_result}")
        if envelope.get("operation") != "toolbox_self_test":
            raise RuntimeError(f"Unexpected tool response: {self_test_result}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
