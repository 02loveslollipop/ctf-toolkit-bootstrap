#!/usr/bin/env python3
"""OpenCROW crypto toolbox MCP server."""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
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


SERVER_NAME = "opencrow-crypto-mcp"
SERVER_VERSION = "0.1.0"
TOOLBOX_ID = "opencrow-crypto-toolbox"
DISPLAY_NAME = "OpenCROW Crypto Toolbox"
OPERATIONS = [
    {"name": "crypto_python", "description": "Run typed inline Python or a Python file inside the managed ctf environment."},
    {"name": "crypto_factordb_lookup", "description": "Query the public FactorDB API for an integer candidate."},
    {"name": "crypto_crack_hash", "description": "Run a typed hash-cracking workflow through hashcat or John the Ripper."},
]
PYTHON_MODULES = ["z3", "fpylll", "Crypto"]
SYSTEM_DEPENDENCIES = ["hashcat", "john"]


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
        summary="Crypto toolbox dependency status returned.",
        inputs={"env_name": env_name},
        observations=observations,
        next_steps=[
            "Use `crypto_python` for Z3, lattice, or PyCryptodome workflows.",
            "Use `crypto_crack_hash` when the fastest path is hash cracking.",
        ],
    )


def crypto_python(arguments: dict[str, object]) -> dict[str, object]:
    env_name = _env_name(arguments)
    code = arguments.get("code")
    path_value = arguments.get("path")
    path_text = str(path_value).strip() if path_value is not None else None
    inputs = {"env_name": env_name, "path": path_text, "has_code": code is not None}
    if (code is None) == (path_value is None):
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="crypto_python",
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
                operation="crypto_python",
                summary="A non-empty file path is required.",
                inputs=inputs,
                stderr="Pass a non-empty `path` or use `code`.",
                exit_code=2,
            )
        path = Path(path_text).expanduser().resolve()
        if not path.exists():
            return _path_error("crypto_python", path, inputs)
        artifacts.append(str(path))
        result = run_conda_python(env_name=env_name, path=path, cwd=cwd, timeout_sec=timeout_sec, prefix="opencrow-crypto-")
    else:
        result = run_conda_python(env_name=env_name, code=str(code), cwd=cwd, timeout_sec=timeout_sec, prefix="opencrow-crypto-")

    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="crypto_python",
            summary="Crypto Python execution completed.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[{"env_name": env_name}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Use `crypto_factordb_lookup` or `crypto_crack_hash` if the workflow needs external factor or cracking assistance."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="crypto_python",
        summary="Crypto Python execution failed.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def crypto_factordb_lookup(arguments: dict[str, object]) -> dict[str, object]:
    integer = str(arguments.get("integer", "")).strip()
    inputs = {"integer": integer}
    if not integer:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="crypto_factordb_lookup",
            summary="An integer query is required.",
            inputs=inputs,
            stderr="Pass `integer`.",
            exit_code=2,
        )

    query = urllib.parse.urlencode({"query": integer})
    url = f"http://factordb.com/api?{query}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
    except Exception as exc:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="crypto_factordb_lookup",
            summary=f"FactorDB lookup failed for {integer}.",
            inputs=inputs,
            command=url,
            stderr=str(exc),
            exit_code=1,
        )

    return success_envelope(
        toolbox=TOOLBOX_ID,
        operation="crypto_factordb_lookup",
        summary=f"FactorDB lookup completed for {integer}.",
        inputs=inputs,
        observations=[payload],
        command=url,
        stdout=json.dumps(payload, indent=2, sort_keys=True),
        next_steps=["Use `crypto_python` or Sage if the factorization suggests a follow-on attack."],
    )


def crypto_crack_hash(arguments: dict[str, object]) -> dict[str, object]:
    backend = str(arguments.get("backend", "hashcat"))
    raw_hash_file = str(arguments.get("hash_file", "")).strip()
    wordlist = arguments.get("wordlist")
    mask = arguments.get("mask")
    show = bool(arguments.get("show", False))
    output_file = arguments.get("output_file")
    inputs = {
        "backend": backend,
        "hash_file": raw_hash_file,
        "wordlist": str(wordlist) if wordlist is not None else None,
        "mask": mask,
        "hash_mode": arguments.get("hash_mode"),
        "format": arguments.get("format"),
        "show": show,
        "output_file": str(output_file) if output_file is not None else None,
    }
    if not raw_hash_file:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="crypto_crack_hash",
            summary="A hash file is required.",
            inputs=inputs,
            stderr="Pass `hash_file`.",
            exit_code=2,
        )
    hash_file = Path(raw_hash_file).expanduser().resolve()
    if not hash_file.exists():
        return _path_error("crypto_crack_hash", hash_file, inputs)

    cwd, timeout_sec = default_execution(arguments)
    artifacts = [str(hash_file)]
    if output_file is not None:
        artifacts.append(str(Path(str(output_file)).expanduser().resolve()))

    if backend == "hashcat":
        if not command_exists("hashcat"):
            return missing_dependency_envelope(TOOLBOX_ID, "crypto_crack_hash", "hashcat", inputs)
        hash_mode = arguments.get("hash_mode")
        if hash_mode is None:
            return error_envelope(
                toolbox=TOOLBOX_ID,
                operation="crypto_crack_hash",
                summary="`hash_mode` is required for hashcat.",
                inputs=inputs,
                stderr="Pass `hash_mode`, for example `0` for raw MD5.",
                exit_code=2,
            )
        command = ["hashcat", "-m", str(hash_mode)]
        if output_file is not None:
            command.extend(["-o", str(Path(str(output_file)).expanduser().resolve())])
        if show:
            command.extend(["--show", str(hash_file)])
        elif wordlist is not None:
            wordlist_path = Path(str(wordlist)).expanduser().resolve()
            if not wordlist_path.exists():
                return _path_error("crypto_crack_hash", wordlist_path, inputs)
            artifacts.append(str(wordlist_path))
            command.extend(["-a", "0", str(hash_file), str(wordlist_path)])
        elif mask is not None:
            command.extend(["-a", "3", str(hash_file), str(mask)])
        else:
            return error_envelope(
                toolbox=TOOLBOX_ID,
                operation="crypto_crack_hash",
                summary="Hashcat requires `wordlist`, `mask`, or `show`.",
                inputs=inputs,
                stderr="Pass a wordlist attack, a mask attack, or `show=true`.",
                exit_code=2,
            )
        result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    elif backend == "john":
        if not command_exists("john"):
            return missing_dependency_envelope(TOOLBOX_ID, "crypto_crack_hash", "john", inputs)
        command = ["john"]
        hash_format = arguments.get("format")
        if hash_format:
            command.append(f"--format={hash_format}")
        if show:
            command.append("--show")
            command.append(str(hash_file))
        else:
            if wordlist is None:
                return error_envelope(
                    toolbox=TOOLBOX_ID,
                    operation="crypto_crack_hash",
                    summary="John requires `wordlist` unless `show=true`.",
                    inputs=inputs,
                    stderr="Pass `wordlist` or set `show=true`.",
                    exit_code=2,
                )
            wordlist_path = Path(str(wordlist)).expanduser().resolve()
            if not wordlist_path.exists():
                return _path_error("crypto_crack_hash", wordlist_path, inputs)
            artifacts.append(str(wordlist_path))
            command.append(f"--wordlist={wordlist_path}")
            command.append(str(hash_file))
        result = run_command(command, cwd=cwd, timeout_sec=timeout_sec)
    else:
        return error_envelope(
            toolbox=TOOLBOX_ID,
            operation="crypto_crack_hash",
            summary=f"Unsupported cracking backend: {backend}",
            inputs=inputs,
            stderr="Supported values are `hashcat` and `john`.",
            exit_code=2,
        )

    if result["ok"]:
        return success_envelope(
            toolbox=TOOLBOX_ID,
            operation="crypto_crack_hash",
            summary=f"{backend} workflow completed.",
            inputs=inputs,
            artifacts=artifacts,
            observations=[{"backend": backend, "show": show}],
            command=result["command"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["exit_code"],
            next_steps=["Inspect the crack output and feed recovered secrets back into `crypto_python` or the challenge workflow."],
        )
    return error_envelope(
        toolbox=TOOLBOX_ID,
        operation="crypto_crack_hash",
        summary=f"{backend} workflow failed.",
        inputs=inputs,
        command=result["command"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
    )


def build_server() -> StdioMCPServer:
    server = StdioMCPServer(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        instructions="OpenCROW crypto toolbox MCP server.",
    )
    server.register_tools(
        [
            MCPTool(
                name="toolbox_info",
                description="Return metadata about the OpenCROW crypto toolbox MCP server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_info_handler(
                    toolbox=TOOLBOX_ID,
                    display_name=DISPLAY_NAME,
                    server_name=SERVER_NAME,
                    server_version=SERVER_VERSION,
                    summary="OpenCROW crypto toolbox information returned.",
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
                description="Return dependency status for the OpenCROW crypto toolbox MCP server.",
                input_schema={"type": "object", "properties": {"env_name": {"type": "string"}}},
                handler=toolbox_verify,
            ),
            MCPTool(
                name="toolbox_capabilities",
                description="Return the structured operations exposed by the OpenCROW crypto toolbox MCP server.",
                input_schema={"type": "object", "properties": {}},
                handler=make_toolbox_capabilities_handler(TOOLBOX_ID, OPERATIONS),
            ),
            MCPTool(
                name="crypto_python",
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
                handler=crypto_python,
            ),
            MCPTool(
                name="crypto_factordb_lookup",
                description="Query the public FactorDB API for an integer candidate.",
                input_schema={"type": "object", "properties": {"integer": {"type": "string"}}},
                handler=crypto_factordb_lookup,
            ),
            MCPTool(
                name="crypto_crack_hash",
                description="Run a typed hash-cracking workflow through hashcat or John the Ripper.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "backend": {"type": "string"},
                        "hash_file": {"type": "string"},
                        "hash_mode": {"type": ["integer", "string"]},
                        "format": {"type": "string"},
                        "wordlist": {"type": "string"},
                        "mask": {"type": "string"},
                        "show": {"type": "boolean"},
                        "output_file": {"type": "string"},
                        "execution": {"type": "object"},
                    },
                },
                handler=crypto_crack_hash,
            ),
        ]
    )
    return server


def main() -> int:
    return build_server().serve()


if __name__ == "__main__":
    sys.exit(main())
