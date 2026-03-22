#!/usr/bin/env python3
"""OpenCROW netcat async MCP server."""

from __future__ import annotations

import sys
from pathlib import Path

from opencrow_io_mcp_common import parse_json_stdout, run_backend_script, session_artifact_paths
from opencrow_mcp_core import (
    MCPTool,
    StdioMCPServer,
    default_execution,
    error_envelope,
    make_toolbox_capabilities_handler,
    make_toolbox_info_handler,
    make_toolbox_self_test_handler,
    success_envelope,
)


SERVER_NAME = "opencrow-netcat-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "netcat-async"
DISPLAY_NAME = "OpenCROW I/O - Netcat Async"
BACKEND_SCRIPT = "nc_async_session.py"
SESSION_BASE_DIR = "/tmp/codex-nc-async"
OPERATIONS = [
    {"name": "session_start", "description": "Start a named asynchronous TCP session."},
    {"name": "session_send", "description": "Send text to a running asynchronous TCP session."},
    {"name": "session_read", "description": "Read or follow the captured log for a TCP session."},
    {"name": "session_status", "description": "Return the structured status for a TCP session."},
    {"name": "session_stop", "description": "Stop a running asynchronous TCP session."},
]


def _run_status(name: str, cwd: str | Path | None, timeout_sec: int) -> tuple[dict[str, object], dict[str, object] | None]:
    result = run_backend_script(BACKEND_SCRIPT, ["status", "--name", name], cwd=cwd, timeout_sec=timeout_sec)
    return result, parse_json_stdout(result)


def toolbox_verify(arguments: dict[str, object]) -> dict[str, object]:
    observations = [
        {"dependency": "python_socket", "available": True},
        {"dependency": "session_base_dir", "path": SESSION_BASE_DIR},
        {"dependency": "backend_script", "path": BACKEND_SCRIPT},
    ]
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="Netcat async MCP server is available.",
        inputs=arguments,
        observations=observations,
        next_steps=["Use `session_start` to open a named TCP session."],
    )


def session_start(arguments: dict[str, object]) -> dict[str, object]:
    name = str(arguments.get("name", "")).strip()
    host = str(arguments.get("host", "")).strip()
    port = arguments.get("port")
    connect_timeout = float(arguments.get("connect_timeout", 10.0))
    inputs = {
        "name": name,
        "host": host,
        "port": port,
        "connect_timeout": connect_timeout,
    }
    if not name or not host or port is None:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_start",
            summary="Session name, host, and port are required.",
            inputs=inputs,
            stderr="Pass `name`, `host`, and `port`.",
            exit_code=2,
        )

    cwd, timeout_sec = default_execution(arguments)
    result = run_backend_script(
        BACKEND_SCRIPT,
        [
            "start",
            "--name",
            name,
            "--host",
            host,
            "--port",
            str(port),
            "--connect-timeout",
            str(connect_timeout),
        ],
        cwd=cwd,
        timeout_sec=timeout_sec,
    )
    status_result, status_payload = _run_status(name, cwd, timeout_sec)
    artifacts = session_artifact_paths(SESSION_BASE_DIR, name)
    observations = [status_payload] if isinstance(status_payload, dict) else []
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_start",
            summary=f"Netcat session '{name}' started.",
            inputs=inputs,
            artifacts=artifacts,
            observations=observations,
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"] if result["stderr"] else status_result.get("stderr", ""),
            exit_code=result["exit_code"],
            next_steps=["Use `session_send` and `session_read` against the same `name`."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="session_start",
        summary=f"Failed to start netcat session '{name}'.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def session_send(arguments: dict[str, object]) -> dict[str, object]:
    name = str(arguments.get("name", "")).strip()
    data = str(arguments.get("data", ""))
    newline = bool(arguments.get("newline", False))
    timeout = float(arguments.get("timeout", 2.0))
    inputs = {"name": name, "data": data, "newline": newline, "timeout": timeout}
    if not name:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_send",
            summary="Session name is required.",
            inputs=inputs,
            stderr="Pass `name`.",
            exit_code=2,
        )

    cwd, timeout_sec = default_execution(arguments)
    command = ["send", "--name", name, "--data", data, "--timeout", str(timeout)]
    if newline:
        command.append("--newline")
    result = run_backend_script(BACKEND_SCRIPT, command, cwd=cwd, timeout_sec=timeout_sec)
    artifacts = session_artifact_paths(SESSION_BASE_DIR, name)
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_send",
            summary=f"Sent data to netcat session '{name}'.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[{"name": name, "newline": newline, "payload_length": len(data)}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Use `session_read` to inspect the remote response."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="session_send",
        summary=f"Failed to send data to netcat session '{name}'.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def session_read(arguments: dict[str, object]) -> dict[str, object]:
    name = str(arguments.get("name", "")).strip()
    tail = arguments.get("tail")
    follow = bool(arguments.get("follow", False))
    inputs = {"name": name, "tail": tail, "follow": follow}
    if not name:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_read",
            summary="Session name is required.",
            inputs=inputs,
            stderr="Pass `name`.",
            exit_code=2,
        )

    cwd, timeout_sec = default_execution(arguments)
    command = ["read", "--name", name]
    if tail is not None:
        command.extend(["--tail", str(int(tail))])
    if follow:
        command.append("--follow")
    result = run_backend_script(BACKEND_SCRIPT, command, cwd=cwd, timeout_sec=timeout_sec)
    artifacts = session_artifact_paths(SESSION_BASE_DIR, name)
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_read",
            summary=f"Read output for netcat session '{name}'.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[{"name": name, "follow": follow, "tail": tail}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="session_read",
        summary=f"Failed to read output for netcat session '{name}'.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def session_status(arguments: dict[str, object]) -> dict[str, object]:
    name = str(arguments.get("name", "")).strip()
    inputs = {"name": name}
    if not name:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_status",
            summary="Session name is required.",
            inputs=inputs,
            stderr="Pass `name`.",
            exit_code=2,
        )

    cwd, timeout_sec = default_execution(arguments)
    result, payload = _run_status(name, cwd, timeout_sec)
    if result["ok"] and isinstance(payload, dict):
        artifacts = list(payload.get("paths", {}).values()) if isinstance(payload.get("paths"), dict) else session_artifact_paths(SESSION_BASE_DIR, name)
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_status",
            summary=f"Status returned for netcat session '{name}'.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[payload],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="session_status",
        summary=f"Failed to load status for netcat session '{name}'.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def session_stop(arguments: dict[str, object]) -> dict[str, object]:
    name = str(arguments.get("name", "")).strip()
    timeout = float(arguments.get("timeout", 3.0))
    inputs = {"name": name, "timeout": timeout}
    if not name:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_stop",
            summary="Session name is required.",
            inputs=inputs,
            stderr="Pass `name`.",
            exit_code=2,
        )

    cwd, timeout_sec = default_execution(arguments)
    result = run_backend_script(
        BACKEND_SCRIPT,
        ["stop", "--name", name, "--timeout", str(timeout)],
        cwd=cwd,
        timeout_sec=timeout_sec,
    )
    artifacts = session_artifact_paths(SESSION_BASE_DIR, name)
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_stop",
            summary=f"Stopped netcat session '{name}'.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[{"name": name}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="session_stop",
        summary=f"Failed to stop netcat session '{name}'.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        instructions="OpenCROW async netcat I/O server.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return metadata about the OpenCROW async netcat I/O server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="OpenCROW async netcat I/O server information returned.",
                    operations=OPERATIONS,
                ),
            ),
            MCPTool(
                name="toolbox_self_test",
                description="Run a lightweight self-test for this OpenCROW MCP server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_self_test_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    operations=OPERATIONS,
                ),
            ),
            MCPTool(
                name="toolbox_verify",
                description="Return dependency status for the OpenCROW async netcat I/O server.",
                input_schema={"type": "object", "properties": {}},
                handler=toolbox_verify,
            ),
            MCPTool(
                name="toolbox_capabilities",
                description="Return the structured operations exposed by the OpenCROW async netcat I/O server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_capabilities_handler(TOOLBOX_ID, OPERATIONS),
            ),
            MCPTool(
                name="session_start",
                description="Start a named asynchronous TCP session.",
                input_schema={
                    "type": "object",
                    "required": ["name", "host", "port"],
                    "properties": {
                        "name": {"type": "string"},
                        "host": {"type": "string"},
                        "port": {"type": "integer"},
                        "connect_timeout": {"type": "number"},
                        "execution": {"type": "object"},
                    },
                },
                handler=session_start,
            ),
            MCPTool(
                name="session_send",
                description="Send text to a running asynchronous TCP session.",
                input_schema={
                    "type": "object",
                    "required": ["name", "data"],
                    "properties": {
                        "name": {"type": "string"},
                        "data": {"type": "string"},
                        "newline": {"type": "boolean"},
                        "timeout": {"type": "number"},
                        "execution": {"type": "object"},
                    },
                },
                handler=session_send,
            ),
            MCPTool(
                name="session_read",
                description="Read or follow a session log.",
                input_schema={
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                        "tail": {"type": "integer"},
                        "follow": {"type": "boolean"},
                        "execution": {"type": "object"},
                    },
                },
                handler=session_read,
            ),
            MCPTool(
                name="session_status",
                description="Return structured status for a named session.",
                input_schema={
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                },
                handler=session_status,
            ),
            MCPTool(
                name="session_stop",
                description="Stop a named asynchronous TCP session.",
                input_schema={
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                        "timeout": {"type": "number"},
                        "execution": {"type": "object"},
                    },
                },
                handler=session_stop,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
