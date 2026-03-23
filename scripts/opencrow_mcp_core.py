#!/usr/bin/env python3
"""Shared stdio MCP helpers for OpenCROW toolbox servers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


JSON = dict[str, Any]
Handler = Callable[[JSON], JSON]
SUPPORTED_PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)
DEFAULT_PROTOCOL_VERSION = "2024-11-05"


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str
    input_schema: JSON
    handler: Handler


def normalize_path(value: str | Path | None) -> str | None:
    if value is None:
        return None
    return str(Path(value).expanduser().resolve())


def summarize_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def decode_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def run_command(
    command: list[str],
    *,
    cwd: str | Path | None = None,
    timeout_sec: int = 120,
    env: dict[str, str] | None = None,
) -> JSON:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            capture_output=True,
            check=False,
            timeout=timeout_sec,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "stdout": "",
            "stdout_bytes": b"",
            "stderr": str(exc),
            "stderr_bytes": b"",
            "exit_code": 127,
            "command": summarize_command(command),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "stdout": decode_output(exc.stdout),
            "stdout_bytes": exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout.encode("utf-8") if exc.stdout else b""),
            "stderr": decode_output(exc.stderr) or f"Timed out after {timeout_sec} seconds.",
            "stderr_bytes": exc.stderr if isinstance(exc.stderr, bytes) else (exc.stderr.encode("utf-8") if exc.stderr else b""),
            "exit_code": 124,
            "command": summarize_command(command),
        }

    return {
        "ok": completed.returncode == 0,
        "stdout": decode_output(completed.stdout),
        "stdout_bytes": completed.stdout,
        "stderr": decode_output(completed.stderr),
        "stderr_bytes": completed.stderr,
        "exit_code": completed.returncode,
        "command": summarize_command(command),
    }


def success_envelope(
    *,
    toolbox: str,
    operation: str,
    summary: str,
    inputs: JSON,
    artifacts: list[str] | None = None,
    observations: list[JSON] | None = None,
    command: str | None = None,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    next_steps: list[str] | None = None,
) -> JSON:
    return {
        "ok": True,
        "summary": summary,
        "toolbox": toolbox,
        "operation": operation,
        "inputs": inputs,
        "artifacts": artifacts or [],
        "observations": observations or [],
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "next_steps": next_steps or [],
    }


def error_envelope(
    *,
    toolbox: str,
    operation: str,
    summary: str,
    inputs: JSON,
    command: str | None = None,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    observations: list[JSON] | None = None,
    next_steps: list[str] | None = None,
) -> JSON:
    return {
        "ok": False,
        "summary": summary,
        "toolbox": toolbox,
        "operation": operation,
        "inputs": inputs,
        "artifacts": [],
        "observations": observations or [],
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "next_steps": next_steps or [],
    }


def missing_dependency_envelope(toolbox: str, operation: str, dependency: str, inputs: JSON) -> JSON:
    return error_envelope(
        toolbox=toolbox,
        operation=operation,
        summary=f"Required dependency is not available: {dependency}",
        inputs=inputs,
        stderr=f"Dependency not found: {dependency}",
        exit_code=127,
        next_steps=[f"Install or expose `{dependency}` before retrying `{operation}`."],
    )


def serialize_tool_result(envelope: JSON) -> JSON:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(envelope, indent=2, sort_keys=True),
            }
        ],
        "isError": not envelope.get("ok", False),
    }


def make_toolbox_info_handler(
    *,
    toolbox: str,
    display_name: str,
    server_name: str,
    server_version: str,
    summary: str,
    operations: list[JSON],
) -> Handler:
    def handler(arguments: JSON) -> JSON:
        return success_envelope(
            toolbox=toolbox,
            operation="toolbox_info",
            summary=summary,
            inputs=arguments,
            observations=[
                {
                    "display_name": display_name,
                    "server_name": server_name,
                    "server_version": server_version,
                    "transport": "stdio",
                    "protocol_baseline": DEFAULT_PROTOCOL_VERSION,
                },
                {
                    "operations": operations,
                },
            ],
            next_steps=["Call `toolbox_capabilities` to inspect the structured operations this server exposes."],
        )

    return handler


def make_toolbox_self_test_handler(
    *,
    toolbox: str,
    display_name: str,
    server_name: str,
    server_version: str,
    operations: list[JSON],
) -> Handler:
    def handler(arguments: JSON) -> JSON:
        return success_envelope(
            toolbox=toolbox,
            operation="toolbox_self_test",
            summary=f"{display_name} self-test passed.",
            inputs=arguments,
            observations=[
                {
                    "status": "ready",
                    "display_name": display_name,
                    "server_name": server_name,
                    "server_version": server_version,
                    "transport": "stdio",
                    "protocol_baseline": DEFAULT_PROTOCOL_VERSION,
                    "operation_count": len(operations),
                    "registered_tool_count": len(operations) + 4,
                }
            ],
            next_steps=[
                "Call `toolbox_capabilities` to inspect the structured operations this server exposes.",
                "Call `toolbox_verify` when you need dependency status for the current environment.",
            ],
        )

    return handler


def make_toolbox_capabilities_handler(toolbox: str, operations: list[JSON]) -> Handler:
    def handler(arguments: JSON) -> JSON:
        return success_envelope(
            toolbox=toolbox,
            operation="toolbox_capabilities",
            summary=f"{toolbox} capabilities returned.",
            inputs=arguments,
            observations=operations,
        )

    return handler


class StdioMCPServer:
    def __init__(self, *, server_name: str, server_version: str, instructions: str | None = None) -> None:
        self.server_name = server_name
        self.server_version = server_version
        self.instructions = instructions
        self.tools: dict[str, MCPTool] = {}

    def register_tool(self, tool: MCPTool) -> None:
        self.tools[tool.name] = tool

    def register_tools(self, tools: list[MCPTool]) -> None:
        for tool in tools:
            self.register_tool(tool)

    def _tool_descriptors(self) -> list[JSON]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in self.tools.values()
        ]

    def serve(self) -> int:
        stdin = sys.stdin.buffer
        stdout = sys.stdout.buffer

        while True:
            request = self._read_message(stdin)
            if request is None:
                return 0
            response = self._handle_message(request)
            if response is None:
                continue
            self._write_message(stdout, response)

    def _handle_message(self, request: JSON) -> JSON | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params", {})

        if method == "notifications/initialized":
            return None

        if method == "initialize":
            client_version = str(params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION)
            protocol_version = (
                client_version
                if client_version in SUPPORTED_PROTOCOL_VERSIONS
                else DEFAULT_PROTOCOL_VERSION
            )
            return self._result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {
                        "tools": {},
                    },
                    "serverInfo": {
                        "name": self.server_name,
                        "version": self.server_version,
                    },
                    "instructions": self.instructions or "",
                },
            )

        if method == "ping":
            return self._result(request_id, {})

        if method == "tools/list":
            return self._result(request_id, {"tools": self._tool_descriptors()})

        if method == "tools/call":
            tool_name = params.get("name")
            if tool_name not in self.tools:
                return self._error(request_id, -32602, f"Unknown tool: {tool_name}")
            arguments = params.get("arguments") or {}
            try:
                envelope = self.tools[tool_name].handler(arguments)
            except Exception as exc:  # pragma: no cover - defensive server path
                envelope = error_envelope(
                    toolbox=self.server_name,
                    operation=str(tool_name),
                    summary=f"Unhandled exception while running {tool_name}",
                    inputs=arguments if isinstance(arguments, dict) else {"arguments": arguments},
                    stderr=f"{exc}\n{traceback.format_exc()}",
                )
            return self._result(request_id, serialize_tool_result(envelope))

        return self._error(request_id, -32601, f"Method not found: {method}")

    @staticmethod
    def _result(request_id: Any, result: JSON) -> JSON:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> JSON:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    @staticmethod
    def _read_message(stream: Any) -> JSON | None:
        headers: dict[str, str] = {}

        while True:
            line = stream.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            decoded = line.decode("utf-8").strip()
            if not decoded:
                break
            name, value = decoded.split(":", 1)
            headers[name.lower()] = value.strip()

        content_length = int(headers.get("content-length", "0"))
        if content_length <= 0:
            return None

        body = stream.read(content_length)
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    @staticmethod
    def _write_message(stream: Any, payload: JSON) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\nContent-Type: application/json\r\n\r\n".encode("utf-8")
        stream.write(header)
        stream.write(body)
        stream.flush()


def command_exists(name: str) -> bool:
    from shutil import which

    return which(name) is not None


def conda_module_available(env_name: str, module_name: str) -> bool:
    code = f"import importlib.util; raise SystemExit(0 if importlib.util.find_spec('{module_name}') else 1)"
    try:
        result = subprocess.run(
            ["conda", "run", "-n", env_name, "python", "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def default_execution(arguments: JSON) -> tuple[str | None, int]:
    execution = arguments.get("execution") if isinstance(arguments.get("execution"), dict) else {}
    cwd = normalize_path(execution.get("cwd")) if execution else None
    timeout_sec = int(execution.get("timeout_sec", 120)) if execution else 120
    return cwd, timeout_sec


def merge_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if extra:
        env.update(extra)
    return env
