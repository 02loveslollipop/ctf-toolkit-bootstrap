#!/usr/bin/env python3
"""OpenCROW web toolbox MCP server."""

from __future__ import annotations

import sys
from pathlib import Path

from opencrow_mcp_core import (
    MCPTool,
    StdioMCPServer,
    command_exists,
    default_execution,
    error_envelope,
    make_toolbox_capabilities_handler,
    make_toolbox_info_handler,
    make_toolbox_self_test_handler,
    missing_dependency_envelope,
    run_command,
    success_envelope,
)


SERVER_NAME = "opencrow-web-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "opencrow-web-toolbox"
DISPLAY_NAME = "OpenCROW Web Toolbox"
OPERATIONS = [
    {"name": "web_discover", "description": "Directory, path, or vhost discovery with ffuf, gobuster, or dirb."},
    {"name": "web_fuzz", "description": "Typed request fuzzing with wfuzz."},
    {"name": "web_sqlmap_scan", "description": "Typed SQL injection automation with sqlmap."},
]


def _wordlist_error(operation: str, wordlist: Path, inputs: dict[str, object]) -> dict[str, object]:
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation=operation,
        summary=f"Wordlist does not exist: {wordlist}",
        inputs=inputs,
        stderr=f"Missing wordlist: {wordlist}",
        exit_code=2,
    )


def toolbox_verify(arguments: dict[str, object]) -> dict[str, object]:
    observations = [
        {"dependency": "ffuf", "available": command_exists("ffuf")},
        {"dependency": "gobuster", "available": command_exists("gobuster")},
        {"dependency": "dirb", "available": command_exists("dirb")},
        {"dependency": "wfuzz", "available": command_exists("wfuzz")},
        {"dependency": "sqlmap", "available": command_exists("sqlmap")},
    ]
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="Web toolbox dependency status returned.",
        inputs=arguments,
        observations=observations,
    )


def web_discover(arguments: dict[str, object]) -> dict[str, object]:
    backend = str(arguments.get("backend", "ffuf"))
    target_url = str(arguments.get("target_url", "")).strip()
    wordlist = Path(str(arguments.get("wordlist", ""))).expanduser().resolve()
    extensions = [str(item) for item in arguments.get("extensions", [])]
    mode = str(arguments.get("mode", "dir"))
    inputs = {
        "backend": backend,
        "target_url": target_url,
        "wordlist": str(wordlist),
        "extensions": extensions,
        "mode": mode,
    }
    if not target_url:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="web_discover",
            summary="Target URL is required.",
            inputs=inputs,
            stderr="Pass `target_url`.",
            exit_code=2,
        )
    if not wordlist.exists():
        return _wordlist_error("web_discover", wordlist, inputs)

    cwd, timeout_sec = default_execution(arguments)

    if backend == "ffuf":
        if not command_exists("ffuf"):
            return missing_dependency_envelope(TOOLBOX_ID, "web_discover", "ffuf", inputs)
        command = ["ffuf", "-noninteractive", "-s", "-w", str(wordlist), "-u", target_url]
        if extensions:
            command.extend(["-e", ",".join(extensions)])
    elif backend == "gobuster":
        if not command_exists("gobuster"):
            return missing_dependency_envelope(TOOLBOX_ID, "web_discover", "gobuster", inputs)
        if mode not in {"dir", "vhost"}:
            return error_envelope(
                toolbox=TOOLBOX_ID,
                operation="web_discover",
                summary=f"Unsupported gobuster mode: {mode}",
                inputs=inputs,
                stderr="Supported gobuster modes are `dir` and `vhost`.",
                exit_code=2,
            )
        command = ["gobuster", mode, "-q", "-w", str(wordlist), "-u", target_url]
        if extensions and mode == "dir":
            command.extend(["-x", ",".join(extensions)])
    elif backend == "dirb":
        if not command_exists("dirb"):
            return missing_dependency_envelope(TOOLBOX_ID, "web_discover", "dirb", inputs)
        command = ["dirb", target_url, str(wordlist), "-S"]
    else:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="web_discover",
            summary=f"Unsupported discovery backend: {backend}",
            inputs=inputs,
            stderr="Supported backends are `ffuf`, `gobuster`, and `dirb`.",
            exit_code=2,
        )

    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="web_discover",
        summary=f"Web discovery {'completed' if result['ok'] else 'failed'} with {backend}.",
        inputs=inputs,
        observations=[{"backend": backend, "mode": mode}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def web_fuzz(arguments: dict[str, object]) -> dict[str, object]:
    target_url = str(arguments.get("target_url", "")).strip()
    wordlist = Path(str(arguments.get("wordlist", ""))).expanduser().resolve()
    method = str(arguments.get("method", "GET"))
    payload_keyword = str(arguments.get("payload_keyword", "FUZZ"))
    data = arguments.get("data")
    headers = [str(value) for value in arguments.get("headers", [])]
    inputs = {
        "target_url": target_url,
        "wordlist": str(wordlist),
        "method": method,
        "payload_keyword": payload_keyword,
        "data": data,
        "headers": headers,
    }
    if not target_url:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="web_fuzz",
            summary="Target URL is required.",
            inputs=inputs,
            stderr="Pass `target_url`.",
            exit_code=2,
        )
    if not wordlist.exists():
        return _wordlist_error("web_fuzz", wordlist, inputs)
    if not command_exists("wfuzz"):
        return missing_dependency_envelope(TOOLBOX_ID, "web_fuzz", "wfuzz", inputs)

    cwd, timeout_sec = default_execution(arguments)
    command = ["wfuzz", "-u", target_url, "-w", str(wordlist), "-X", method]
    if data is not None:
        command.extend(["-d", str(data)])
    for header in headers:
        command.extend(["-H", header])
    if arguments.get("hide_status"):
        command.extend(["--hc", ",".join(str(item) for item in arguments["hide_status"])])
    if arguments.get("show_status"):
        command.extend(["--sc", ",".join(str(item) for item in arguments["show_status"])])
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="web_fuzz",
        summary=f"wfuzz run {'completed' if result['ok'] else 'failed'} against {target_url}.",
        inputs=inputs,
        observations=[{"tool": "wfuzz", "payload_keyword": payload_keyword}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def web_sqlmap_scan(arguments: dict[str, object]) -> dict[str, object]:
    target_url = str(arguments.get("target_url", "")).strip()
    data = arguments.get("data")
    cookie = arguments.get("cookie")
    env_level = int(arguments.get("level", 1))
    env_risk = int(arguments.get("risk", 1))
    inputs = {
        "target_url": target_url,
        "data": data,
        "cookie": cookie,
        "level": env_level,
        "risk": env_risk,
    }
    if not target_url:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="web_sqlmap_scan",
            summary="Target URL is required.",
            inputs=inputs,
            stderr="Pass `target_url`.",
            exit_code=2,
        )
    if not command_exists("sqlmap"):
        return missing_dependency_envelope(TOOLBOX_ID, "web_sqlmap_scan", "sqlmap", inputs)

    cwd, timeout_sec = default_execution(arguments)
    command = ["sqlmap", "--batch", "-u", target_url, "--level", str(env_level), "--risk", str(env_risk)]
    if data is not None:
        command.append(f"--data={data}")
    if cookie is not None:
        command.append(f"--cookie={cookie}")
    if arguments.get("test_parameter"):
        command.extend(["-p", str(arguments["test_parameter"])])
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="web_sqlmap_scan",
        summary=f"sqlmap scan {'completed' if result['ok'] else 'failed'} for {target_url}.",
        inputs=inputs,
        observations=[{"tool": "sqlmap", "level": env_level, "risk": env_risk}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        instructions="OpenCROW web toolbox MCP server. Use the typed web tools instead of raw shell commands.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return server metadata and the high-level operations provided by the web toolbox.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="Web toolbox server metadata returned.",
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
                description="Verify that the core web dependencies required by this server are available.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=toolbox_verify,
            ),
            MCPTool(
                name="toolbox_capabilities",
                description="Return the structured capability list for this toolbox server.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=make_toolbox_capabilities_handler(TOOLBOX_ID, OPERATIONS),
            ),
            MCPTool(
                name="web_discover",
                description="Run discovery against a target URL with ffuf, gobuster, or dirb.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "backend": {"type": "string", "enum": ["ffuf", "gobuster", "dirb"]},
                        "target_url": {"type": "string"},
                        "wordlist": {"type": "string"},
                        "mode": {"type": "string", "enum": ["dir", "vhost"]},
                        "extensions": {"type": "array", "items": {"type": "string"}},
                        "execution": {
                            "type": "object",
                            "properties": {
                                "cwd": {"type": "string"},
                                "timeout_sec": {"type": "integer", "minimum": 1},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["backend", "target_url", "wordlist"],
                    "additionalProperties": False,
                },
                handler=web_discover,
            ),
            MCPTool(
                name="web_fuzz",
                description="Run a typed wfuzz scan against a target URL.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "target_url": {"type": "string"},
                        "wordlist": {"type": "string"},
                        "method": {"type": "string"},
                        "payload_keyword": {"type": "string"},
                        "data": {"type": "string"},
                        "headers": {"type": "array", "items": {"type": "string"}},
                        "hide_status": {"type": "array", "items": {"type": "integer"}},
                        "show_status": {"type": "array", "items": {"type": "integer"}},
                        "execution": {
                            "type": "object",
                            "properties": {
                                "cwd": {"type": "string"},
                                "timeout_sec": {"type": "integer", "minimum": 1},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["target_url", "wordlist"],
                    "additionalProperties": False,
                },
                handler=web_fuzz,
            ),
            MCPTool(
                name="web_sqlmap_scan",
                description="Run a typed sqlmap scan against a target URL.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "target_url": {"type": "string"},
                        "data": {"type": "string"},
                        "cookie": {"type": "string"},
                        "test_parameter": {"type": "string"},
                        "level": {"type": "integer", "minimum": 1, "maximum": 5},
                        "risk": {"type": "integer", "minimum": 1, "maximum": 3},
                        "execution": {
                            "type": "object",
                            "properties": {
                                "cwd": {"type": "string"},
                                "timeout_sec": {"type": "integer", "minimum": 1},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["target_url"],
                    "additionalProperties": False,
                },
                handler=web_sqlmap_scan,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
