#!/usr/bin/env python3
"""OpenCROW utility toolbox MCP server."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from opencrow_mcp_core import (
    MCPTool,
    StdioMCPServer,
    command_exists,
    default_execution,
    error_envelope,
    make_toolbox_capabilities_handler,
    make_toolbox_info_handler,
    missing_dependency_envelope,
    normalize_path,
    run_command,
    success_envelope,
)


SERVER_NAME = "opencrow-utility-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "opencrow-utility-toolbox"
DISPLAY_NAME = "OpenCROW Utility Toolbox"
OPERATIONS = [
    {"name": "utility_search", "description": "Search a workspace with ripgrep using typed filters and output modes."},
    {"name": "utility_json_query", "description": "Run a typed jq query against a JSON file or inline payload."},
    {"name": "utility_yaml_query", "description": "Run a typed yq query against a YAML file or inline payload."},
    {"name": "utility_hexdump", "description": "Create a typed xxd hex dump for a file region."},
]
SYSTEM_DEPENDENCIES = [
    "jq",
    "yq",
    "xxd",
    "tmux",
    "screen",
    "rg",
    "fzf",
    "opencrow-autosetup",
    "opencrow-exploit",
    "opencrow-netcat-mcp",
    "opencrow-ssh-mcp",
    "opencrow-minecraft-mcp",
]


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
    observations = [
        {"dependency": dependency, "available": command_exists(dependency), "type": "system-command"}
        for dependency in SYSTEM_DEPENDENCIES
    ]
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="Utility toolbox dependency status returned.",
        inputs=arguments,
        observations=observations,
        next_steps=[
            "Use `utility_search` to navigate challenge trees quickly.",
            "Use `utility_json_query` and `utility_yaml_query` for structured-data slicing before deeper analysis.",
        ],
    )


def utility_search(arguments: dict[str, object]) -> dict[str, object]:
    pattern = str(arguments.get("pattern", "")).strip()
    root = normalize_path(arguments.get("root")) or "."
    files_only = bool(arguments.get("files_only", False))
    ignore_case = bool(arguments.get("ignore_case", False))
    hidden = bool(arguments.get("hidden", True))
    file_glob = str(arguments.get("file_glob", "")).strip()
    max_count = arguments.get("max_count")
    inputs = {
        "pattern": pattern,
        "root": root,
        "files_only": files_only,
        "ignore_case": ignore_case,
        "hidden": hidden,
        "file_glob": file_glob or None,
        "max_count": int(max_count) if max_count is not None else None,
    }
    if not pattern:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="utility_search",
            summary="A search pattern is required.",
            inputs=inputs,
            stderr="Pass `pattern`.",
            exit_code=2,
        )
    if not command_exists("rg"):
        return missing_dependency_envelope(TOOLBOX_ID, "utility_search", "rg", inputs)

    command = ["rg"]
    if files_only:
        command.append("-l")
    if ignore_case:
        command.append("-i")
    if hidden:
        command.append("--hidden")
    if file_glob:
        command.extend(["-g", file_glob])
    if max_count is not None:
        command.extend(["-m", str(int(max_count))])
    command.extend([pattern, root])
    cwd, timeout_sec = default_execution(arguments)
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    envelope_factory = success_envelope if result["ok"] or result["exit_code"] == 1 else error_envelope
    summary = "Search completed." if result["ok"] else ("Search completed with no matches." if result["exit_code"] == 1 else "Search failed.")
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="utility_search",
        summary=summary,
        inputs=inputs,
        artifacts=[root],
        observations=[{"tool": "rg", "files_only": files_only}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def _with_input_source(
    *,
    path_text: str | None,
    inline_input: str | None,
    operation: str,
    inputs: dict[str, object],
    callback,
) -> dict[str, object]:
    if (path_text is None) == (inline_input is None):
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation=operation,
            summary="Pass exactly one of `path` or `input_text`.",
            inputs=inputs,
            stderr="Use `path` for a file input or `input_text` for inline content.",
            exit_code=2,
        )
    if path_text is not None:
        path = Path(path_text).expanduser().resolve()
        if not path.exists():
            return _path_error(operation, path, inputs)
        return callback(path)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tmp", prefix="opencrow-utility-", delete=False) as handle:
            handle.write(inline_input or "")
            temp_path = Path(handle.name)
        return callback(temp_path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def utility_json_query(arguments: dict[str, object]) -> dict[str, object]:
    query = str(arguments.get("query", "")).strip()
    raw_path = str(arguments.get("path", "")).strip() or None
    input_text = str(arguments.get("input_text")) if arguments.get("input_text") is not None else None
    raw_output = bool(arguments.get("raw_output", False))
    inputs = {
        "query": query,
        "path": raw_path,
        "has_input_text": input_text is not None,
        "raw_output": raw_output,
    }
    if not query:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="utility_json_query",
            summary="A jq query is required.",
            inputs=inputs,
            stderr="Pass `query`.",
            exit_code=2,
        )
    if not command_exists("jq"):
        return missing_dependency_envelope(TOOLBOX_ID, "utility_json_query", "jq", inputs)

    cwd, timeout_sec = default_execution(arguments)

    def run_with(path: Path) -> dict[str, object]:
        command = ["jq"]
        if raw_output:
            command.append("-r")
        command.extend([query, str(path)])
        result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
        envelope_factory = success_envelope if result["ok"] else error_envelope
        return envelope_factory(
            toolbox=TOOLBOX_ID,
            operation="utility_json_query",
            summary=f"jq query {'completed' if result['ok'] else 'failed'}.",
            inputs=inputs,
            artifacts=[str(path)] if raw_path is not None else [],
            observations=[{"tool": "jq", "raw_output": raw_output}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
        )

    return _with_input_source(path_text=raw_path, inline_input=input_text, operation="utility_json_query", inputs=inputs, callback=run_with)


def utility_yaml_query(arguments: dict[str, object]) -> dict[str, object]:
    query = str(arguments.get("query", "")).strip()
    raw_path = str(arguments.get("path", "")).strip() or None
    input_text = str(arguments.get("input_text")) if arguments.get("input_text") is not None else None
    inputs = {
        "query": query,
        "path": raw_path,
        "has_input_text": input_text is not None,
    }
    if not query:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="utility_yaml_query",
            summary="A yq query is required.",
            inputs=inputs,
            stderr="Pass `query`.",
            exit_code=2,
        )
    if not command_exists("yq"):
        return missing_dependency_envelope(TOOLBOX_ID, "utility_yaml_query", "yq", inputs)

    cwd, timeout_sec = default_execution(arguments)

    def run_with(path: Path) -> dict[str, object]:
        command = ["yq", query, str(path)]
        result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
        envelope_factory = success_envelope if result["ok"] else error_envelope
        return envelope_factory(
            toolbox=TOOLBOX_ID,
            operation="utility_yaml_query",
            summary=f"yq query {'completed' if result['ok'] else 'failed'}.",
            inputs=inputs,
            artifacts=[str(path)] if raw_path is not None else [],
            observations=[{"tool": "yq"}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
        )

    return _with_input_source(path_text=raw_path, inline_input=input_text, operation="utility_yaml_query", inputs=inputs, callback=run_with)


def utility_hexdump(arguments: dict[str, object]) -> dict[str, object]:
    raw_path = str(arguments.get("path", "")).strip()
    length = arguments.get("length")
    offset = arguments.get("offset")
    cols = int(arguments.get("cols", 16))
    inputs = {
        "path": raw_path,
        "length": int(length) if length is not None else None,
        "offset": int(offset) if offset is not None else None,
        "cols": cols,
    }
    if not raw_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="utility_hexdump",
            summary="A target file path is required.",
            inputs=inputs,
            stderr="Pass `path`.",
            exit_code=2,
        )
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        return _path_error("utility_hexdump", path, inputs)
    if not command_exists("xxd"):
        return missing_dependency_envelope(TOOLBOX_ID, "utility_hexdump", "xxd", inputs)

    command = ["xxd", "-g", "1", "-c", str(cols)]
    if offset is not None:
        command.extend(["-s", str(int(offset))])
    if length is not None:
        command.extend(["-l", str(int(length))])
    command.append(str(path))
    cwd, timeout_sec = default_execution(arguments)
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="utility_hexdump",
        summary=f"Hex dump {'completed' if result['ok'] else 'failed'} for {path.name}.",
        inputs=inputs,
        artifacts=[str(path)],
        observations=[{"tool": "xxd"}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        instructions="OpenCROW utility toolbox MCP server. Use typed utility helpers instead of raw shell commands.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return metadata about the OpenCROW utility toolbox MCP server.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="OpenCROW utility toolbox information returned.",
                    operations=OPERATIONS,
                ),
            ),
            MCPTool(
                name="toolbox_verify",
                description="Return dependency status for the OpenCROW utility toolbox MCP server.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=toolbox_verify,
            ),
            MCPTool(
                name="toolbox_capabilities",
                description="Return the structured operations exposed by the OpenCROW utility toolbox MCP server.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=make_toolbox_capabilities_handler(TOOLBOX_ID, OPERATIONS),
            ),
            MCPTool(
                name="utility_search",
                description="Search a workspace with ripgrep using typed filters and output modes.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "root": {"type": "string"},
                        "files_only": {"type": "boolean"},
                        "ignore_case": {"type": "boolean"},
                        "hidden": {"type": "boolean"},
                        "file_glob": {"type": "string"},
                        "max_count": {"type": "integer"},
                        "execution": {"type": "object"},
                    },
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
                handler=utility_search,
            ),
            MCPTool(
                name="utility_json_query",
                description="Run a typed jq query against a JSON file or inline payload.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "input_text": {"type": "string"},
                        "raw_output": {"type": "boolean"},
                        "execution": {"type": "object"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=utility_json_query,
            ),
            MCPTool(
                name="utility_yaml_query",
                description="Run a typed yq query against a YAML file or inline payload.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "input_text": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=utility_yaml_query,
            ),
            MCPTool(
                name="utility_hexdump",
                description="Create a typed xxd hex dump for a file region.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "length": {"type": "integer"},
                        "offset": {"type": "integer"},
                        "cols": {"type": "integer"},
                        "execution": {"type": "object"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=utility_hexdump,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
