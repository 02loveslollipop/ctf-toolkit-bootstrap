#!/usr/bin/env python3
"""OpenCROW Minecraft async MCP server."""

from __future__ import annotations

import sys

from opencrow_io_mcp_common import parse_json_stdout, run_backend_script
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


SERVER_NAME = "opencrow-minecraft-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "minecraft-async"
DISPLAY_NAME = "OpenCROW I/O - Minecraft Async"
BACKEND_SCRIPT = "minecraft_async.py"
OPERATIONS = [
    {"name": "minecraft_status", "description": "Return structured status about the installed Minecraft client and managed session."},
    {"name": "minecraft_launch", "description": "Launch the installed Minecraft client with typed direct-backend options."},
    {"name": "minecraft_join_server", "description": "Launch directly into a multiplayer server via quick play."},
    {"name": "minecraft_join_world", "description": "Launch directly into a local world via quick play."},
    {"name": "minecraft_focus", "description": "Focus the Minecraft window on the current X11 display."},
    {"name": "minecraft_send_text", "description": "Type raw text into the focused Minecraft input field."},
    {"name": "minecraft_chat", "description": "Open chat and send a message."},
    {"name": "minecraft_command", "description": "Open slash-command mode and send a command."},
    {"name": "minecraft_screenshot", "description": "Capture the current Minecraft window to a PNG file."},
    {"name": "minecraft_read_log", "description": "Read or follow the relevant Minecraft logs."},
    {"name": "minecraft_stop", "description": "Stop the managed Minecraft session."},
]


def _status_command(arguments: dict[str, object]) -> list[str]:
    command = ["status", "--json"]
    if arguments.get("session"):
        command.extend(["--session", str(arguments["session"])])
    if arguments.get("game_dir"):
        command.extend(["--game-dir", str(arguments["game_dir"])])
    return command


def _run_status(arguments: dict[str, object]) -> tuple[dict[str, object], dict[str, object] | None]:
    cwd, timeout_sec = default_execution(arguments)
    result = run_backend_script(BACKEND_SCRIPT, _status_command(arguments), cwd=cwd, timeout_sec=timeout_sec)
    return result, parse_json_stdout(result)


def _minecraft_artifacts(status_payload: dict[str, object] | None) -> list[str]:
    if not isinstance(status_payload, dict):
        return []
    artifacts: list[str] = []
    game_dir = status_payload.get("game_dir")
    latest_log = status_payload.get("latest_log")
    if isinstance(game_dir, str):
        artifacts.append(game_dir)
    if isinstance(latest_log, str):
        artifacts.append(latest_log)
    meta = status_payload.get("meta")
    if isinstance(meta, dict):
        for value in meta.values():
            if isinstance(value, str) and value.startswith("/"):
                artifacts.append(value)
    return artifacts


def toolbox_verify(arguments: dict[str, object]) -> dict[str, object]:
    result, payload = _run_status(arguments)
    observations = [
        {"dependency": "python3", "available": True},
        {"dependency": "imagemagick_import", "available": command_exists("import")},
        {"dependency": "status_backend_ok", "available": result["ok"]},
    ]
    if isinstance(payload, dict):
        observations.append(payload)
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="Minecraft async MCP server dependency status returned.",
        inputs=arguments,
        artifacts=_minecraft_artifacts(payload),
        observations=observations,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        next_steps=["Use `minecraft_status` to inspect the current client/session state before launching or driving the window."],
    )


def minecraft_status(arguments: dict[str, object]) -> dict[str, object]:
    result, payload = _run_status(arguments)
    if result["ok"] and isinstance(payload, dict):
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="minecraft_status",
            summary="Minecraft status returned.",
            inputs={"session": arguments.get("session", "default"), "game_dir": arguments.get("game_dir")},
            artifacts=_minecraft_artifacts(payload),
            observations=[payload],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="minecraft_status",
        summary="Failed to load Minecraft status.",
        inputs={"session": arguments.get("session", "default"), "game_dir": arguments.get("game_dir")},
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def _run_action(operation: str, command: list[str], inputs: dict[str, object], *, status_args: dict[str, object] | None = None) -> dict[str, object]:
    cwd, timeout_sec = default_execution(inputs)
    result = run_backend_script(BACKEND_SCRIPT, command, cwd=cwd, timeout_sec=timeout_sec)
    status_payload = None
    if result["ok"] and status_args is not None:
        _, status_payload = _run_status(status_args)
    artifacts = _minecraft_artifacts(status_payload)
    if operation == "minecraft_screenshot" and inputs.get("output"):
        artifacts = [str(inputs["output"])] + artifacts
    if result["ok"]:
        observations = [status_payload] if isinstance(status_payload, dict) else []
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation=operation,
            summary=f"{operation} completed.",
            inputs=inputs,
            artifacts=artifacts,
            observations=observations,
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation=operation,
        summary=f"{operation} failed.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def minecraft_launch(arguments: dict[str, object]) -> dict[str, object]:
    session = str(arguments.get("session", "default"))
    command = ["launch", "--session", session]
    inputs = {
        "session": session,
        "game_dir": arguments.get("game_dir"),
        "backend": arguments.get("backend", "auto"),
        "username": arguments.get("username", "Player"),
        "version": arguments.get("version", "1.21.8"),
        "server": arguments.get("server"),
        "world": arguments.get("world"),
        "java": arguments.get("java"),
        "width": arguments.get("width"),
        "height": arguments.get("height"),
        "min_memory": arguments.get("min_memory", 512),
        "max_memory": arguments.get("max_memory", 4096),
        "instance": arguments.get("instance", "default"),
        "dry_run": bool(arguments.get("dry_run", False)),
        "execution": arguments.get("execution"),
    }
    if inputs["game_dir"]:
        command.extend(["--game-dir", str(inputs["game_dir"])])
    command.extend(["--backend", str(inputs["backend"])])
    command.extend(["--username", str(inputs["username"])])
    command.extend(["--version", str(inputs["version"])])
    if inputs["server"]:
        command.extend(["--server", str(inputs["server"])])
    if inputs["world"]:
        command.extend(["--world", str(inputs["world"])])
    if inputs["java"]:
        command.extend(["--java", str(inputs["java"])])
    if inputs["width"] is not None:
        command.extend(["--width", str(int(inputs["width"]))])
    if inputs["height"] is not None:
        command.extend(["--height", str(int(inputs["height"]))])
    command.extend(["--min-memory", str(int(inputs["min_memory"]))])
    command.extend(["--max-memory", str(int(inputs["max_memory"]))])
    command.extend(["--instance", str(inputs["instance"])])
    if inputs["dry_run"]:
        command.append("--dry-run")
    return _run_action("minecraft_launch", command, inputs, status_args={"session": session, "game_dir": inputs["game_dir"]})


def minecraft_join_server(arguments: dict[str, object]) -> dict[str, object]:
    session = str(arguments.get("session", "default"))
    server = str(arguments.get("server", "")).strip()
    inputs = {
        "session": session,
        "game_dir": arguments.get("game_dir"),
        "username": arguments.get("username", "Player"),
        "version": arguments.get("version", "1.21.8"),
        "server": server,
        "java": arguments.get("java"),
        "width": arguments.get("width"),
        "height": arguments.get("height"),
        "min_memory": arguments.get("min_memory", 512),
        "max_memory": arguments.get("max_memory", 4096),
        "dry_run": bool(arguments.get("dry_run", False)),
        "execution": arguments.get("execution"),
    }
    if not server:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="minecraft_join_server",
            summary="Server is required.",
            inputs=inputs,
            stderr="Pass `server`.",
            exit_code=2,
        )
    command = ["join-server", "--session", session, "--username", str(inputs["username"]), "--version", str(inputs["version"]), "--server", server]
    if inputs["game_dir"]:
        command.extend(["--game-dir", str(inputs["game_dir"])])
    if inputs["java"]:
        command.extend(["--java", str(inputs["java"])])
    if inputs["width"] is not None:
        command.extend(["--width", str(int(inputs["width"]))])
    if inputs["height"] is not None:
        command.extend(["--height", str(int(inputs["height"]))])
    command.extend(["--min-memory", str(int(inputs["min_memory"]))])
    command.extend(["--max-memory", str(int(inputs["max_memory"]))])
    if inputs["dry_run"]:
        command.append("--dry-run")
    return _run_action("minecraft_join_server", command, inputs, status_args={"session": session, "game_dir": inputs["game_dir"]})


def minecraft_join_world(arguments: dict[str, object]) -> dict[str, object]:
    session = str(arguments.get("session", "default"))
    world = str(arguments.get("world", "")).strip()
    inputs = {
        "session": session,
        "game_dir": arguments.get("game_dir"),
        "username": arguments.get("username", "Player"),
        "version": arguments.get("version", "1.21.8"),
        "world": world,
        "java": arguments.get("java"),
        "width": arguments.get("width"),
        "height": arguments.get("height"),
        "min_memory": arguments.get("min_memory", 512),
        "max_memory": arguments.get("max_memory", 4096),
        "dry_run": bool(arguments.get("dry_run", False)),
        "execution": arguments.get("execution"),
    }
    if not world:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="minecraft_join_world",
            summary="World is required.",
            inputs=inputs,
            stderr="Pass `world`.",
            exit_code=2,
        )
    command = ["join-world", "--session", session, "--username", str(inputs["username"]), "--version", str(inputs["version"]), "--world", world]
    if inputs["game_dir"]:
        command.extend(["--game-dir", str(inputs["game_dir"])])
    if inputs["java"]:
        command.extend(["--java", str(inputs["java"])])
    if inputs["width"] is not None:
        command.extend(["--width", str(int(inputs["width"]))])
    if inputs["height"] is not None:
        command.extend(["--height", str(int(inputs["height"]))])
    command.extend(["--min-memory", str(int(inputs["min_memory"]))])
    command.extend(["--max-memory", str(int(inputs["max_memory"]))])
    if inputs["dry_run"]:
        command.append("--dry-run")
    return _run_action("minecraft_join_world", command, inputs, status_args={"session": session, "game_dir": inputs["game_dir"]})


def minecraft_focus(arguments: dict[str, object]) -> dict[str, object]:
    inputs = {"execution": arguments.get("execution")}
    return _run_action("minecraft_focus", ["focus"], inputs)


def minecraft_send_text(arguments: dict[str, object]) -> dict[str, object]:
    text = str(arguments.get("text", ""))
    newline = bool(arguments.get("newline", False))
    inputs = {"text": text, "newline": newline, "execution": arguments.get("execution")}
    if not text:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="minecraft_send_text",
            summary="Text is required.",
            inputs=inputs,
            stderr="Pass `text`.",
            exit_code=2,
        )
    command = ["send-text", "--text", text]
    if newline:
        command.append("--newline")
    return _run_action("minecraft_send_text", command, inputs)


def minecraft_chat(arguments: dict[str, object]) -> dict[str, object]:
    text = str(arguments.get("text", ""))
    inputs = {"text": text, "execution": arguments.get("execution")}
    if not text:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="minecraft_chat",
            summary="Text is required.",
            inputs=inputs,
            stderr="Pass `text`.",
            exit_code=2,
        )
    return _run_action("minecraft_chat", ["chat", "--text", text], inputs)


def minecraft_command(arguments: dict[str, object]) -> dict[str, object]:
    text = str(arguments.get("text", ""))
    inputs = {"text": text, "execution": arguments.get("execution")}
    if not text:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="minecraft_command",
            summary="Text is required.",
            inputs=inputs,
            stderr="Pass `text`.",
            exit_code=2,
        )
    return _run_action("minecraft_command", ["command", "--text", text], inputs)


def minecraft_screenshot(arguments: dict[str, object]) -> dict[str, object]:
    output = str(arguments.get("output", "")).strip()
    inputs = {"output": output, "execution": arguments.get("execution")}
    if not output:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="minecraft_screenshot",
            summary="Output path is required.",
            inputs=inputs,
            stderr="Pass `output`.",
            exit_code=2,
        )
    return _run_action("minecraft_screenshot", ["screenshot", "--output", output], inputs)


def minecraft_read_log(arguments: dict[str, object]) -> dict[str, object]:
    session = str(arguments.get("session", "default"))
    which = str(arguments.get("which", "both"))
    tail = int(arguments.get("tail", 80))
    follow = bool(arguments.get("follow", False))
    inputs = {
        "session": session,
        "game_dir": arguments.get("game_dir"),
        "which": which,
        "tail": tail,
        "follow": follow,
        "execution": arguments.get("execution"),
    }
    command = ["read-log", "--session", session, "--which", which, "--tail", str(tail)]
    if arguments.get("game_dir"):
        command.extend(["--game-dir", str(arguments["game_dir"])])
    if follow:
        command.append("--follow")
    return _run_action("minecraft_read_log", command, inputs, status_args={"session": session, "game_dir": arguments.get("game_dir")})


def minecraft_stop(arguments: dict[str, object]) -> dict[str, object]:
    session = str(arguments.get("session", "default"))
    inputs = {"session": session, "execution": arguments.get("execution")}
    return _run_action("minecraft_stop", ["stop", "--session", session], inputs, status_args={"session": session})


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        instructions="OpenCROW Minecraft async I/O server.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return metadata about the OpenCROW Minecraft async I/O server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="OpenCROW Minecraft async I/O server information returned.",
                    operations=OPERATIONS,
                ),
            ),
            MCPTool(
                name="toolbox_verify",
                description="Return dependency status for the OpenCROW Minecraft async I/O server.",
                input_schema={"type": "object", "properties": {}},
                handler=toolbox_verify,
            ),
            MCPTool(
                name="toolbox_capabilities",
                description="Return the structured operations exposed by the OpenCROW Minecraft async I/O server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_capabilities_handler(TOOLBOX_ID, OPERATIONS),
            ),
            MCPTool(
                name="minecraft_status",
                description="Return structured status about the installed Minecraft client and managed session.",
                input_schema={"type": "object", "properties": {"session": {"type": "string"}, "game_dir": {"type": "string"}, "execution": {"type": "object"}}},
                handler=minecraft_status,
            ),
            MCPTool(
                name="minecraft_launch",
                description="Launch the installed Minecraft client with typed options.",
                input_schema={"type": "object", "properties": {"session": {"type": "string"}, "game_dir": {"type": "string"}, "backend": {"type": "string"}, "username": {"type": "string"}, "version": {"type": "string"}, "server": {"type": "string"}, "world": {"type": "string"}, "java": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"}, "min_memory": {"type": "integer"}, "max_memory": {"type": "integer"}, "instance": {"type": "string"}, "dry_run": {"type": "boolean"}, "execution": {"type": "object"}}},
                handler=minecraft_launch,
            ),
            MCPTool(
                name="minecraft_join_server",
                description="Launch directly into a multiplayer server via quick play.",
                input_schema={"type": "object", "required": ["server"], "properties": {"session": {"type": "string"}, "game_dir": {"type": "string"}, "username": {"type": "string"}, "version": {"type": "string"}, "server": {"type": "string"}, "java": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"}, "min_memory": {"type": "integer"}, "max_memory": {"type": "integer"}, "dry_run": {"type": "boolean"}, "execution": {"type": "object"}}},
                handler=minecraft_join_server,
            ),
            MCPTool(
                name="minecraft_join_world",
                description="Launch directly into a local world via quick play.",
                input_schema={"type": "object", "required": ["world"], "properties": {"session": {"type": "string"}, "game_dir": {"type": "string"}, "username": {"type": "string"}, "version": {"type": "string"}, "world": {"type": "string"}, "java": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"}, "min_memory": {"type": "integer"}, "max_memory": {"type": "integer"}, "dry_run": {"type": "boolean"}, "execution": {"type": "object"}}},
                handler=minecraft_join_world,
            ),
            MCPTool(
                name="minecraft_focus",
                description="Focus the Minecraft window on the current X11 display.",
                input_schema={"type": "object", "properties": {"execution": {"type": "object"}}},
                handler=minecraft_focus,
            ),
            MCPTool(
                name="minecraft_send_text",
                description="Type raw text into the focused Minecraft input field.",
                input_schema={"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}, "newline": {"type": "boolean"}, "execution": {"type": "object"}}},
                handler=minecraft_send_text,
            ),
            MCPTool(
                name="minecraft_chat",
                description="Open chat and send a message.",
                input_schema={"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}, "execution": {"type": "object"}}},
                handler=minecraft_chat,
            ),
            MCPTool(
                name="minecraft_command",
                description="Open slash-command mode and send a command.",
                input_schema={"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}, "execution": {"type": "object"}}},
                handler=minecraft_command,
            ),
            MCPTool(
                name="minecraft_screenshot",
                description="Capture the current Minecraft window to a PNG file.",
                input_schema={"type": "object", "required": ["output"], "properties": {"output": {"type": "string"}, "execution": {"type": "object"}}},
                handler=minecraft_screenshot,
            ),
            MCPTool(
                name="minecraft_read_log",
                description="Read or follow the relevant Minecraft logs.",
                input_schema={"type": "object", "properties": {"session": {"type": "string"}, "game_dir": {"type": "string"}, "which": {"type": "string"}, "tail": {"type": "integer"}, "follow": {"type": "boolean"}, "execution": {"type": "object"}}},
                handler=minecraft_read_log,
            ),
            MCPTool(
                name="minecraft_stop",
                description="Stop the managed Minecraft session.",
                input_schema={"type": "object", "properties": {"session": {"type": "string"}, "execution": {"type": "object"}}},
                handler=minecraft_stop,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
