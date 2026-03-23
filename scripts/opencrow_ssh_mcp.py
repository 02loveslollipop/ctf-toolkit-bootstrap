#!/usr/bin/env python3
"""OpenCROW SSH async MCP server."""

from __future__ import annotations

import sys
from pathlib import Path

from opencrow_io_mcp_common import normalize_session_name, parse_json_stdout, run_backend_script, session_artifact_paths
from opencrow_mcp_core import (
    MCPTool,
    MCPResourceTemplate,
    StdioMCPServer,
    command_exists,
    default_execution,
    error_envelope,
    json_resource_contents,
    make_toolbox_capabilities_handler,
    make_toolbox_info_handler,
    make_toolbox_self_test_handler,
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


def _invalid_session_name(operation: str, inputs: dict[str, object], exc: ValueError) -> dict[str, object]:
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation=operation,
        summary="Invalid session name.",
        inputs=inputs,
        stderr=str(exc),
        exit_code=2,
    )


def _session_artifact_snapshot(name: str) -> list[dict[str, object]]:
    return [
        {"path": path, "exists": Path(path).exists()}
        for path in session_artifact_paths(SESSION_BASE_DIR, name)
    ]


def _read_session_status_resource(uri: str, params: dict[str, str]) -> list[dict[str, object]]:
    name = normalize_session_name(params.get("name", ""))
    result, payload = _run_status(name, None, 30)
    return json_resource_contents(
        uri,
        {
            "name": name,
            "backend_script": BACKEND_SCRIPT,
            "base_dir": SESSION_BASE_DIR,
            "ok": result["ok"],
            "status": payload,
            "stderr": result["stderr"],
            "exit_code": result["exit_code"],
        },
    )


def _read_session_artifacts_resource(uri: str, params: dict[str, str]) -> list[dict[str, object]]:
    name = normalize_session_name(params.get("name", ""))
    return json_resource_contents(
        uri,
        {
            "name": name,
            "base_dir": SESSION_BASE_DIR,
            "artifacts": _session_artifact_snapshot(name),
        },
    )


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
    raw_name = arguments.get("name", "")
    host = str(arguments.get("host", "")).strip()
    user = arguments.get("user")
    port = int(arguments.get("port", 22))
    identity = arguments.get("identity")
    options = arguments.get("options") if isinstance(arguments.get("options"), list) else []
    remote_command = arguments.get("remote_command")
    ssh_bin = str(arguments.get("ssh_bin", "ssh"))
    inputs = {
        "name": str(raw_name).strip(),
        "host": host,
        "user": user,
        "port": port,
        "identity": identity,
        "options": options,
        "remote_command": remote_command,
        "ssh_bin": ssh_bin,
    }
    if not host:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="session_start",
            summary="Session name and host are required.",
            inputs=inputs,
            stderr="Pass `name` and `host`.",
            exit_code=2,
        )
    try:
        name = normalize_session_name(raw_name)
    except ValueError as exc:
        return _invalid_session_name("session_start", inputs, exc)
    inputs["name"] = name

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
    raw_name = arguments.get("name", "")
    data = str(arguments.get("data", ""))
    newline = bool(arguments.get("newline", False))
    timeout = float(arguments.get("timeout", 3.0))
    inputs = {"name": str(raw_name).strip(), "data": data, "newline": newline, "timeout": timeout}
    try:
        name = normalize_session_name(raw_name)
    except ValueError as exc:
        return _invalid_session_name("session_send", inputs, exc)
    inputs["name"] = name

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
    raw_name = arguments.get("name", "")
    tail = arguments.get("tail")
    follow = bool(arguments.get("follow", False))
    inputs = {"name": str(raw_name).strip(), "tail": tail, "follow": follow}
    try:
        name = normalize_session_name(raw_name)
    except ValueError as exc:
        return _invalid_session_name("session_read", inputs, exc)
    inputs["name"] = name

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
    raw_name = arguments.get("name", "")
    inputs = {"name": str(raw_name).strip()}
    try:
        name = normalize_session_name(raw_name)
    except ValueError as exc:
        return _invalid_session_name("session_status", inputs, exc)
    inputs["name"] = name

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
    raw_name = arguments.get("name", "")
    timeout = float(arguments.get("timeout", 5.0))
    inputs = {"name": str(raw_name).strip(), "timeout": timeout}
    try:
        name = normalize_session_name(raw_name)
    except ValueError as exc:
        return _invalid_session_name("session_stop", inputs, exc)
    inputs["name"] = name

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
    server.register_resource_templates(
        [
            MCPResourceTemplate(
                uri_template=f"opencrow://{SERVER_NAME}/sessions/{{name}}/status",
                name="SSH session status",
                description="Read status metadata for a named asynchronous SSH session.",
                mime_type="application/json",
                handler=_read_session_status_resource,
            ),
            MCPResourceTemplate(
                uri_template=f"opencrow://{SERVER_NAME}/sessions/{{name}}/artifacts",
                name="SSH session artifacts",
                description="Read the expected artifact paths and existence state for a named asynchronous SSH session.",
                mime_type="application/json",
                handler=_read_session_artifacts_resource,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
