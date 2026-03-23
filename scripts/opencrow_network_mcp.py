#!/usr/bin/env python3
"""OpenCROW network toolbox MCP server."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from opencrow_ctf_mcp_common import run_conda_python
from opencrow_mcp_core import (
    MCPTool,
    StdioMCPServer,
    command_exists,
    conda_module_available,
    decode_output,
    default_execution,
    error_envelope,
    make_toolbox_capabilities_handler,
    make_toolbox_info_handler,
    make_toolbox_self_test_handler,
    missing_dependency_envelope,
    run_command,
    success_envelope,
    summarize_command,
)


SERVER_NAME = "opencrow-network-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "opencrow-network-toolbox"
DISPLAY_NAME = "OpenCROW Network Toolbox"
OPERATIONS = [
    {"name": "network_python", "description": "Run typed inline Python or a Python file inside the managed ctf environment."},
    {"name": "network_pcap_inspect", "description": "Inspect a PCAP with tshark or tcpdump using typed filter options."},
    {"name": "network_scan", "description": "Run a typed nmap scan against a host or range."},
    {"name": "network_socket_probe", "description": "Probe a TCP or UDP service with nc or socat using typed connection parameters."},
]
PYTHON_MODULES = ["scapy"]
SYSTEM_DEPENDENCIES = ["tshark", "tcpdump", "nmap", "nc", "socat"]


def _env_name(arguments: dict[str, object]) -> str:
    return str(arguments.get("env_name", "ctf"))


def _path_error(operation: str, path: Path, inputs: dict[str, object]) -> dict[str, object]:
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation=operation,
        summary=f"Input file does not exist: {path}",
        inputs=inputs,
        stderr=f"Missing file: {path}",
        exit_code=2,
    )


def toolbox_verify(arguments: dict[str, object]) -> dict[str, object]:
    env_name = _env_name(arguments)
    observations = [
        {"dependency": module, "available": conda_module_available(env_name, module), "type": "python-module", "env_name": env_name}
        for module in PYTHON_MODULES
    ]
    observations.extend(
        {"dependency": dependency, "available": command_exists(dependency), "type": "system-command"}
        for dependency in SYSTEM_DEPENDENCIES
    )
    observations.append({"dependency": "conda", "available": command_exists("conda"), "type": "system-command"})
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="Network toolbox dependency status returned.",
        inputs={"env_name": env_name},
        observations=observations,
        next_steps=[
            "Use `network_pcap_inspect` for captures and `network_scan` for service mapping.",
            "Use `network_socket_probe` for quick socket-level validation before escalating to persistent async I/O.",
        ],
    )


def network_python(arguments: dict[str, object]) -> dict[str, object]:
    env_name = _env_name(arguments)
    code = arguments.get("code")
    path_value = arguments.get("path")
    path_text = str(path_value).strip() if path_value is not None else None
    inputs = {"env_name": env_name, "path": path_text, "has_code": code is not None}
    if (code is None) == (path_value is None):
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="network_python",
            summary="Pass exactly one of `code` or `path`.",
            inputs=inputs,
            stderr="Use `code` for inline execution or `path` for a Python file.",
            exit_code=2,
        )

    cwd, timeout_sec = default_execution(arguments)
    artifacts: list[str] = []
    if path_value is not None:
        if not path_text:
            return error_envelope(
                toolbox=TOOLBOX_ID,
                operation="network_python",
                summary="A non-empty file path is required.",
                inputs=inputs,
                stderr="Pass a non-empty `path` or use `code`.",
                exit_code=2,
            )
        path = Path(path_text).expanduser().resolve()
        if not path.exists():
            return _path_error("network_python", path, inputs)
        artifacts.append(str(path))
        result = run_conda_python(env_name=env_name, path=path, cwd=cwd, timeout_sec=timeout_sec, prefix="opencrow-network-")
    else:
        result = run_conda_python(env_name=env_name, code=str(code), cwd=cwd, timeout_sec=timeout_sec, prefix="opencrow-network-")

    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="network_python",
            summary="Network Python execution completed.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[{"env_name": env_name}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="network_python",
        summary="Network Python execution failed.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def network_pcap_inspect(arguments: dict[str, object]) -> dict[str, object]:
    backend = str(arguments.get("backend", "tshark"))
    raw_path = str(arguments.get("path", "")).strip()
    display_filter = str(arguments.get("display_filter", "")).strip()
    count = arguments.get("count")
    inputs = {
        "backend": backend,
        "path": raw_path,
        "display_filter": display_filter or None,
        "count": int(count) if count is not None else None,
    }
    if not raw_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="network_pcap_inspect",
            summary="A PCAP path is required.",
            inputs=inputs,
            stderr="Pass `path`.",
            exit_code=2,
        )
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        return _path_error("network_pcap_inspect", path, inputs)

    cwd, timeout_sec = default_execution(arguments)
    if backend == "tshark":
        if not command_exists("tshark"):
            return missing_dependency_envelope(TOOLBOX_ID, "network_pcap_inspect", "tshark", inputs)
        command = ["tshark", "-r", str(path)]
        if display_filter:
            command.extend(["-Y", display_filter])
        if count is not None:
            command.extend(["-c", str(int(count))])
    elif backend == "tcpdump":
        if not command_exists("tcpdump"):
            return missing_dependency_envelope(TOOLBOX_ID, "network_pcap_inspect", "tcpdump", inputs)
        command = ["tcpdump", "-nn", "-r", str(path)]
        if count is not None:
            command.extend(["-c", str(int(count))])
        if display_filter:
            command.append(display_filter)
    else:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="network_pcap_inspect",
            summary=f"Unsupported PCAP backend: {backend}",
            inputs=inputs,
            stderr="Supported backends are `tshark` and `tcpdump`.",
            exit_code=2,
        )

    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="network_pcap_inspect",
        summary=f"PCAP inspection {'completed' if result['ok'] else 'failed'} with {backend}.",
        inputs=inputs,
        artifacts=[str(path)],
        observations=[{"backend": backend}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        next_steps=["Escalate to `network_python` with scapy when packet structure or payload parsing needs custom logic."] if result["ok"] else [],
    )


def network_scan(arguments: dict[str, object]) -> dict[str, object]:
    target = str(arguments.get("target", "")).strip()
    ports = arguments.get("ports")
    service_version = bool(arguments.get("service_version", False))
    udp = bool(arguments.get("udp", False))
    scripts = [str(item) for item in arguments.get("scripts", [])]
    inputs = {
        "target": target,
        "ports": str(ports) if ports is not None else None,
        "service_version": service_version,
        "udp": udp,
        "scripts": scripts,
    }
    if not target:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="network_scan",
            summary="A scan target is required.",
            inputs=inputs,
            stderr="Pass `target`.",
            exit_code=2,
        )
    if not command_exists("nmap"):
        return missing_dependency_envelope(TOOLBOX_ID, "network_scan", "nmap", inputs)

    cwd, timeout_sec = default_execution(arguments)
    command = ["nmap", "-Pn"]
    if service_version:
        command.append("-sV")
    if udp:
        command.append("-sU")
    if ports is not None:
        command.extend(["-p", str(ports)])
    if scripts:
        command.extend(["--script", ",".join(scripts)])
    command.append(target)
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="network_scan",
        summary=f"nmap scan {'completed' if result['ok'] else 'failed'} for {target}.",
        inputs=inputs,
        observations=[{"tool": "nmap"}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def _probe_command(
    *,
    backend: str,
    host: str,
    port: int,
    udp: bool,
    read_timeout_sec: int,
    message: str | None,
) -> list[str]:
    if backend == "nc":
        if message is None:
            command = ["nc", "-vz", "-w", str(read_timeout_sec)]
            if udp:
                command.append("-u")
            command.extend([host, str(port)])
            return command
        command = ["nc", "-w", str(read_timeout_sec)]
        if udp:
            command.append("-u")
        command.extend([host, str(port)])
        return command

    destination = f"{'UDP' if udp else 'TCP'}:{host}:{port}"
    return ["socat", f"-T{read_timeout_sec}", "-", destination]


def _run_probe(command: list[str], *, cwd: str | None, timeout_sec: int, message: str | None) -> dict[str, object]:
    data = None if message is None else message.encode("utf-8")
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            input=data,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": 127,
            "command": summarize_command(command),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "stdout": decode_output(exc.stdout),
            "stderr": decode_output(exc.stderr) or f"Timed out after {timeout_sec} seconds.",
            "exit_code": 124,
            "command": summarize_command(command),
        }
    return {
        "ok": completed.returncode == 0,
        "stdout": decode_output(completed.stdout),
        "stderr": decode_output(completed.stderr),
        "exit_code": completed.returncode,
        "command": summarize_command(command),
    }


def network_socket_probe(arguments: dict[str, object]) -> dict[str, object]:
    backend = str(arguments.get("backend", "nc"))
    host = str(arguments.get("host", "")).strip()
    port = arguments.get("port")
    udp = bool(arguments.get("udp", False))
    message = arguments.get("message")
    read_timeout_sec = int(arguments.get("read_timeout_sec", 5))
    inputs = {
        "backend": backend,
        "host": host,
        "port": int(port) if port is not None else None,
        "udp": udp,
        "message": message,
        "read_timeout_sec": read_timeout_sec,
    }
    if not host:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="network_socket_probe",
            summary="A host is required.",
            inputs=inputs,
            stderr="Pass `host`.",
            exit_code=2,
        )
    if port is None:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="network_socket_probe",
            summary="A port is required.",
            inputs=inputs,
            stderr="Pass `port`.",
            exit_code=2,
        )
    dependency = "nc" if backend == "nc" else "socat" if backend == "socat" else backend
    if backend not in {"nc", "socat"}:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="network_socket_probe",
            summary=f"Unsupported socket probe backend: {backend}",
            inputs=inputs,
            stderr="Supported backends are `nc` and `socat`.",
            exit_code=2,
        )
    if not command_exists(dependency):
        return missing_dependency_envelope(TOOLBOX_ID, "network_socket_probe", dependency, inputs)

    cwd, timeout_sec = default_execution(arguments)
    command = _probe_command(
        backend=backend,
        host=host,
        port=int(port),
        udp=udp,
        read_timeout_sec=read_timeout_sec,
        message=str(message) if message is not None else None,
    )
    result = _run_probe(command, cwd=cwd, timeout_sec=timeout_sec, message=str(message) if message is not None else None)
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="network_socket_probe",
        summary=f"Socket probe {'completed' if result['ok'] else 'failed'} with {backend}.",
        inputs=inputs,
        observations=[{"backend": backend, "udp": udp, "sent_message": message is not None}],
        command=result["command"],
        stdout=str(result["stdout"]),
        stderr=str(result["stderr"]),
        exit_code=int(result["exit_code"]) if result["exit_code"] is not None else None,
        next_steps=["Use the async I/O MCP helpers when the service requires a persistent session."] if result["ok"] else [],
    )


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        instructions="OpenCROW network toolbox MCP server. Use typed network and capture tools instead of raw shell commands.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return metadata about the OpenCROW network toolbox MCP server.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="OpenCROW network toolbox information returned.",
                    operations=OPERATIONS,
                ),
            ),
            MCPTool(
                name="toolbox_self_test",
                description="Run a lightweight self-test for this OpenCROW MCP server.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
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
                description="Return dependency status for the OpenCROW network toolbox MCP server.",
                input_schema={"type": "object", "properties": {"env_name": {"type": "string"}}, "additionalProperties": False},
                handler=toolbox_verify,
            ),
            MCPTool(
                name="toolbox_capabilities",
                description="Return the structured operations exposed by the OpenCROW network toolbox MCP server.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=make_toolbox_capabilities_handler(TOOLBOX_ID, OPERATIONS),
            ),
            MCPTool(
                name="network_python",
                description="Run typed inline Python or a Python file inside the managed ctf environment.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "env_name": {"type": "string"},
                        "code": {"type": "string"},
                        "path": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
                handler=network_python,
            ),
            MCPTool(
                name="network_pcap_inspect",
                description="Inspect a PCAP with tshark or tcpdump using typed filter options.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "backend": {"type": "string", "enum": ["tshark", "tcpdump"]},
                        "path": {"type": "string"},
                        "display_filter": {"type": "string"},
                        "count": {"type": "integer"},
                        "execution": {"type": "object"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=network_pcap_inspect,
            ),
            MCPTool(
                name="network_scan",
                description="Run a typed nmap scan against a host or range.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                        "ports": {"type": ["string", "integer"]},
                        "service_version": {"type": "boolean"},
                        "udp": {"type": "boolean"},
                        "scripts": {"type": "array", "items": {"type": "string"}},
                        "execution": {"type": "object"},
                    },
                    "required": ["target"],
                    "additionalProperties": False,
                },
                handler=network_scan,
            ),
            MCPTool(
                name="network_socket_probe",
                description="Probe a TCP or UDP service with nc or socat using typed connection parameters.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "backend": {"type": "string", "enum": ["nc", "socat"]},
                        "host": {"type": "string"},
                        "port": {"type": "integer"},
                        "udp": {"type": "boolean"},
                        "message": {"type": "string"},
                        "read_timeout_sec": {"type": "integer"},
                        "execution": {"type": "object"},
                    },
                    "required": ["host", "port"],
                    "additionalProperties": False,
                },
                handler=network_socket_probe,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
