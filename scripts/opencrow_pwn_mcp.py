#!/usr/bin/env python3
"""OpenCROW pwn toolbox MCP server."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from opencrow_ctf_mcp_common import run_conda_python
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


SERVER_NAME = "opencrow-pwn-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "opencrow-pwn-toolbox"
DISPLAY_NAME = "OpenCROW Pwn Toolbox"
OPERATIONS = [
    {"name": "pwn_python", "description": "Run typed inline Python or a Python file inside the managed ctf environment."},
    {"name": "pwn_checksec", "description": "Inspect ELF mitigations and binary metadata for a target."},
    {"name": "pwn_cyclic", "description": "Generate or locate pwntools cyclic patterns with typed inputs."},
    {"name": "pwn_patch_binary", "description": "Copy and patch an ELF binary with patchelf using typed interpreter and rpath options."},
    {"name": "pwn_one_gadget", "description": "Search a libc for one-shot gadgets with typed one_gadget options."},
]
PYTHON_MODULES = ["pwn"]
SYSTEM_DEPENDENCIES = [
    "gdb",
    "pwndbg",
    "gdbserver",
    "pwninit",
    "seccomp-tools",
    "one_gadget",
    "checksec",
    "patchelf",
    "qemu-aarch64",
    "qemu-aarch64-static",
    "qemu-arm",
    "qemu-x86_64",
    "gcc",
    "nasm",
]


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
        summary="Pwn toolbox dependency status returned.",
        inputs={"env_name": env_name},
        observations=observations,
        next_steps=[
            "Use `pwn_checksec` first on a target binary.",
            "Use `pwn_python` for exploit and helper scripts in the ctf environment.",
        ],
    )


def pwn_python(arguments: dict[str, object]) -> dict[str, object]:
    env_name = _env_name(arguments)
    code = arguments.get("code")
    path_value = arguments.get("path")
    path_text = str(path_value).strip() if path_value is not None else None
    inputs = {"env_name": env_name, "path": path_text, "has_code": code is not None}
    if (code is None) == (path_value is None):
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_python",
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
                operation="pwn_python",
                summary="A non-empty file path is required.",
                inputs=inputs,
                stderr="Pass a non-empty `path` or use `code`.",
                exit_code=2,
            )
        path = Path(path_text).expanduser().resolve()
        if not path.exists():
            return _path_error("pwn_python", path, inputs)
        artifacts.append(str(path))
        result = run_conda_python(env_name=env_name, path=path, cwd=cwd, timeout_sec=timeout_sec, prefix="opencrow-pwn-")
    else:
        result = run_conda_python(env_name=env_name, code=str(code), cwd=cwd, timeout_sec=timeout_sec, prefix="opencrow-pwn-")

    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_python",
            summary="Pwn Python execution completed.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[{"env_name": env_name}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Use `pwn_checksec`, `pwn_cyclic`, or `pwn_patch_binary` for common binary-exploitation setup tasks."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="pwn_python",
        summary="Pwn Python execution failed.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def pwn_checksec(arguments: dict[str, object]) -> dict[str, object]:
    raw_path = str(arguments.get("path", "")).strip()
    inputs = {"path": raw_path}
    if not raw_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_checksec",
            summary="A target path is required.",
            inputs=inputs,
            stderr="Pass `path`.",
            exit_code=2,
        )
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        return _path_error("pwn_checksec", path, inputs)
    if not command_exists("checksec"):
        return missing_dependency_envelope(TOOLBOX_ID, "pwn_checksec", "checksec", inputs)

    cwd, timeout_sec = default_execution(arguments)
    checksec_result = run_command(["checksec", f"--file={path}"], cwd=cwd, timeout_sec=timeout_sec)
    outputs = [f"$ {checksec_result['command']}\n{checksec_result['stdout']}".strip()]
    stderr_parts = [checksec_result["stderr"]] if checksec_result["stderr"] else []
    observations = [{"tool": "checksec", "ok": checksec_result["ok"]}]
    commands = [checksec_result["command"]]
    if command_exists("file"):
        file_result = run_command(["file", "-b", str(path)], cwd=cwd, timeout_sec=timeout_sec)
        commands.append(file_result["command"])
        outputs.append(f"$ {file_result['command']}\n{file_result['stdout']}".strip())
        if file_result["stderr"]:
            stderr_parts.append(file_result["stderr"])
        observations.append({"tool": "file", "ok": file_result["ok"], "description": file_result["stdout"].strip()})

    if checksec_result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_checksec",
            summary=f"Binary triage completed for {path.name}.",
            inputs=inputs,
            artifacts=[str(path)],
            observations=observations,
            command=" && ".join(commands),
            stdout="\n\n".join(part for part in outputs if part),
            stderr="\n".join(part for part in stderr_parts if part),
            exit_code=checksec_result["exit_code"],
            next_steps=["Use `pwn_cyclic` for crash offset work or `pwn_patch_binary` when the runtime needs patching."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="pwn_checksec",
        summary=f"Binary triage failed for {path.name}.",
        inputs=inputs,
        command=checksec_result["command"],
        stdout=checksec_result["stdout"],
        stderr="\n".join(part for part in stderr_parts if part),
        exit_code=checksec_result["exit_code"],
        observations=observations,
    )


def pwn_cyclic(arguments: dict[str, object]) -> dict[str, object]:
    env_name = _env_name(arguments)
    action = str(arguments.get("action", "generate"))
    n = int(arguments.get("word_size", 4))
    inputs = {
        "env_name": env_name,
        "action": action,
        "length": arguments.get("length"),
        "value": arguments.get("value"),
        "value_format": arguments.get("value_format", "string"),
        "word_size": n,
    }
    if action not in {"generate", "find"}:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_cyclic",
            summary=f"Unsupported cyclic action: {action}",
            inputs=inputs,
            stderr="Supported values are `generate` and `find`.",
            exit_code=2,
        )

    if action == "generate":
        length = arguments.get("length")
        if length is None:
            return error_envelope(
                toolbox=TOOLBOX_ID,
                operation="pwn_cyclic",
                summary="`length` is required for cyclic generation.",
                inputs=inputs,
                stderr="Pass `length`.",
                exit_code=2,
            )
        code = (
            "from pwn import cyclic\n"
            f"print(cyclic({int(length)}, n={n}).decode('latin-1'))\n"
        )
    else:
        value = arguments.get("value")
        value_format = str(arguments.get("value_format", "string"))
        if value is None:
            return error_envelope(
                toolbox=TOOLBOX_ID,
                operation="pwn_cyclic",
                summary="`value` is required for cyclic offset lookup.",
                inputs=inputs,
                stderr="Pass `value` and optionally `value_format`.",
                exit_code=2,
            )
        if value_format == "hex":
            code = (
                "from pwn import cyclic_find\n"
                f"print(cyclic_find(int({value!r}, 16), n={n}))\n"
            )
        elif value_format == "int":
            code = (
                "from pwn import cyclic_find\n"
                f"print(cyclic_find(int({value!r}, 0), n={n}))\n"
            )
        else:
            code = (
                "from pwn import cyclic_find\n"
                f"print(cyclic_find({str(value)!r}.encode('latin-1'), n={n}))\n"
            )

    cwd, timeout_sec = default_execution(arguments)
    result = run_conda_python(env_name=env_name, code=code, cwd=cwd, timeout_sec=timeout_sec, prefix="opencrow-pwn-")
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_cyclic",
            summary=f"Pwntools cyclic {action} completed.",
            inputs=inputs,
            observations=[{"env_name": env_name, "action": action}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="pwn_cyclic",
        summary=f"Pwntools cyclic {action} failed.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def pwn_patch_binary(arguments: dict[str, object]) -> dict[str, object]:
    raw_path = str(arguments.get("path", "")).strip()
    raw_output_path = str(arguments.get("output_path", "")).strip()
    interpreter = arguments.get("set_interpreter")
    rpath = arguments.get("set_rpath")
    inputs = {
        "path": raw_path,
        "output_path": raw_output_path,
        "set_interpreter": interpreter,
        "set_rpath": rpath,
    }
    if not raw_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_patch_binary",
            summary="A source binary path is required.",
            inputs=inputs,
            stderr="Pass `path`.",
            exit_code=2,
        )
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        return _path_error("pwn_patch_binary", path, inputs)
    if not raw_output_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_patch_binary",
            summary="`output_path` is required.",
            inputs=inputs,
            stderr="Pass `output_path`.",
            exit_code=2,
        )
    output_path = Path(raw_output_path).expanduser().resolve()
    if interpreter is None and rpath is None:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_patch_binary",
            summary="At least one patchelf change is required.",
            inputs=inputs,
            stderr="Pass `set_interpreter`, `set_rpath`, or both.",
            exit_code=2,
        )
    if not command_exists("patchelf"):
        return missing_dependency_envelope(TOOLBOX_ID, "pwn_patch_binary", "patchelf", inputs)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, output_path)
    command = ["patchelf"]
    if interpreter is not None:
        command.extend(["--set-interpreter", str(interpreter)])
    if rpath is not None:
        command.extend(["--set-rpath", str(rpath)])
    command.append(str(output_path))

    cwd, timeout_sec = default_execution(arguments)
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_patch_binary",
            summary=f"Patched copy written to {output_path.name}.",
            inputs=inputs,
            artifacts=[str(path), str(output_path)],
            observations=[{"copied_from": str(path), "patched_to": str(output_path)}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Run `pwn_checksec` or your exploit script against the patched copy."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="pwn_patch_binary",
        summary=f"Failed to patch {output_path.name}.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def pwn_one_gadget(arguments: dict[str, object]) -> dict[str, object]:
    raw_libc_path = str(arguments.get("libc_path", "")).strip()
    level = arguments.get("level")
    raw = bool(arguments.get("raw", False))
    inputs = {"libc_path": raw_libc_path, "level": level, "raw": raw}
    if not raw_libc_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_one_gadget",
            summary="A libc path is required.",
            inputs=inputs,
            stderr="Pass `libc_path`.",
            exit_code=2,
        )
    libc_path = Path(raw_libc_path).expanduser().resolve()
    if not libc_path.exists():
        return _path_error("pwn_one_gadget", libc_path, inputs)
    if not command_exists("one_gadget"):
        return missing_dependency_envelope(TOOLBOX_ID, "pwn_one_gadget", "one_gadget", inputs)

    command = ["one_gadget"]
    if level is not None:
        command.extend(["--level", str(int(level))])
    if raw:
        command.append("--raw")
    command.append(str(libc_path))

    cwd, timeout_sec = default_execution(arguments)
    result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    observations = [{"gadget_line_count": len([line for line in result["stdout"].splitlines() if line.strip()])}]
    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="pwn_one_gadget",
            summary=f"one_gadget search completed for {libc_path.name}.",
            inputs=inputs,
            artifacts=[str(libc_path)],
            observations=observations,
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Cross-check the reported gadget constraints against your exploit state."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="pwn_one_gadget",
        summary=f"one_gadget search failed for {libc_path.name}.",
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
        instructions="OpenCROW pwn toolbox MCP server.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return metadata about the OpenCROW pwn toolbox MCP server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="OpenCROW pwn toolbox information returned.",
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
                description="Return dependency status for the OpenCROW pwn toolbox MCP server.",
                input_schema={"type": "object", "properties": {"env_name": {"type": "string"}}},
                handler=toolbox_verify,
            ),
            MCPTool(
                name="toolbox_capabilities",
                description="Return the structured operations exposed by the OpenCROW pwn toolbox MCP server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_capabilities_handler(TOOLBOX_ID, OPERATIONS),
            ),
            MCPTool(
                name="pwn_python",
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
                handler=pwn_python,
            ),
            MCPTool(
                name="pwn_checksec",
                description="Inspect ELF mitigations and binary metadata for a target.",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}, "execution": {"type": "object"}}},
                handler=pwn_checksec,
            ),
            MCPTool(
                name="pwn_cyclic",
                description="Generate or locate pwntools cyclic patterns with typed inputs.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "env_name": {"type": "string"},
                        "action": {"type": "string"},
                        "length": {"type": "integer"},
                        "value": {"type": ["string", "integer"]},
                        "value_format": {"type": "string"},
                        "word_size": {"type": "integer"},
                        "execution": {"type": "object"},
                    },
                },
                handler=pwn_cyclic,
            ),
            MCPTool(
                name="pwn_patch_binary",
                description="Copy and patch an ELF binary with patchelf using typed interpreter and rpath options.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "output_path": {"type": "string"},
                        "set_interpreter": {"type": "string"},
                        "set_rpath": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                },
                handler=pwn_patch_binary,
            ),
            MCPTool(
                name="pwn_one_gadget",
                description="Search a libc for one-shot gadgets with typed one_gadget options.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "libc_path": {"type": "string"},
                        "level": {"type": "integer"},
                        "raw": {"type": "boolean"},
                        "execution": {"type": "object"},
                    },
                },
                handler=pwn_one_gadget,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
