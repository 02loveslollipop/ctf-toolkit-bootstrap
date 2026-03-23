#!/usr/bin/env python3
"""OpenCROW reversing toolbox MCP server."""

from __future__ import annotations

import sys
from pathlib import Path

from opencrow_ctf_mcp_common import conda_command_exists, conda_run, run_conda_python
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


SERVER_NAME = "opencrow-reversing-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "opencrow-reversing-toolbox"
DISPLAY_NAME = "OpenCROW Reversing Toolbox"
OPERATIONS = [
    {"name": "reversing_python", "description": "Run typed inline Python or a Python file inside the managed ctf environment."},
    {"name": "reversing_disassemble", "description": "Disassemble a target through objdump or radare2 with typed options."},
    {"name": "reversing_trace", "description": "Trace a target through strace or ltrace with typed arguments."},
    {"name": "reversing_binwalk", "description": "Scan or extract embedded blobs with binwalk."},
    {"name": "reversing_gadget_search", "description": "Search for gadgets with ropper or ROPgadget using typed inputs."},
]
PYTHON_MODULES = ["angr", "claripy", "capstone", "unicorn", "keystone", "ropper", "r2pipe", "lief", "qiling"]
SYSTEM_DEPENDENCIES = ["ghidra-headless", "r2", "objdump", "strace", "ltrace", "binwalk"]
CONDA_COMMANDS = ["ROPgadget", "ropper", "frida-ps"]


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


def _binwalk_command() -> str:
    system_binwalk = Path("/usr/bin/binwalk")
    if system_binwalk.exists():
        return str(system_binwalk)
    return "binwalk"


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
    observations.extend(
        {"dependency": dependency, "available": conda_command_exists(env_name, dependency), "type": "conda-command", "env_name": env_name}
        for dependency in CONDA_COMMANDS
    )
    observations.append({"dependency": "conda", "available": command_exists("conda"), "type": "system-command"})
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="Reversing toolbox dependency status returned.",
        inputs={"env_name": env_name},
        observations=observations,
        next_steps=[
            "Use `reversing_disassemble` for quick static triage.",
            "Use `reversing_python` for angr, capstone, qiling, or other Python-driven analysis.",
        ],
    )


def reversing_python(arguments: dict[str, object]) -> dict[str, object]:
    env_name = _env_name(arguments)
    code = arguments.get("code")
    path_value = arguments.get("path")
    path_text = str(path_value).strip() if path_value is not None else None
    inputs = {"env_name": env_name, "path": path_text, "has_code": code is not None}
    if (code is None) == (path_value is None):
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_python",
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
                operation="reversing_python",
                summary="A non-empty file path is required.",
                inputs=inputs,
                stderr="Pass a non-empty `path` or use `code`.",
                exit_code=2,
            )
        path = Path(path_text).expanduser().resolve()
        if not path.exists():
            return _path_error("reversing_python", path, inputs)
        artifacts.append(str(path))
        result = run_conda_python(env_name=env_name, path=path, cwd=cwd, timeout_sec=timeout_sec, prefix="opencrow-reversing-")
    else:
        result = run_conda_python(env_name=env_name, code=str(code), cwd=cwd, timeout_sec=timeout_sec, prefix="opencrow-reversing-")

    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_python",
            summary="Reversing Python execution completed.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[{"env_name": env_name}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Use `reversing_disassemble`, `reversing_trace`, or `reversing_gadget_search` for native helper workflows."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="reversing_python",
        summary="Reversing Python execution failed.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def reversing_disassemble(arguments: dict[str, object]) -> dict[str, object]:
    raw_path = str(arguments.get("path", "")).strip()
    backend = str(arguments.get("backend", "objdump"))
    inputs = {
        "path": raw_path,
        "backend": backend,
        "section": arguments.get("section"),
        "start_address": arguments.get("start_address"),
        "stop_address": arguments.get("stop_address"),
        "intel_syntax": bool(arguments.get("intel_syntax", True)),
        "instruction_count": int(arguments.get("instruction_count", 64)),
        "address": arguments.get("address"),
    }
    if not raw_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_disassemble",
            summary="A target path is required.",
            inputs=inputs,
            stderr="Pass `path`.",
            exit_code=2,
        )
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        return _path_error("reversing_disassemble", path, inputs)

    cwd, timeout_sec = default_execution(arguments)
    if backend == "objdump":
        if not command_exists("objdump"):
            return missing_dependency_envelope(TOOLBOX_ID, "reversing_disassemble", "objdump", inputs)
        command = ["objdump", "-d"]
        if inputs["intel_syntax"]:
            command.append("-Mintel")
        if inputs["section"]:
            command.extend(["-j", str(inputs["section"])])
        if inputs["start_address"]:
            command.append(f"--start-address={inputs['start_address']}")
        if inputs["stop_address"]:
            command.append(f"--stop-address={inputs['stop_address']}")
        command.append(str(path))
        result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    elif backend == "radare2":
        if not command_exists("r2"):
            return missing_dependency_envelope(TOOLBOX_ID, "reversing_disassemble", "r2", inputs)
        commands = ["aaa"]
        if inputs["address"]:
            commands.append(f"s {inputs['address']}")
        commands.append(f"pd {inputs['instruction_count']}")
        result = run_command(["r2", "-q", "-c", "; ".join(commands), str(path)], cwd=cwd, timeout_sec=timeout_sec)
    else:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_disassemble",
            summary=f"Unsupported disassembly backend: {backend}",
            inputs=inputs,
            stderr="Supported values are `objdump` and `radare2`.",
            exit_code=2,
        )

    observations = [{"backend": backend, "line_count": len([line for line in result["stdout"].splitlines() if line.strip()])}]
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_disassemble",
            summary=f"{backend} disassembly completed for {path.name}.",
            inputs=inputs,
            artifacts=[str(path)],
            observations=observations,
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Use `reversing_trace` or `reversing_gadget_search` if static disassembly is not enough."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="reversing_disassemble",
        summary=f"{backend} disassembly failed for {path.name}.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        observations=observations,
    )


def reversing_trace(arguments: dict[str, object]) -> dict[str, object]:
    raw_path = str(arguments.get("path", "")).strip()
    backend = str(arguments.get("backend", "strace"))
    argv = [str(item) for item in arguments.get("argv", [])] if isinstance(arguments.get("argv"), list) else []
    trace_children = bool(arguments.get("trace_children", False))
    output_path_value = arguments.get("output_path")
    inputs = {
        "path": raw_path,
        "backend": backend,
        "argv": argv,
        "trace_children": trace_children,
        "output_path": str(output_path_value) if output_path_value is not None else None,
    }
    if not raw_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_trace",
            summary="A target path is required.",
            inputs=inputs,
            stderr="Pass `path`.",
            exit_code=2,
        )
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        return _path_error("reversing_trace", path, inputs)
    if backend not in {"strace", "ltrace"}:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_trace",
            summary=f"Unsupported trace backend: {backend}",
            inputs=inputs,
            stderr="Supported values are `strace` and `ltrace`.",
            exit_code=2,
        )
    if not command_exists(backend):
        return missing_dependency_envelope(TOOLBOX_ID, "reversing_trace", backend, inputs)

    command = [backend]
    if trace_children:
        command.append("-f")
    artifacts = [str(path)]
    if output_path_value is not None:
        output_path = Path(str(output_path_value)).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        artifacts.append(str(output_path))
        command.extend(["-o", str(output_path)])
    command.extend([str(path), *argv])

    cwd, timeout_sec = default_execution(arguments)
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    observations = [{"backend": backend, "trace_children": trace_children}]
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_trace",
            summary=f"{backend} trace completed for {path.name}.",
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
        operation="reversing_trace",
        summary=f"{backend} trace failed for {path.name}.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        observations=observations,
    )


def reversing_binwalk(arguments: dict[str, object]) -> dict[str, object]:
    raw_path = str(arguments.get("path", "")).strip()
    extract = bool(arguments.get("extract", False))
    output_dir_value = arguments.get("output_dir")
    inputs = {
        "path": raw_path,
        "extract": extract,
        "output_dir": str(output_dir_value) if output_dir_value is not None else None,
    }
    if not raw_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_binwalk",
            summary="A target path is required.",
            inputs=inputs,
            stderr="Pass `path`.",
            exit_code=2,
        )
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        return _path_error("reversing_binwalk", path, inputs)
    if not command_exists("binwalk"):
        return missing_dependency_envelope(TOOLBOX_ID, "reversing_binwalk", "binwalk", inputs)

    command = [_binwalk_command()]
    artifacts = [str(path)]
    if extract:
        command.append("-e")
        if output_dir_value is not None:
            output_dir = Path(str(output_dir_value)).expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            command.extend(["-C", str(output_dir)])
            artifacts.append(str(output_dir))
    command.append(str(path))

    cwd, timeout_sec = default_execution(arguments)
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    observations = [{"extract": extract}]
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_binwalk",
            summary=f"binwalk {'extraction' if extract else 'scan'} completed for {path.name}.",
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
        operation="reversing_binwalk",
        summary=f"binwalk {'extraction' if extract else 'scan'} failed for {path.name}.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        observations=observations,
    )


def reversing_gadget_search(arguments: dict[str, object]) -> dict[str, object]:
    env_name = _env_name(arguments)
    raw_path = str(arguments.get("path", "")).strip()
    backend = str(arguments.get("backend", "ropper"))
    search = arguments.get("search")
    badbytes = arguments.get("badbytes")
    inputs = {
        "env_name": env_name,
        "path": raw_path,
        "backend": backend,
        "search": search,
        "badbytes": badbytes,
    }
    if not raw_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_gadget_search",
            summary="A target path is required.",
            inputs=inputs,
            stderr="Pass `path`.",
            exit_code=2,
        )
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        return _path_error("reversing_gadget_search", path, inputs)

    cwd, timeout_sec = default_execution(arguments)
    if backend == "ropper":
        if not conda_command_exists(env_name, "ropper"):
            return missing_dependency_envelope(TOOLBOX_ID, "reversing_gadget_search", "ropper (ctf env)", inputs)
        command = ["ropper", "--file", str(path), "--nocolor"]
        if search:
            command.extend(["--search", str(search)])
        if badbytes:
            command.extend(["--badbytes", str(badbytes)])
        result = conda_run(command, env_name=env_name, cwd=cwd, timeout_sec=timeout_sec)
    elif backend == "ROPgadget":
        if not conda_command_exists(env_name, "ROPgadget"):
            return missing_dependency_envelope(TOOLBOX_ID, "reversing_gadget_search", "ROPgadget (ctf env)", inputs)
        command = ["ROPgadget", "--binary", str(path)]
        if search:
            command.extend(["--only", str(search)])
        if badbytes:
            command.extend(["--badbytes", str(badbytes)])
        result = conda_run(command, env_name=env_name, cwd=cwd, timeout_sec=timeout_sec)
    else:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_gadget_search",
            summary=f"Unsupported gadget backend: {backend}",
            inputs=inputs,
            stderr="Supported values are `ropper` and `ROPgadget`.",
            exit_code=2,
        )

    observations = [{"backend": backend, "line_count": len([line for line in result["stdout"].splitlines() if line.strip()])}]
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_gadget_search",
            summary=f"{backend} gadget search completed for {path.name}.",
            inputs=inputs,
            artifacts=[str(path)],
            observations=observations,
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Feed the gadget search output into your exploit or analysis workflow."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="reversing_gadget_search",
        summary=f"{backend} gadget search failed for {path.name}.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        observations=observations,
    )


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        instructions="OpenCROW reversing toolbox MCP server.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return metadata about the OpenCROW reversing toolbox MCP server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="OpenCROW reversing toolbox information returned.",
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
                description="Return dependency status for the OpenCROW reversing toolbox MCP server.",
                input_schema={"type": "object", "properties": {"env_name": {"type": "string"}}},
                handler=toolbox_verify,
            ),
            MCPTool(
                name="toolbox_capabilities",
                description="Return the structured operations exposed by the OpenCROW reversing toolbox MCP server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_capabilities_handler(TOOLBOX_ID, OPERATIONS),
            ),
            MCPTool(
                name="reversing_python",
                description="Run typed inline Python or a Python file inside the managed ctf environment.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "env_name": {"type": "string"},
                        "code": {"type": "string"},
                        "path": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                },
                handler=reversing_python,
            ),
            MCPTool(
                name="reversing_disassemble",
                description="Disassemble a target through objdump or radare2 with typed options.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "backend": {"type": "string"},
                        "section": {"type": "string"},
                        "start_address": {"type": ["string", "integer"]},
                        "stop_address": {"type": ["string", "integer"]},
                        "intel_syntax": {"type": "boolean"},
                        "instruction_count": {"type": "integer"},
                        "address": {"type": ["string", "integer"]},
                        "execution": {"type": "object"},
                    },
                },
                handler=reversing_disassemble,
            ),
            MCPTool(
                name="reversing_trace",
                description="Trace a target through strace or ltrace with typed arguments.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "backend": {"type": "string"},
                        "argv": {"type": "array"},
                        "trace_children": {"type": "boolean"},
                        "output_path": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                },
                handler=reversing_trace,
            ),
            MCPTool(
                name="reversing_binwalk",
                description="Scan or extract embedded blobs with binwalk.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "extract": {"type": "boolean"},
                        "output_dir": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                },
                handler=reversing_binwalk,
            ),
            MCPTool(
                name="reversing_gadget_search",
                description="Search for gadgets with ropper or ROPgadget using typed inputs.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "env_name": {"type": "string"},
                        "path": {"type": "string"},
                        "backend": {"type": "string"},
                        "search": {"type": "string"},
                        "badbytes": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                },
                handler=reversing_gadget_search,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
