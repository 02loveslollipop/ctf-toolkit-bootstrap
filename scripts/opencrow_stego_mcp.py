#!/usr/bin/env python3
"""OpenCROW stego toolbox MCP server."""

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
    missing_dependency_envelope,
    run_command,
    success_envelope,
)


SERVER_NAME = "opencrow-stego-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "opencrow-stego-toolbox"
DISPLAY_NAME = "OpenCROW Stego Toolbox"
OPERATIONS = [
    {
        "name": "stego_inspect",
        "description": "Inspect a stego candidate with file typing, zsteg triage, and steghide info where applicable.",
    },
    {
        "name": "stego_extract",
        "description": "Extract hidden data with a typed steghide or zsteg workflow.",
    },
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
        {"dependency": "zsteg", "available": command_exists("zsteg")},
        {"dependency": "steghide", "available": command_exists("steghide")},
        {"dependency": "file", "available": command_exists("file")},
    ]
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="Stego toolbox dependency status returned.",
        inputs=arguments,
        observations=observations,
        next_steps=["Use `stego_inspect` on a candidate media file once the required tools are available."],
    )


def stego_inspect(arguments: dict[str, object]) -> dict[str, object]:
    path = Path(str(arguments.get("path", ""))).expanduser().resolve()
    inputs = {"path": str(path), "passphrase": arguments.get("passphrase")}
    if not path.exists():
        return _path_error("stego_inspect", path, inputs)

    cwd, timeout_sec = default_execution(arguments)
    observations: list[dict[str, object]] = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    commands: list[str] = []

    if command_exists("file"):
        file_result = run_command(["file", "-b", str(path)], cwd=cwd, timeout_sec=timeout_sec)
        commands.append(file_result["command"])
        stdout_parts.append(f"$ {file_result['command']}\n{file_result['stdout']}".strip())
        if file_result["stderr"]:
            stderr_parts.append(file_result["stderr"])
        observations.append(
            {
                "tool": "file",
                "ok": file_result["ok"],
                "description": file_result["stdout"].strip(),
            }
        )

    suffix = path.suffix.lower()
    if suffix in {".png", ".bmp"}:
        if not command_exists("zsteg"):
            return missing_dependency_envelope(TOOLBOX_ID, "stego_inspect", "zsteg", inputs)
        zsteg_result = run_command(["zsteg", "-a", str(path)], cwd=cwd, timeout_sec=timeout_sec)
        commands.append(zsteg_result["command"])
        stdout_parts.append(f"$ {zsteg_result['command']}\n{zsteg_result['stdout']}".strip())
        if zsteg_result["stderr"]:
            stderr_parts.append(zsteg_result["stderr"])
        observations.append(
            {
                "tool": "zsteg",
                "ok": zsteg_result["ok"],
                "line_count": len([line for line in zsteg_result["stdout"].splitlines() if line.strip()]),
            }
        )

    if command_exists("steghide") and suffix in {".jpg", ".jpeg", ".bmp", ".wav", ".au"}:
        steghide_cmd = ["steghide", "info", str(path)]
        if arguments.get("passphrase") is not None:
            steghide_cmd.extend(["-p", str(arguments["passphrase"])])
        steghide_result = run_command(steghide_cmd, cwd=cwd, timeout_sec=timeout_sec)
        commands.append(steghide_result["command"])
        stdout_parts.append(f"$ {steghide_result['command']}\n{steghide_result['stdout']}".strip())
        if steghide_result["stderr"]:
            stderr_parts.append(steghide_result["stderr"])
        observations.append(
            {
                "tool": "steghide",
                "ok": steghide_result["ok"],
                "inspected_format": suffix,
            }
        )

    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="stego_inspect",
        summary=f"Stego inspection completed for {path.name}.",
        inputs=inputs,
        artifacts=[str(path)],
        observations=observations,
        command=" && ".join(commands) if commands else None,
        stdout="\n\n".join(part for part in stdout_parts if part),
        stderr="\n".join(part for part in stderr_parts if part),
        next_steps=[
            "Use `stego_extract` if the inspection indicates an embedded payload.",
            "If zsteg output shows candidate payload names, pass the exact payload string to `stego_extract`.",
        ],
    )


def stego_extract(arguments: dict[str, object]) -> dict[str, object]:
    path = Path(str(arguments.get("path", ""))).expanduser().resolve()
    tool = str(arguments.get("tool", "steghide"))
    output_dir = Path(str(arguments.get("output_dir", path.parent))).expanduser().resolve()
    payload = arguments.get("payload")
    extract_name = str(arguments.get("extract_name", "zsteg_extract.bin"))
    inputs = {
        "path": str(path),
        "tool": tool,
        "output_dir": str(output_dir),
        "payload": payload,
        "extract_name": extract_name,
        "passphrase": arguments.get("passphrase"),
    }
    if not path.exists():
        return _path_error("stego_extract", path, inputs)
    output_dir.mkdir(parents=True, exist_ok=True)

    cwd, timeout_sec = default_execution(arguments)

    if tool == "steghide":
        if not command_exists("steghide"):
            return missing_dependency_envelope(TOOLBOX_ID, "stego_extract", "steghide", inputs)
        output_path = output_dir / str(arguments.get("extract_file", f"{path.stem}.extracted"))
        command = [
            "steghide",
            "extract",
            "-sf",
            str(path),
            "-xf",
            str(output_path),
            "-f",
        ]
        if arguments.get("passphrase") is not None:
            command.extend(["-p", str(arguments["passphrase"])])
        result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
        return (
            success_envelope(
                toolbox=TOOLBOX_ID,
                operation="stego_extract",
                summary=f"Steghide extraction completed for {path.name}.",
                inputs=inputs,
                artifacts=[str(output_path)] if output_path.exists() else [],
                observations=[{"tool": "steghide", "output_path": str(output_path), "exists": output_path.exists()}],
                command=result["command"],
                stdout=result["stdout"],
                stderr=result["stderr"],
                exit_code=result["exit_code"],
            )
            if result["ok"]
            else error_envelope(
                toolbox=TOOLBOX_ID,
                operation="stego_extract",
                summary=f"Steghide extraction failed for {path.name}.",
                inputs=inputs,
                command=result["command"],
                stdout=result["stdout"],
                stderr=result["stderr"],
                exit_code=result["exit_code"],
            )
        )

    if tool == "zsteg":
        if not command_exists("zsteg"):
            return missing_dependency_envelope(TOOLBOX_ID, "stego_extract", "zsteg", inputs)
        if not payload:
            return error_envelope(
                toolbox=TOOLBOX_ID,
                operation="stego_extract",
                summary="zsteg extraction requires a payload name.",
                inputs=inputs,
                stderr="Pass `payload` with a value like `1b,rgb,lsb,xy`.",
                exit_code=2,
            )
        output_path = output_dir / extract_name
        command = ["zsteg", "-E", str(payload), str(path)]
        result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
        if result["ok"]:
            output_path.write_bytes(result.get("stdout_bytes", b""))
        return (
            success_envelope(
                toolbox=TOOLBOX_ID,
                operation="stego_extract",
                summary=f"zsteg extraction completed for {path.name}.",
                inputs=inputs,
                artifacts=[str(output_path)],
                observations=[{"tool": "zsteg", "payload": str(payload), "output_path": str(output_path)}],
                command=result["command"],
                stdout=result["stdout"],
                stderr=result["stderr"],
                exit_code=result["exit_code"],
            )
            if result["ok"]
            else error_envelope(
                toolbox=TOOLBOX_ID,
                operation="stego_extract",
                summary=f"zsteg extraction failed for {path.name}.",
                inputs=inputs,
                command=result["command"],
                stdout=result["stdout"],
                stderr=result["stderr"],
                exit_code=result["exit_code"],
            )
        )

    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="stego_extract",
        summary=f"Unsupported stego extraction tool: {tool}",
        inputs=inputs,
        stderr="Supported values are `steghide` and `zsteg`.",
        exit_code=2,
    )


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        instructions="OpenCROW stego toolbox MCP server. Use the typed stego tools instead of raw shell commands.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return server metadata and the high-level operations provided by the stego toolbox.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="Stego toolbox server metadata returned.",
                    operations=OPERATIONS,
                ),
            ),
            MCPTool(
                name="toolbox_verify",
                description="Verify that the core stego dependencies required by this server are available.",
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
                name="stego_inspect",
                description="Inspect a candidate stego file with file typing, zsteg, and steghide-aware checks.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "passphrase": {"type": "string"},
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
                handler=stego_inspect,
            ),
            MCPTool(
                name="stego_extract",
                description="Extract a hidden payload with steghide or zsteg using typed arguments.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "tool": {"type": "string", "enum": ["steghide", "zsteg"]},
                        "payload": {"type": "string"},
                        "passphrase": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "extract_file": {"type": "string"},
                        "extract_name": {"type": "string"},
                        "execution": {
                            "type": "object",
                            "properties": {
                                "cwd": {"type": "string"},
                                "timeout_sec": {"type": "integer", "minimum": 1},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["path", "tool"],
                    "additionalProperties": False,
                },
                handler=stego_extract,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
