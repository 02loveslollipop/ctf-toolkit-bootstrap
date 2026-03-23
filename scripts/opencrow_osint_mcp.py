#!/usr/bin/env python3
"""OpenCROW OSINT toolbox MCP server."""

from __future__ import annotations

import os
import sys

from opencrow_mcp_core import (
    MCPTool,
    StdioMCPServer,
    command_exists,
    conda_module_available,
    default_execution,
    error_envelope,
    make_toolbox_capabilities_handler,
    make_toolbox_info_handler,
    make_toolbox_self_test_handler,
    missing_dependency_envelope,
    run_command,
    success_envelope,
)


SERVER_NAME = "opencrow-osint-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "opencrow-osint-toolbox"
DISPLAY_NAME = "OpenCROW OSINT Toolbox"
OPERATIONS = [
    {"name": "osint_username_lookup", "description": "Scan a username with sherlock."},
    {"name": "osint_archive_lookup", "description": "Query archived pages with waybackpy."},
    {"name": "osint_shodan_lookup", "description": "Query Shodan for host or search results."},
]


def toolbox_verify(arguments: dict[str, object]) -> dict[str, object]:
    env_name = str(arguments.get("env", "ctf"))
    observations = [
        {"dependency": "sherlock", "available": command_exists("sherlock")},
        {"dependency": f"conda:{env_name}:waybackpy", "available": conda_module_available(env_name, "waybackpy")},
        {"dependency": f"conda:{env_name}:shodan", "available": conda_module_available(env_name, "shodan")},
        {"credential": "SHODAN_API_KEY", "available": bool(os.environ.get("SHODAN_API_KEY"))},
    ]
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="OSINT toolbox dependency status returned.",
        inputs={"env": env_name},
        observations=observations,
    )


def osint_username_lookup(arguments: dict[str, object]) -> dict[str, object]:
    username = str(arguments.get("username", "")).strip()
    if not username:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="osint_username_lookup",
            summary="Username is required.",
            inputs=arguments,
            stderr="Pass `username`.",
            exit_code=2,
        )
    if not command_exists("sherlock"):
        return missing_dependency_envelope(TOOLBOX_ID, "osint_username_lookup", "sherlock", {"username": username})

    cwd, timeout_sec = default_execution(arguments)
    result = run_command(["sherlock", username], cwd=cwd, timeout_sec=timeout_sec)
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="osint_username_lookup",
        summary=f"Sherlock lookup {'completed' if result['ok'] else 'failed'} for {username}.",
        inputs={"username": username},
        observations=[{"tool": "sherlock", "username": username}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def osint_archive_lookup(arguments: dict[str, object]) -> dict[str, object]:
    url = str(arguments.get("url", "")).strip()
    env_name = str(arguments.get("env", "ctf"))
    mode = str(arguments.get("mode", "latest"))
    if not url:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="osint_archive_lookup",
            summary="URL is required.",
            inputs=arguments,
            stderr="Pass `url`.",
            exit_code=2,
        )
    if not conda_module_available(env_name, "waybackpy"):
        return missing_dependency_envelope(TOOLBOX_ID, "osint_archive_lookup", f"conda:{env_name}:waybackpy", {"url": url, "env": env_name})

    cwd, timeout_sec = default_execution(arguments)
    if mode == "latest":
        command = ["conda", "run", "-n", env_name, "waybackpy", "-u", url, "-n"]
    elif mode == "available":
        command = ["conda", "run", "-n", env_name, "waybackpy", "-u", url, "--cdx", "-l", "1"]
    else:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="osint_archive_lookup",
            summary=f"Unsupported wayback lookup mode: {mode}",
            inputs={"url": url, "env": env_name, "mode": mode},
            stderr="Supported values are `latest` and `available`.",
            exit_code=2,
        )
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="osint_archive_lookup",
        summary=f"Wayback lookup {'completed' if result['ok'] else 'failed'} for {url}.",
        inputs={"url": url, "env": env_name, "mode": mode},
        observations=[{"tool": "waybackpy", "mode": mode}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def osint_shodan_lookup(arguments: dict[str, object]) -> dict[str, object]:
    lookup_type = str(arguments.get("lookup_type", "host"))
    query = str(arguments.get("query", "")).strip()
    env_name = str(arguments.get("env", "ctf"))
    inputs = {"lookup_type": lookup_type, "query": query, "env": env_name}
    if not query:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="osint_shodan_lookup",
            summary="Shodan query is required.",
            inputs=inputs,
            stderr="Pass `query`.",
            exit_code=2,
        )
    if not os.environ.get("SHODAN_API_KEY"):
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="osint_shodan_lookup",
            summary="Shodan API key is missing.",
            inputs=inputs,
            stderr="Set SHODAN_API_KEY before calling this operation.",
            exit_code=3,
            next_steps=["Export SHODAN_API_KEY and retry the request."],
        )
    if not conda_module_available(env_name, "shodan"):
        return missing_dependency_envelope(TOOLBOX_ID, "osint_shodan_lookup", f"conda:{env_name}:shodan", inputs)

    cwd, timeout_sec = default_execution(arguments)
    if lookup_type not in {"host", "search"}:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="osint_shodan_lookup",
            summary=f"Unsupported Shodan lookup type: {lookup_type}",
            inputs=inputs,
            stderr="Supported values are `host` and `search`.",
            exit_code=2,
        )
    inline_code = (
        "import json, os, sys\n"
        "from shodan import Shodan\n"
        "api = Shodan(os.environ['SHODAN_API_KEY'])\n"
        f"mode = {lookup_type!r}\n"
        f"query = {query!r}\n"
        "payload = api.host(query) if mode == 'host' else api.search(query)\n"
        "print(json.dumps(payload, indent=2, sort_keys=True))\n"
    )
    command = ["conda", "run", "-n", env_name, "python", "-c", inline_code]
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="osint_shodan_lookup",
        summary=f"Shodan lookup {'completed' if result['ok'] else 'failed'} for {query}.",
        inputs=inputs,
        observations=[{"tool": "shodan", "lookup_type": lookup_type}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        instructions="OpenCROW OSINT toolbox MCP server. Use the typed OSINT tools instead of raw shell commands.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return server metadata and the high-level operations provided by the OSINT toolbox.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="OSINT toolbox server metadata returned.",
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
                description="Verify that the core OSINT dependencies required by this server are available.",
                input_schema={
                    "type": "object",
                    "properties": {"env": {"type": "string"}},
                    "additionalProperties": False,
                },
                handler=toolbox_verify,
            ),
            MCPTool(
                name="toolbox_capabilities",
                description="Return the structured capability list for this toolbox server.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=make_toolbox_capabilities_handler(TOOLBOX_ID, OPERATIONS),
            ),
            MCPTool(
                name="osint_username_lookup",
                description="Run sherlock against a username.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "username": {"type": "string"},
                        "execution": {
                            "type": "object",
                            "properties": {
                                "cwd": {"type": "string"},
                                "timeout_sec": {"type": "integer", "minimum": 1},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["username"],
                    "additionalProperties": False,
                },
                handler=osint_username_lookup,
            ),
            MCPTool(
                name="osint_archive_lookup",
                description="Query the Wayback Machine through waybackpy.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "mode": {"type": "string", "enum": ["latest", "available"]},
                        "env": {"type": "string"},
                        "execution": {
                            "type": "object",
                            "properties": {
                                "cwd": {"type": "string"},
                                "timeout_sec": {"type": "integer", "minimum": 1},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
                handler=osint_archive_lookup,
            ),
            MCPTool(
                name="osint_shodan_lookup",
                description="Run a host or search lookup through the Shodan CLI.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "lookup_type": {"type": "string", "enum": ["host", "search"]},
                        "query": {"type": "string"},
                        "env": {"type": "string"},
                        "execution": {
                            "type": "object",
                            "properties": {
                                "cwd": {"type": "string"},
                                "timeout_sec": {"type": "integer", "minimum": 1},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["lookup_type", "query"],
                    "additionalProperties": False,
                },
                handler=osint_shodan_lookup,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
