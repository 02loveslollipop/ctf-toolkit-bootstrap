#!/usr/bin/env python3
"""OpenCROW reversing toolbox MCP server."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from opencrow_ctf_mcp_common import conda_command_exists, conda_run, run_conda_python
from opencrow_mcp_core import (
    MCPTool,
    StdioMCPServer,
    append_jsonl,
    command_exists,
    conda_module_available,
    default_execution,
    error_envelope,
    execution_transcript_path,
    make_toolbox_capabilities_handler,
    make_toolbox_info_handler,
    make_toolbox_self_test_handler,
    missing_dependency_envelope,
    normalize_path,
    run_command,
    success_envelope,
    utc_now_iso,
)


JSON = dict[str, Any]
SCRIPT_DIR = Path(__file__).resolve().parent
REVERSING_WORKER = SCRIPT_DIR / "opencrow_reversing_worker.py"
GHIDRA_SCRIPT_DIR = SCRIPT_DIR / "ghidra"
GHIDRA_SCRIPT_NAME = "OpenCrowDecompileFunction.java"
TRANSCRIPT_PREVIEW_LIMIT = 400

SERVER_NAME = "opencrow-reversing-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "opencrow-reversing-toolbox"
DISPLAY_NAME = "OpenCROW Reversing Toolbox"
OPERATIONS = [
    {"name": "reversing_python", "description": "Run typed inline Python or a Python file inside the managed ctf environment."},
    {"name": "reversing_disassemble", "description": "Disassemble a target through objdump or radare2 with typed options."},
    {"name": "reversing_decompile", "description": "Decompile a function with Ghidra headless using a typed function target."},
    {"name": "reversing_read_data", "description": "Read data from a binary by virtual address using typed decode modes."},
    {"name": "reversing_emulate_blob", "description": "Emulate a blob or extracted code region with Unicorn using typed execution inputs."},
    {"name": "reversing_symbolic_execute", "description": "Run a typed angr symbolic execution workflow against a function or blob."},
    {"name": "reversing_trace", "description": "Trace a target through strace or ltrace with typed arguments."},
    {"name": "reversing_binwalk", "description": "Scan or extract embedded blobs with binwalk."},
    {"name": "reversing_gadget_search", "description": "Search for gadgets with ropper or ROPgadget using typed inputs."},
]
PYTHON_MODULES = ["angr", "claripy", "capstone", "unicorn", "keystone", "ropper", "r2pipe", "lief", "qiling"]
SYSTEM_DEPENDENCIES = ["ghidra-headless", "r2", "objdump", "strace", "ltrace", "binwalk"]
CONDA_COMMANDS = ["ROPgadget", "ropper", "frida-ps"]


def _env_name(arguments: JSON) -> str:
    return str(arguments.get("env_name", "ctf"))


def _path_error(operation: str, path: Path, inputs: JSON) -> JSON:
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation=operation,
        summary=f"Input file does not exist: {path}",
        inputs=inputs,
        stderr=f"Missing file: {path}",
        exit_code=2,
    )


def _preview_text(value: str, limit: int = TRANSCRIPT_PREVIEW_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]..."


def _transcript_wrapper(handler: Callable[[JSON], JSON]) -> Callable[[JSON], JSON]:
    def wrapped(arguments: JSON) -> JSON:
        envelope = handler(arguments)
        transcript_path = execution_transcript_path(arguments)
        if transcript_path is None:
            return envelope
        transcript_event = {
            "timestamp": utc_now_iso(),
            "toolbox": TOOLBOX_ID,
            "operation": envelope.get("operation"),
            "ok": envelope.get("ok", False),
            "inputs": envelope.get("inputs", {}),
            "artifacts": envelope.get("artifacts", []),
            "observations": envelope.get("observations", []),
            "command": envelope.get("command"),
            "stdout_preview": _preview_text(str(envelope.get("stdout", ""))),
            "stderr_preview": _preview_text(str(envelope.get("stderr", ""))),
            "exit_code": envelope.get("exit_code"),
        }
        written_path = append_jsonl(transcript_path, transcript_event)
        artifacts = envelope.get("artifacts")
        if isinstance(artifacts, list) and written_path not in artifacts:
            artifacts.append(written_path)
        observations = envelope.get("observations")
        if isinstance(observations, list):
            observations.append({"transcript_path": written_path})
        return envelope

    return wrapped


def _binwalk_command() -> str:
    system_binwalk = Path("/usr/bin/binwalk")
    if system_binwalk.exists():
        return str(system_binwalk)
    return "binwalk"


def _parse_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError(f"`{field_name}` is required.")
    return int(text, 0)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return int(text, 0)


def _json_payload(stdout: str) -> JSON | None:
    stripped = stdout.strip()
    if not stripped:
        return None
    parsed = json.loads(stripped)
    return parsed if isinstance(parsed, dict) else None


def _run_worker(operation: str, payload: JSON, *, env_name: str, cwd: str | None, timeout_sec: int) -> tuple[JSON, JSON | None]:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix="opencrow-reversing-", delete=False) as handle:
            json.dump(payload, handle, sort_keys=True)
            temp_path = Path(handle.name)
        result = conda_run(
            ["python", str(REVERSING_WORKER), operation, "--config", str(temp_path)],
            env_name=env_name,
            cwd=cwd,
            timeout_sec=timeout_sec,
        )
        parsed = None
        try:
            parsed = _json_payload(result["stdout"])
        except json.JSONDecodeError:
            parsed = None
        return result, parsed
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _r2ghidra_dec_available() -> bool:
    if not command_exists("r2"):
        return False
    result = run_command(["r2", "-q", "-e", "scr.color=0", "-c", "L~ghidra", "malloc://1"], timeout_sec=30)
    return result["ok"] and bool(result["stdout"].strip())


def _ghidra_install_dir() -> str | None:
    ghidra_headless = shutil.which("ghidra-headless")
    if not ghidra_headless:
        return None
    resolved = Path(ghidra_headless).resolve()
    if resolved.name == "analyzeHeadless" and resolved.parent.name == "support":
        return str(resolved.parent.parent)
    return str(resolved.parent)


def _r2_sections(path: Path, *, cwd: str | None, timeout_sec: int) -> list[JSON] | None:
    result = run_command(
        ["r2", "-q", "-e", "scr.color=0", "-c", "iSj", str(path)],
        cwd=cwd,
        timeout_sec=timeout_sec,
    )
    if not result["ok"]:
        return None
    try:
        payload = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    sections: list[JSON] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        vaddr = item.get("vaddr")
        if not isinstance(vaddr, int):
            continue
        size_value = item.get("vsize")
        if not isinstance(size_value, int) or size_value <= 0:
            size_value = item.get("size")
        if not isinstance(size_value, int) or size_value <= 0:
            continue
        sections.append(
            {
                "name": str(item.get("name", "") or ""),
                "vaddr": vaddr,
                "size": size_value,
                "perm": str(item.get("perm", "") or ""),
            }
        )
    return sections


def _find_address_section(
    sections: list[JSON],
    address: int,
    *,
    requested_section: str | None = None,
    executable_only: bool = False,
) -> JSON | None:
    for section in sections:
        name = str(section.get("name", ""))
        if requested_section and name != requested_section:
            continue
        if executable_only and "x" not in str(section.get("perm", "")):
            continue
        start = int(section["vaddr"])
        size = int(section["size"])
        if start <= address < start + max(size, 1):
            return section
    return None


def toolbox_verify(arguments: JSON) -> JSON:
    env_name = _env_name(arguments)
    python_status = {module: conda_module_available(env_name, module) for module in PYTHON_MODULES}
    system_status = {dependency: command_exists(dependency) for dependency in SYSTEM_DEPENDENCIES}
    conda_command_status = {dependency: conda_command_exists(env_name, dependency) for dependency in CONDA_COMMANDS}
    ghidra_install_dir = _ghidra_install_dir()
    r2ghidra_available = _r2ghidra_dec_available()
    observations = [
        {"dependency": module, "available": available, "type": "python-module", "env_name": env_name}
        for module, available in python_status.items()
    ]
    observations.extend(
        {
            "dependency": dependency,
            "available": available,
            "type": "system-command",
            "path": shutil.which(dependency),
        }
        for dependency, available in system_status.items()
    )
    observations.extend(
        {"dependency": dependency, "available": available, "type": "conda-command", "env_name": env_name}
        for dependency, available in conda_command_status.items()
    )
    observations.append({"dependency": "conda", "available": command_exists("conda"), "type": "system-command"})
    observations.append(
        {
            "dependency": "ghidra-install-dir",
            "available": ghidra_install_dir is not None,
            "type": "install-path",
            "path": ghidra_install_dir,
        }
    )
    observations.append(
        {
            "dependency": "r2ghidra-dec",
            "available": r2ghidra_available,
            "type": "radare2-plugin",
            "notes": "Used by radare2 pdg-style workflows; detected separately from base radare2 availability.",
        }
    )
    observations.extend(
        [
            {
                "capability": "base_reversing_stack",
                "available": all(
                    [
                        python_status.get("capstone", False),
                        python_status.get("lief", False),
                        system_status.get("r2", False),
                        system_status.get("objdump", False),
                    ]
                ),
                "type": "capability",
            },
            {
                "capability": "decompilation",
                "available": system_status.get("ghidra-headless", False) and ghidra_install_dir is not None,
                "type": "capability",
            },
            {
                "capability": "symbolic_execution",
                "available": python_status.get("angr", False) and python_status.get("claripy", False),
                "type": "capability",
            },
            {
                "capability": "r2ghidra_dec",
                "available": r2ghidra_available,
                "type": "capability",
            },
        ]
    )
    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="toolbox_verify",
        summary="Reversing toolbox dependency status returned.",
        inputs={"env_name": env_name},
        observations=observations,
        next_steps=[
            "Use `reversing_disassemble` for quick static triage.",
            "Use `reversing_decompile` when you need Ghidra-derived C output.",
            "Use `reversing_symbolic_execute` or `reversing_emulate_blob` before falling back to ad hoc `reversing_python` scripts.",
        ],
    )


def reversing_python(arguments: JSON) -> JSON:
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
            next_steps=["Use `reversing_decompile`, `reversing_read_data`, or `reversing_symbolic_execute` before dropping to more ad hoc scripts."],
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


def reversing_disassemble(arguments: JSON) -> JSON:
    raw_path = str(arguments.get("path", "")).strip()
    backend = str(arguments.get("backend", "objdump")).strip() or "objdump"
    requested_address = _optional_int(arguments.get("start_address"))
    if requested_address is None:
        requested_address = _optional_int(arguments.get("address"))
    stop_address = _optional_int(arguments.get("stop_address"))
    instruction_count = int(arguments.get("instruction_count", 64))
    inputs = {
        "path": raw_path,
        "backend": backend,
        "section": arguments.get("section"),
        "start_address": hex(requested_address) if requested_address is not None else None,
        "stop_address": hex(stop_address) if stop_address is not None else None,
        "intel_syntax": bool(arguments.get("intel_syntax", True)),
        "instruction_count": instruction_count,
        "address": hex(requested_address) if requested_address is not None else None,
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
    effective_address = requested_address
    if backend == "objdump":
        if not command_exists("objdump"):
            return missing_dependency_envelope(TOOLBOX_ID, "reversing_disassemble", "objdump", inputs)
        command = ["objdump", "-d"]
        if inputs["intel_syntax"]:
            command.append("-Mintel")
        if inputs["section"]:
            command.extend(["-j", str(inputs["section"])])
        if requested_address is not None:
            command.append(f"--start-address={requested_address:#x}")
            if stop_address is None:
                stop_address = requested_address + max(1, instruction_count) * 16
                inputs["stop_address"] = hex(stop_address)
            command.append(f"--stop-address={stop_address:#x}")
        elif stop_address is not None:
            command.append(f"--stop-address={stop_address:#x}")
        command.append(str(path))
        result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    elif backend == "radare2":
        if not command_exists("r2"):
            return missing_dependency_envelope(TOOLBOX_ID, "reversing_disassemble", "r2", inputs)
        selected_section = None
        if requested_address is not None:
            sections = _r2_sections(path, cwd=cwd, timeout_sec=timeout_sec)
            if sections is not None:
                selected_section = _find_address_section(
                    sections,
                    requested_address,
                    requested_section=str(inputs["section"]) if inputs["section"] else None,
                    executable_only=True,
                )
                if selected_section is None:
                    return error_envelope(
                        toolbox=TOOLBOX_ID,
                        operation="reversing_disassemble",
                        summary=f"Invalid address for radare2 disassembly: {requested_address:#x}",
                        inputs=inputs,
                        stderr="The requested address is not inside an executable mapped section.",
                        exit_code=2,
                        observations=[
                            {
                                "requested_address": requested_address,
                                "requested_section": inputs["section"],
                                "available_executable_sections": [
                                    section["name"] for section in sections if "x" in str(section.get("perm", ""))
                                ],
                            }
                        ],
                    )
            probe = run_command(
                ["r2", "-q", "-e", "scr.color=0", "-c", f"aaa; s {requested_address:#x}; ?v $$", str(path)],
                cwd=cwd,
                timeout_sec=timeout_sec,
            )
            if not probe["ok"]:
                return error_envelope(
                    toolbox=TOOLBOX_ID,
                    operation="reversing_disassemble",
                    summary="radare2 failed while validating the requested address.",
                    inputs=inputs,
                    command=probe["command"],
                    stdout=probe["stdout"],
                    stderr=probe["stderr"],
                    exit_code=probe["exit_code"],
                )
            try:
                effective_address = int(probe["stdout"].strip().splitlines()[-1], 0)
            except (IndexError, ValueError):
                effective_address = None
            if effective_address != requested_address:
                return error_envelope(
                    toolbox=TOOLBOX_ID,
                    operation="reversing_disassemble",
                    summary=f"Invalid address for radare2 disassembly: {requested_address:#x}",
                    inputs=inputs,
                    stderr="radare2 did not seek to the requested address.",
                    exit_code=2,
                    observations=[{"requested_address": requested_address, "effective_address": effective_address}],
                )
        commands = ["aaa"]
        if requested_address is not None:
            commands.append(f"s {requested_address:#x}")
        commands.append(f"pd {instruction_count}")
        result = run_command(
            ["r2", "-q", "-e", "scr.color=0", "-c", "; ".join(commands), str(path)],
            cwd=cwd,
            timeout_sec=timeout_sec,
        )
    else:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_disassemble",
            summary=f"Unsupported disassembly backend: {backend}",
            inputs=inputs,
            stderr="Supported values are `objdump` and `radare2`.",
            exit_code=2,
        )

    line_count = len([line for line in result["stdout"].splitlines() if line.strip()])
    observations = [
        {
            "backend": backend,
            "line_count": line_count,
            "requested_address": requested_address,
            "effective_address": effective_address,
        }
    ]
    if requested_address is not None and line_count == 0:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_disassemble",
            summary=f"No disassembly output was produced for address {requested_address:#x}.",
            inputs=inputs,
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            observations=observations,
        )
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
            next_steps=["Use `reversing_decompile` or `reversing_read_data` if you need higher-level function logic or VA-based data extraction."],
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


def reversing_decompile(arguments: JSON) -> JSON:
    raw_path = str(arguments.get("path", "")).strip()
    function_name = str(arguments.get("function_name", "")).strip()
    function_address_raw = arguments.get("function_address")
    function_address = _optional_int(function_address_raw)
    project_dir_value = arguments.get("project_dir")
    inputs = {
        "path": raw_path,
        "function_name": function_name or None,
        "function_address": hex(function_address) if function_address is not None else None,
        "project_dir": normalize_path(project_dir_value) if project_dir_value is not None else None,
    }
    if not raw_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_decompile",
            summary="A target path is required.",
            inputs=inputs,
            stderr="Pass `path`.",
            exit_code=2,
        )
    if bool(function_name) == bool(function_address is not None):
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_decompile",
            summary="Pass exactly one of `function_name` or `function_address`.",
            inputs=inputs,
            stderr="Use `function_name` for a symbol name or `function_address` for an exact entry address.",
            exit_code=2,
        )

    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        return _path_error("reversing_decompile", path, inputs)
    if not command_exists("ghidra-headless"):
        return missing_dependency_envelope(TOOLBOX_ID, "reversing_decompile", "ghidra-headless", inputs)
    ghidra_script_path = GHIDRA_SCRIPT_DIR / GHIDRA_SCRIPT_NAME
    if not ghidra_script_path.exists():
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_decompile",
            summary=f"Missing bundled Ghidra script: {ghidra_script_path.name}",
            inputs=inputs,
            stderr=f"Expected script at {ghidra_script_path}",
            exit_code=2,
        )

    cwd, timeout_sec = default_execution(arguments)
    project_root: Path | None = None
    temp_root: tempfile.TemporaryDirectory[str] | None = None
    try:
        if project_dir_value is None:
            temp_root = tempfile.TemporaryDirectory(prefix="opencrow-ghidra-")
            project_root = Path(temp_root.name)
            persist_artifacts = False
        else:
            base_dir = Path(str(project_dir_value)).expanduser().resolve()
            base_dir.mkdir(parents=True, exist_ok=True)
            project_root = Path(tempfile.mkdtemp(prefix="opencrow-ghidra-", dir=str(base_dir)))
            persist_artifacts = True

        output_path = project_root / "decompiled.c"
        command = [
            "ghidra-headless",
            str(project_root),
            "project",
            "-import",
            str(path),
            "-scriptPath",
            str(GHIDRA_SCRIPT_DIR),
            "-postScript",
            GHIDRA_SCRIPT_NAME,
            function_name,
            f"{function_address:#x}" if function_address is not None else "",
            str(output_path),
        ]
        result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
        artifacts = [str(path)]
        if persist_artifacts:
            artifacts.extend([str(project_root), str(output_path)])
        if not result["ok"]:
            return error_envelope(
                toolbox=TOOLBOX_ID,
                operation="reversing_decompile",
                summary=f"Ghidra headless decompilation failed for {path.name}.",
                inputs=inputs,
                artifacts=artifacts,
                command=result["command"],
                stdout=result["stdout"],
                stderr=result["stderr"],
                exit_code=result["exit_code"],
            )
        if not output_path.exists():
            return error_envelope(
                toolbox=TOOLBOX_ID,
                operation="reversing_decompile",
                summary="Ghidra completed but did not emit the decompiled output file.",
                inputs=inputs,
                artifacts=artifacts,
                command=result["command"],
                stdout=result["stdout"],
                stderr=result["stderr"],
                exit_code=2,
            )
        decompiled_text = output_path.read_text(encoding="utf-8", errors="replace")
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_decompile",
            summary=f"Ghidra decompilation completed for {path.name}.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[
                {
                    "function_name": function_name or None,
                    "function_address": function_address,
                    "project_root": str(project_root),
                    "output_path": str(output_path),
                }
            ],
            command=result["command"],
            stdout=decompiled_text,
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Use `reversing_read_data` to inspect any tables or global data referenced by the decompiled function."],
        )
    finally:
        if temp_root is not None:
            temp_root.cleanup()


def reversing_read_data(arguments: JSON) -> JSON:
    env_name = _env_name(arguments)
    raw_path = str(arguments.get("path", "")).strip()
    inputs = {
        "env_name": env_name,
        "path": raw_path,
        "virtual_address": arguments.get("virtual_address"),
        "size": arguments.get("size"),
        "format": arguments.get("format", "hex"),
        "endianness": arguments.get("endianness", "little"),
    }
    if not raw_path:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_read_data",
            summary="A target path is required.",
            inputs=inputs,
            stderr="Pass `path`.",
            exit_code=2,
        )
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        return _path_error("reversing_read_data", path, inputs)
    if not conda_module_available(env_name, "lief"):
        return missing_dependency_envelope(TOOLBOX_ID, "reversing_read_data", "lief (ctf env)", inputs)

    cwd, timeout_sec = default_execution(arguments)
    result, payload = _run_worker(
        "read-data",
        {
            "path": str(path),
            "virtual_address": arguments.get("virtual_address"),
            "size": arguments.get("size"),
            "format": arguments.get("format", "hex"),
            "endianness": arguments.get("endianness", "little"),
        },
        env_name=env_name,
        cwd=cwd,
        timeout_sec=timeout_sec,
    )
    if result["ok"] and payload is not None:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_read_data",
            summary=f"Read {payload.get('size')} bytes from {path.name} at {payload.get('virtual_address'):#x}.",
            inputs=inputs,
            artifacts=[str(path)],
            observations=[
                {
                    "virtual_address": payload.get("virtual_address"),
                    "file_offset": payload.get("file_offset"),
                    "section": payload.get("section"),
                    "format": payload.get("format"),
                    "value": payload.get("value"),
                }
            ],
            command=result["command"],
            stdout=json.dumps(payload, indent=2, sort_keys=True),
            stderr=result["stderr"],
            exit_code=result["exit_code"],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="reversing_read_data",
        summary=f"Failed to read virtual-address data from {path.name}.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def reversing_emulate_blob(arguments: JSON) -> JSON:
    env_name = _env_name(arguments)
    raw_path = str(arguments.get("path", "")).strip()
    blob_hex = arguments.get("blob_hex")
    inputs = {
        "env_name": env_name,
        "path": raw_path or None,
        "has_blob_hex": blob_hex is not None,
        "arch": arguments.get("arch"),
        "start_address": arguments.get("start_address"),
        "size": arguments.get("size"),
        "entry_address": arguments.get("entry_address"),
        "base_address": arguments.get("base_address"),
        "max_instructions": arguments.get("max_instructions"),
    }
    if not raw_path and not str(blob_hex or "").strip():
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_emulate_blob",
            summary="Pass `blob_hex` or `path` with `start_address` and `size`.",
            inputs=inputs,
            stderr="Use `blob_hex` for raw bytes, or `path` plus typed code-region coordinates.",
            exit_code=2,
        )
    if raw_path:
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            return _path_error("reversing_emulate_blob", path, inputs)
        inputs["path"] = str(path)
    else:
        path = None
    if not conda_module_available(env_name, "unicorn"):
        return missing_dependency_envelope(TOOLBOX_ID, "reversing_emulate_blob", "unicorn (ctf env)", inputs)

    cwd, timeout_sec = default_execution(arguments)
    payload_in = {
        "path": str(path) if path is not None else None,
        "blob_hex": blob_hex,
        "arch": arguments.get("arch"),
        "start_address": arguments.get("start_address"),
        "size": arguments.get("size"),
        "base_address": arguments.get("base_address"),
        "entry_address": arguments.get("entry_address"),
        "stack_address": arguments.get("stack_address"),
        "stack_size": arguments.get("stack_size"),
        "registers": arguments.get("registers"),
        "memory_map": arguments.get("memory_map"),
        "arguments": arguments.get("arguments"),
        "max_instructions": arguments.get("max_instructions"),
        "stop_address": arguments.get("stop_address"),
    }
    result, payload = _run_worker("emulate-blob", payload_in, env_name=env_name, cwd=cwd, timeout_sec=timeout_sec)
    artifacts = [str(path)] if path is not None else []
    if result["ok"] and payload is not None:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_emulate_blob",
            summary="Blob emulation completed.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[
                {
                    "arch": payload.get("arch"),
                    "entry_address": payload.get("entry_address"),
                    "stop_reason": payload.get("stop_reason"),
                    "executed_instructions": payload.get("executed_instructions"),
                    "last_address": payload.get("last_address"),
                    "registers": payload.get("registers"),
                }
            ],
            command=result["command"],
            stdout=json.dumps(payload, indent=2, sort_keys=True),
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Use `reversing_symbolic_execute` if you need to solve for an input that drives the emulated code to a target state."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="reversing_emulate_blob",
        summary="Blob emulation failed.",
        inputs=inputs,
        artifacts=artifacts,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def reversing_symbolic_execute(arguments: JSON) -> JSON:
    env_name = _env_name(arguments)
    raw_path = str(arguments.get("path", "")).strip()
    blob_hex = arguments.get("blob_hex")
    inputs = {
        "env_name": env_name,
        "path": raw_path or None,
        "has_blob_hex": blob_hex is not None,
        "arch": arguments.get("arch"),
        "function_address": arguments.get("function_address"),
        "entry_address": arguments.get("entry_address"),
        "find_addresses": arguments.get("find_addresses"),
        "avoid_addresses": arguments.get("avoid_addresses"),
        "symbolic_inputs": arguments.get("symbolic_inputs"),
    }
    if not raw_path and not str(blob_hex or "").strip():
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_symbolic_execute",
            summary="Pass `blob_hex` or `path`.",
            inputs=inputs,
            stderr="Use `path` for a file-backed target or `blob_hex` for a raw code buffer.",
            exit_code=2,
        )
    if raw_path:
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            return _path_error("reversing_symbolic_execute", path, inputs)
        inputs["path"] = str(path)
    else:
        path = None
    if not conda_module_available(env_name, "angr"):
        return missing_dependency_envelope(TOOLBOX_ID, "reversing_symbolic_execute", "angr (ctf env)", inputs)

    cwd, timeout_sec = default_execution(arguments)
    payload_in = {
        "path": str(path) if path is not None else None,
        "blob_hex": blob_hex,
        "arch": arguments.get("arch"),
        "base_address": arguments.get("base_address"),
        "function_address": arguments.get("function_address"),
        "entry_address": arguments.get("entry_address"),
        "registers": arguments.get("registers"),
        "symbolic_inputs": arguments.get("symbolic_inputs"),
        "find_addresses": arguments.get("find_addresses"),
        "avoid_addresses": arguments.get("avoid_addresses"),
    }
    result, payload = _run_worker("symbolic-execute", payload_in, env_name=env_name, cwd=cwd, timeout_sec=timeout_sec)
    artifacts = [str(path)] if path is not None else []
    if result["ok"] and payload is not None:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="reversing_symbolic_execute",
            summary="Symbolic execution completed.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[
                {
                    "arch": payload.get("arch"),
                    "start_address": payload.get("start_address"),
                    "found_address": payload.get("found_address"),
                    "solutions": payload.get("solutions"),
                }
            ],
            command=result["command"],
            stdout=json.dumps(payload, indent=2, sort_keys=True),
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Replay the returned model under `reversing_emulate_blob` or the target binary to confirm the recovered input."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="reversing_symbolic_execute",
        summary="Symbolic execution failed.",
        inputs=inputs,
        artifacts=artifacts,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def reversing_trace(arguments: JSON) -> JSON:
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


def reversing_binwalk(arguments: JSON) -> JSON:
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


def reversing_gadget_search(arguments: JSON) -> JSON:
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
                input_schema={"type": "object", "properties": {"env_name": {"type": "string"}, "execution": {"type": "object"}}},
                handler=_transcript_wrapper(toolbox_verify),
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
                handler=_transcript_wrapper(reversing_python),
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
                handler=_transcript_wrapper(reversing_disassemble),
            ),
            MCPTool(
                name="reversing_decompile",
                description="Decompile a function with Ghidra headless using a typed function target.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "function_name": {"type": "string"},
                        "function_address": {"type": ["string", "integer"]},
                        "project_dir": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                },
                handler=_transcript_wrapper(reversing_decompile),
            ),
            MCPTool(
                name="reversing_read_data",
                description="Read data from a binary by virtual address using typed decode modes.",
                input_schema={
                    "type": "object",
                    "required": ["path", "virtual_address", "size"],
                    "properties": {
                        "env_name": {"type": "string"},
                        "path": {"type": "string"},
                        "virtual_address": {"type": ["string", "integer"]},
                        "size": {"type": "integer"},
                        "format": {"type": "string"},
                        "endianness": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                },
                handler=_transcript_wrapper(reversing_read_data),
            ),
            MCPTool(
                name="reversing_emulate_blob",
                description="Emulate a blob or extracted code region with Unicorn using typed execution inputs.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "env_name": {"type": "string"},
                        "path": {"type": "string"},
                        "blob_hex": {"type": "string"},
                        "arch": {"type": "string"},
                        "start_address": {"type": ["string", "integer"]},
                        "size": {"type": "integer"},
                        "base_address": {"type": ["string", "integer"]},
                        "entry_address": {"type": ["string", "integer"]},
                        "stack_address": {"type": ["string", "integer"]},
                        "stack_size": {"type": "integer"},
                        "registers": {"type": "object"},
                        "memory_map": {"type": "array"},
                        "arguments": {"type": "array"},
                        "max_instructions": {"type": "integer"},
                        "stop_address": {"type": ["string", "integer"]},
                        "execution": {"type": "object"},
                    },
                },
                handler=_transcript_wrapper(reversing_emulate_blob),
            ),
            MCPTool(
                name="reversing_symbolic_execute",
                description="Run a typed angr symbolic execution workflow against a function or blob.",
                input_schema={
                    "type": "object",
                    "required": ["arch", "symbolic_inputs", "find_addresses"],
                    "properties": {
                        "env_name": {"type": "string"},
                        "path": {"type": "string"},
                        "blob_hex": {"type": "string"},
                        "arch": {"type": "string"},
                        "base_address": {"type": ["string", "integer"]},
                        "function_address": {"type": ["string", "integer"]},
                        "entry_address": {"type": ["string", "integer"]},
                        "registers": {"type": "object"},
                        "symbolic_inputs": {"type": "array"},
                        "find_addresses": {"type": "array"},
                        "avoid_addresses": {"type": "array"},
                        "execution": {"type": "object"},
                    },
                },
                handler=_transcript_wrapper(reversing_symbolic_execute),
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
                handler=_transcript_wrapper(reversing_trace),
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
                handler=_transcript_wrapper(reversing_binwalk),
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
                handler=_transcript_wrapper(reversing_gadget_search),
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
