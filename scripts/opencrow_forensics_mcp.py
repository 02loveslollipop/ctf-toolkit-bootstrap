#!/usr/bin/env python3
"""OpenCROW forensics toolbox MCP server."""

from __future__ import annotations

import sys
from pathlib import Path

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


SERVER_NAME = "opencrow-forensics-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "opencrow-forensics-toolbox"
DISPLAY_NAME = "OpenCROW Forensics Toolbox"
OPERATIONS = [
    {"name": "forensics_metadata", "description": "Extract metadata from a file or directory with exiftool."},
    {"name": "forensics_carve", "description": "Recover embedded or deleted files with foremost."},
    {"name": "forensics_memory_inspect", "description": "Run a selected volatility3 plugin against a memory image."},
]


def _path_error(operation: str, path: Path, inputs: dict[str, object]) -> dict[str, object]:
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation=operation,
        summary=f"Input path does not exist: {path}",
        inputs=inputs,
        stderr=f"Missing path: {path}",
        exit_code=2,
    )


def toolbox_verify(arguments: dict[str, object]) -> dict[str, object]:
    env_name = str(arguments.get("env", "ctf"))
    observations = [
        {"dependency": "exiftool", "available": command_exists("exiftool")},
        {"dependency": "foremost", "available": command_exists("foremost")},
        {"dependency": f"conda:{env_name}:volatility3", "available": conda_module_available(env_name, "volatility3")},
    ]
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="Forensics toolbox dependency status returned.",
        inputs={"env": env_name},
        observations=observations,
        next_steps=["Use `forensics_metadata`, `forensics_carve`, or `forensics_memory_inspect` once the required dependencies are available."],
    )


def forensics_metadata(arguments: dict[str, object]) -> dict[str, object]:
    path = Path(str(arguments.get("path", ""))).expanduser().resolve()
    inputs = {"path": str(path)}
    if not path.exists():
        return _path_error("forensics_metadata", path, inputs)
    if not command_exists("exiftool"):
        return missing_dependency_envelope(TOOLBOX_ID, "forensics_metadata", "exiftool", inputs)

    cwd, timeout_sec = default_execution(arguments)
    result = run_command(["exiftool", str(path)], cwd=cwd, timeout_sec=timeout_sec)
    observations = [{"tool": "exiftool", "target": str(path)}]
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="forensics_metadata",
        summary=f"Metadata extraction {'completed' if result['ok'] else 'failed'} for {path.name}.",
        inputs=inputs,
        artifacts=[str(path)],
        observations=observations,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def forensics_carve(arguments: dict[str, object]) -> dict[str, object]:
    path = Path(str(arguments.get("path", ""))).expanduser().resolve()
    output_dir = Path(str(arguments.get("output_dir", path.parent / f"{path.stem}_foremost"))).expanduser().resolve()
    inputs = {"path": str(path), "output_dir": str(output_dir)}
    if not path.exists():
        return _path_error("forensics_carve", path, inputs)
    if not command_exists("foremost"):
        return missing_dependency_envelope(TOOLBOX_ID, "forensics_carve", "foremost", inputs)

    output_dir.mkdir(parents=True, exist_ok=True)
    cwd, timeout_sec = default_execution(arguments)
    result = run_command(["foremost", "-i", str(path), "-o", str(output_dir)], cwd=cwd, timeout_sec=timeout_sec)
    carved_artifacts = sorted(str(item) for item in output_dir.rglob("*") if item.is_file())
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="forensics_carve",
        summary=f"File carving {'completed' if result['ok'] else 'failed'} for {path.name}.",
        inputs=inputs,
        artifacts=carved_artifacts,
        observations=[{"tool": "foremost", "carved_file_count": len(carved_artifacts)}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        next_steps=["Review `audit.txt` and the carved file tree in the output directory."] if result["ok"] else [],
    )


def forensics_memory_inspect(arguments: dict[str, object]) -> dict[str, object]:
    image_path = Path(str(arguments.get("image_path", ""))).expanduser().resolve()
    env_name = str(arguments.get("env", "ctf"))
    plugin = str(arguments.get("plugin", "")).strip()
    plugin_args = [str(value) for value in arguments.get("plugin_args", [])]
    inputs = {"image_path": str(image_path), "plugin": plugin, "plugin_args": plugin_args, "env": env_name}
    if not image_path.exists():
        return _path_error("forensics_memory_inspect", image_path, inputs)
    if not plugin:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="forensics_memory_inspect",
            summary="Volatility plugin name is required.",
            inputs=inputs,
            stderr="Pass `plugin`, for example `windows.info.Info` or `linux.pslist.PsList`.",
            exit_code=2,
        )
    if not conda_module_available(env_name, "volatility3"):
        return missing_dependency_envelope(TOOLBOX_ID, "forensics_memory_inspect", f"conda:{env_name}:volatility3", inputs)

    cwd, timeout_sec = default_execution(arguments)
    command = ["conda", "run", "-n", env_name, "vol", "-f", str(image_path), plugin, *plugin_args]
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    envelope_factory = success_envelope if result["ok"] else error_envelope
    return envelope_factory(
        toolbox=TOOLBOX_ID,
        operation="forensics_memory_inspect",
        summary=f"Volatility inspection {'completed' if result['ok'] else 'failed'} with plugin {plugin}.",
        inputs=inputs,
        artifacts=[str(image_path)],
        observations=[{"tool": "volatility3", "plugin": plugin}],
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        instructions="OpenCROW forensics toolbox MCP server. Use the typed forensics tools instead of raw shell commands.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return server metadata and the high-level operations provided by the forensics toolbox.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="Forensics toolbox server metadata returned.",
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
                description="Verify that the core forensics dependencies required by this server are available.",
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
                name="forensics_metadata",
                description="Extract metadata from a file or directory with exiftool.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "execution": {
                            "type": "object",
                            "properties": {
                                "cwd": {"type": "string"},
                                "timeout_sec": {"type": "integer", "minimum": 1},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=forensics_metadata,
            ),
            MCPTool(
                name="forensics_carve",
                description="Recover files from an image or blob with foremost.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "execution": {
                            "type": "object",
                            "properties": {
                                "cwd": {"type": "string"},
                                "timeout_sec": {"type": "integer", "minimum": 1},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=forensics_carve,
            ),
            MCPTool(
                name="forensics_memory_inspect",
                description="Run a selected volatility3 plugin against a memory image.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "image_path": {"type": "string"},
                        "plugin": {"type": "string"},
                        "plugin_args": {"type": "array", "items": {"type": "string"}},
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
                    "required": ["image_path", "plugin"],
                    "additionalProperties": False,
                },
                handler=forensics_memory_inspect,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
