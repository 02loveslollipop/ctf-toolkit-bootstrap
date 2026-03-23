#!/usr/bin/env python3
"""Smoke test the public OpenCROW reversing MCP surface against a tiny fixture."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import opencrow_reversing_mcp as reversing_mcp


JSON = dict[str, Any]
X86_64_COMPARE_BLOB_HEX = "81ff341200007507b801000000c331c0c3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="ctf", help="Conda environment to use. Default: ctf.")
    return parser


def build_fixture(temp_root: Path) -> tuple[Path, int, int, int]:
    source_path = temp_root / "fixture.c"
    binary_path = temp_root / "fixture"
    source_path.write_text(
        textwrap.dedent(
            """
            #include <stdint.h>

            const char banner[] = "flag{demo}";

            int add3(int value) {
                return value + 3;
            }

            int cmp_magic(uint32_t value) {
                return value == 0x1234 ? 1 : 0;
            }

            int main(void) {
                return add3((int)banner[0]);
            }
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["gcc", "-O0", "-g", "-fno-pie", "-no-pie", "-o", str(binary_path), str(source_path)],
        check=True,
    )

    add3_address: int | None = None
    banner_address: int | None = None
    banner_size: int | None = None
    symbol_output = subprocess.check_output(["nm", "-n", "-S", str(binary_path)], text=True)
    for line in symbol_output.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3] == "add3":
            add3_address = int(parts[0], 16)
        if len(parts) >= 4 and parts[3] == "banner":
            banner_address = int(parts[0], 16)
            banner_size = int(parts[1], 16)

    if add3_address is None or banner_address is None or banner_size is None:
        raise RuntimeError("Failed to resolve test symbols from the smoke fixture.")
    return binary_path, add3_address, banner_address, banner_size


def parse_resource_json(response: JSON) -> JSON:
    contents = response["result"]["contents"]
    if not isinstance(contents, list) or not contents:
        raise RuntimeError("MCP resource read returned no contents.")
    text = contents[0]["text"]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise RuntimeError("Expected JSON object resource contents.")
    return parsed


def observation_map(envelope: JSON, key: str) -> dict[str, JSON]:
    mapped: dict[str, JSON] = {}
    for item in envelope.get("observations", []):
        if not isinstance(item, dict):
            continue
        value = item.get(key)
        if isinstance(value, str):
            mapped[value] = item
    return mapped


def main() -> int:
    args = build_parser().parse_args()
    server = reversing_mcp.build_server()

    with tempfile.TemporaryDirectory(prefix="opencrow-reversing-smoke-") as temp_dir:
        temp_root = Path(temp_dir)
        binary_path, add3_address, banner_address, banner_size = build_fixture(temp_root)
        transcript_path = temp_root / "reversing-transcript.jsonl"

        init_response = server._handle_message(  # noqa: SLF001
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        capabilities_response = server._handle_message(  # noqa: SLF001
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "resources/read",
                "params": {"uri": f"opencrow://{reversing_mcp.SERVER_NAME}/capabilities"},
            }
        )
        capabilities_payload = parse_resource_json(capabilities_response)

        verify = server.tools["toolbox_verify"].handler({"env_name": args.env})
        disassemble = server.tools["reversing_disassemble"].handler(
            {
                "path": str(binary_path),
                "backend": "objdump",
                "address": hex(add3_address),
                "instruction_count": 8,
            }
        )
        invalid_address = server.tools["reversing_disassemble"].handler(
            {
                "path": str(binary_path),
                "backend": "radare2",
                "address": "0x1",
            }
        )
        decompile = server.tools["reversing_decompile"].handler(
            {
                "path": str(binary_path),
                "function_name": "add3",
            }
        )
        read_data = server.tools["reversing_read_data"].handler(
            {
                "env_name": args.env,
                "path": str(binary_path),
                "virtual_address": hex(banner_address),
                "size": banner_size,
                "format": "cstring",
                "execution": {"transcript_path": str(transcript_path)},
            }
        )
        emulate = server.tools["reversing_emulate_blob"].handler(
            {
                "env_name": args.env,
                "blob_hex": X86_64_COMPARE_BLOB_HEX,
                "arch": "x86_64",
                "base_address": "0x401000",
                "entry_address": "0x401000",
                "arguments": [{"type": "int", "value": "0x1234"}],
                "max_instructions": 20,
            }
        )
        symbolic = server.tools["reversing_symbolic_execute"].handler(
            {
                "env_name": args.env,
                "blob_hex": X86_64_COMPARE_BLOB_HEX,
                "arch": "x86_64",
                "base_address": "0x401000",
                "function_address": "0x401000",
                "symbolic_inputs": [{"name": "arg0", "length": 8, "register": "rdi"}],
                "find_addresses": ["0x401008"],
                "avoid_addresses": ["0x40100e"],
            }
        )

        transcript_lines = transcript_path.read_text(encoding="utf-8").splitlines()
        transcript_event = json.loads(transcript_lines[0]) if transcript_lines else {}
        verify_dependencies = observation_map(verify, "dependency")
        verify_capabilities = observation_map(verify, "capability")

        checks = {
            "initialize_capabilities": init_response["result"]["capabilities"]
            == {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
            "capabilities_resource_shape": all(
                key in capabilities_payload for key in ["initializeCapabilities", "tools", "resources", "resourceTemplates"]
            ),
            "capabilities_resource_lists_new_tools": all(
                tool_name in {tool["name"] for tool in capabilities_payload["tools"]}
                for tool_name in [
                    "reversing_decompile",
                    "reversing_read_data",
                    "reversing_emulate_blob",
                    "reversing_symbolic_execute",
                ]
            ),
            "verify_lists_r2ghidra_plugin": "r2ghidra-dec" in verify_dependencies,
            "verify_lists_ghidra_install_dir": "ghidra-install-dir" in verify_dependencies,
            "verify_exposes_capability_buckets": all(
                name in verify_capabilities
                for name in ["base_reversing_stack", "decompilation", "symbolic_execution", "r2ghidra_dec"]
            ),
            "disassemble_explicit_address": disassemble["ok"] and f"{add3_address:08x} <add3>" in disassemble["stdout"],
            "disassemble_bad_address_rejected": (not invalid_address["ok"])
            and "Invalid address for radare2 disassembly" in invalid_address["summary"],
            "decompile_function": decompile["ok"] and "return value + 3;" in decompile["stdout"],
            "read_data_by_virtual_address": read_data["ok"] and "flag{demo}" in read_data["stdout"],
            "emulate_blob": emulate["ok"] and '"rax": 1' in emulate["stdout"],
            "symbolic_execute": symbolic["ok"] and "0000000000001234" in symbolic["stdout"],
            "transcript_artifact_written": transcript_path.exists()
            and str(transcript_path) in read_data["artifacts"]
            and len(transcript_lines) == 1
            and transcript_event.get("operation") == "reversing_read_data",
        }

        print(json.dumps({"checks": checks}, indent=2, sort_keys=True))
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
