#!/usr/bin/env python3
"""OpenCROW SSH async MCP server."""

from __future__ import annotations

import sys
from pathlib import Path

from opencrow_io_mcp_common import parse_json_stdout, run_backend_script, session_artifact_paths
from opencrow_mcp_core import (
    MCPTool,
    StdioMCPServer,
    command_exists,
    default_execution,
    error_envelope,
    make_toolbox_capabilities_handler,
    make_toolbox_info_handler,
    success_envelope,
)


SERVER_NAME = "opencrow-ssh-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "ssh-async"
DISPLAY_NAME = "OpenCROW I/O - SSH Async"
BACKEND_SCRIPT = "ssh_async_session.py"
SESSION_BASE_DIR = "/tmp/codex-ssh-async"
OPERATIONS = [
    {"name": "session_start", "description": "Start a named asynchronous SSH session."},
    {"name": "session_send", "description": "Send text to a running asynchronous SSH session."},
    {"name": "session_read", "description": "Read or follow the captured log for an SSH session."},
    {"name": "session_status", "description": "Return the structured status for an SSH session."},
    {"name": "session_stop", "description": "Stop a running asynchronous SSH session."},
]


def _run_status(name: str, cwd: str | Path | None, timeout_sec: int) -> tuple[dict[str, object], dict[str, object] | None]:
    result = run_backend_script(BACKEND_SCRIPT, ["status", "--name", name], cwd=cwd, timeout_sec=timeout_sec)
    return result, parse_json_stdout(result)


def toolbox_verify(arguments: dict[str, object]) -> dict[str, object]:
    observations = [
        {"dependency": "ssh", "available": command_exists("ssh")},
        {"dependency": "session_base_dir", "path": SESSION_BASE_DIR},
        {"dependency": "backend_script", "path": BACKEND_SCRIPT},
    ]
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="SSH async MCP server dependency status returned.",
        inputs=arguments,
        observations=observations,
        next_steps=["Use `session_start` to open a named SSH session once `ssh` is available."],
    )


def session_start(arguments: dict[str, object]) -> dict[str, object]:
    name = str(arguments.get("name", "")).strip()
    host = str(arguments.get("host", "")).strip()
    user = arguments.get("user")
    port = int(arguments.get("port", 22))
    identity = arguments.get("identity")
    options = arguments.get("options") if isinstance(arguments.get("options"), list) else []
    remote_command = arguments.get("remote_command")
    ssh_bin = str(arguments.get("ssh_bin", "ssh"))
    inputs = {
        "name": name,
        "host": host,
        "user": user,
        "port": port,
        "identity": identity,
        "options": options,
        "remote_command": remote_command,
        "ssh_bin": ssh_bin,
    }
    if not name or not host:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_start",
            summary="Session name and host are required.",
            inputs=inputs,
            stderr="Pass `name` and `host`.",
            exit_code=2,
        )

    cwd, timeout_sec = default_execution(arguments)
    command = ["start", "--name", name, "--host", host, "--port", str(port), "--ssh-bin", ssh_bin]
    if user:
        command.extend(["--user", str(user)])
    if identity:
        command.extend(["--identity", str(identity)])
    if remote_command:
        command.extend(["--remote-command", str(remote_command)])
    for option in options:
        command.extend(["--option", str(option)])
    result = run_backend_script(BACKEND_SCRIPT, command, cwd=cwd, timeout_sec=timeout_sec)
    status_result, status_payload = _run_status(name, cwd, timeout_sec)
    artifacts = session_artifact_paths(SESSION_BASE_DIR, name)
    observations = [status_payload] if isinstance(status_payload, dict) else []
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_start",
            summary=f"SSH session '{name}' started.",
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
        summary=f"Failed to start SSH session '{name}'.",
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
    timeout = float(arguments.get("timeout", 3.0))
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
            summary=f"Sent data to SSH session '{name}'.",
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
        summary=f"Failed to send data to SSH session '{name}'.",
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
            summary=f"Read output for SSH session '{name}'.",
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
        summary=f"Failed to read output for SSH session '{name}'.",
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
            summary=f"Status returned for SSH session '{name}'.",
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
        summary=f"Failed to load status for SSH session '{name}'.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def session_stop(arguments: dict[str, object]) -> dict[str, object]:
    name = str(arguments.get("name", "")).strip()
    timeout = float(arguments.get("timeout", 5.0))
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
            summary=f"Stopped SSH session '{name}'.",
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
        summary=f"Failed to stop SSH session '{name}'.",
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
        instructions="OpenCROW async SSH I/O server.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return metadata about the OpenCROW async SSH I/O server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="OpenCROW async SSH I/O server information returned.",
                    operations=OPERATIONS,
                ),
            ),
            MCPTool(
                name="toolbox_verify",
                description="Return dependency status for the OpenCROW async SSH I/O server.",
                input_schema={"type": "object", "properties": {}},
                handler=toolbox_verify,
            ),
            MCPTool(
                name="toolbox_capabilities",
                description="Return the structured operations exposed by the OpenCROW async SSH I/O server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_capabilities_handler(TOOLBOX_ID, OPERATIONS),
            ),
            MCPTool(
                name="session_start",
                description="Start a named asynchronous SSH session.",
                input_schema={
                    "type": "object",
                    "required": ["name", "host"],
                    "properties": {
                        "name": {"type": "string"},
                        "host": {"type": "string"},
                        "user": {"type": "string"},
                        "port": {"type": "integer"},
                        "identity": {"type": "string"},
                        "options": {"type": "array", "items": {"type": "string"}},
                        "remote_command": {"type": "string"},
                        "ssh_bin": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                },
                handler=session_start,
            ),
            MCPTool(
                name="session_send",
                description="Send text to a running asynchronous SSH session.",
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
                description="Stop a named asynchronous SSH session.",
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
