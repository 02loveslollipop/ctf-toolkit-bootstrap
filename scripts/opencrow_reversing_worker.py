#!/usr/bin/env python3
"""Helper operations for the OpenCROW reversing MCP server."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


JSON = dict[str, Any]
PAGE_SIZE = 0x1000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["read-data", "emulate-blob", "symbolic-execute"])
    parser.add_argument("--config", required=True, help="Path to a JSON config file.")
    return parser.parse_args()


def parse_int(value: Any, *, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise ValueError("Missing integer value.")
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        if default is None:
            raise ValueError("Missing integer value.")
        return default
    return int(text, 0)


def align_down(value: int, alignment: int = PAGE_SIZE) -> int:
    return value & ~(alignment - 1)


def align_up(value: int, alignment: int = PAGE_SIZE) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def decode_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def load_config(path: str) -> JSON:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object config payload.")
    return payload


def read_file_region(path: Path, offset: int, size: int) -> bytes:
    if offset < 0:
        raise ValueError(f"Invalid file offset: {offset}")
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read(size)
    if len(data) != size:
        raise ValueError(f"Requested {size} bytes at file offset {offset:#x}, got {len(data)} bytes.")
    return data


def normalize_arch(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86-64": "x86_64",
        "i386": "x86",
        "i686": "x86",
        "aarch64": "arm64",
    }
    normalized = aliases.get(text, text)
    if normalized not in {"x86", "x86_64", "arm", "arm64"}:
        raise ValueError(f"Unsupported architecture: {value}")
    return normalized


def read_blob_bytes(config: JSON) -> tuple[bytes, JSON]:
    import lief

    blob_hex = config.get("blob_hex")
    if blob_hex is not None and str(blob_hex).strip():
        data = bytes.fromhex(str(blob_hex).strip())
        base_address = parse_int(config.get("base_address"), default=0x1000000)
        return data, {
            "source": "blob_hex",
            "base_address": base_address,
        }

    raw_path = str(config.get("path", "")).strip()
    if not raw_path:
        raise ValueError("Pass either `blob_hex` or `path`.")
    start_address = parse_int(config.get("start_address"))
    size = parse_int(config.get("size"))
    path = Path(raw_path).expanduser().resolve()
    binary = lief.parse(str(path))
    if binary is None:
        raise ValueError(f"Failed to parse binary: {path}")
    offset = int(binary.virtual_address_to_offset(start_address))
    data = read_file_region(path, offset, size)
    return data, {
        "source": "binary",
        "path": str(path),
        "virtual_address": start_address,
        "size": size,
        "file_offset": offset,
        "base_address": parse_int(config.get("base_address"), default=start_address),
    }


def detect_section(binary: Any, virtual_address: int) -> str | None:
    for section in getattr(binary, "sections", []):
        start = int(getattr(section, "virtual_address", 0))
        size = int(getattr(section, "size", 0))
        if size <= 0:
            size = len(getattr(section, "content", []))
        if start <= virtual_address < start + max(size, 1):
            return str(getattr(section, "name", "") or "")
    return None


def run_read_data(config: JSON) -> JSON:
    import lief

    raw_path = str(config.get("path", "")).strip()
    if not raw_path:
        raise ValueError("Pass `path`.")
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"Input file does not exist: {path}")

    virtual_address = parse_int(config.get("virtual_address"))
    size = parse_int(config.get("size"))
    output_format = str(config.get("format", "hex")).strip() or "hex"
    endianness = str(config.get("endianness", "little")).strip().lower() or "little"
    if endianness not in {"little", "big"}:
        raise ValueError("`endianness` must be `little` or `big`.")
    if output_format not in {"bytes", "hex", "u32", "u64", "cstring"}:
        raise ValueError("Unsupported `format`. Use `bytes`, `hex`, `u32`, `u64`, or `cstring`.")

    binary = lief.parse(str(path))
    if binary is None:
        raise ValueError(f"Failed to parse binary: {path}")
    offset = int(binary.virtual_address_to_offset(virtual_address))
    raw = read_file_region(path, offset, size)

    if output_format == "bytes":
        value: Any = list(raw)
    elif output_format == "hex":
        value = raw.hex()
    elif output_format == "u32":
        if size < 4:
            raise ValueError("`u32` format requires `size >= 4`.")
        value = int.from_bytes(raw[:4], endianness)
    elif output_format == "u64":
        if size < 8:
            raise ValueError("`u64` format requires `size >= 8`.")
        value = int.from_bytes(raw[:8], endianness)
    else:
        cstr = raw.split(b"\x00", 1)[0]
        value = decode_text(cstr)

    return {
        "path": str(path),
        "virtual_address": virtual_address,
        "size": size,
        "format": output_format,
        "endianness": endianness,
        "file_offset": offset,
        "section": detect_section(binary, virtual_address),
        "hex": raw.hex(),
        "value": value,
    }


def unicorn_arch_profile(arch_name: str) -> JSON:
    if arch_name == "x86_64":
        from unicorn import UC_ARCH_X86, UC_MODE_64, x86_const

        return {
            "uc_arch": UC_ARCH_X86,
            "uc_mode": UC_MODE_64,
            "pc": x86_const.UC_X86_REG_RIP,
            "sp": x86_const.UC_X86_REG_RSP,
            "lr": None,
            "return_address": 0x4141414141414141,
            "arg_regs": [
                x86_const.UC_X86_REG_RDI,
                x86_const.UC_X86_REG_RSI,
                x86_const.UC_X86_REG_RDX,
                x86_const.UC_X86_REG_RCX,
                x86_const.UC_X86_REG_R8,
                x86_const.UC_X86_REG_R9,
            ],
            "registers": {
                "rip": x86_const.UC_X86_REG_RIP,
                "rsp": x86_const.UC_X86_REG_RSP,
                "rax": x86_const.UC_X86_REG_RAX,
                "rbx": x86_const.UC_X86_REG_RBX,
                "rcx": x86_const.UC_X86_REG_RCX,
                "rdx": x86_const.UC_X86_REG_RDX,
                "rsi": x86_const.UC_X86_REG_RSI,
                "rdi": x86_const.UC_X86_REG_RDI,
                "rbp": x86_const.UC_X86_REG_RBP,
                "r8": x86_const.UC_X86_REG_R8,
                "r9": x86_const.UC_X86_REG_R9,
                "r10": x86_const.UC_X86_REG_R10,
                "r11": x86_const.UC_X86_REG_R11,
                "r12": x86_const.UC_X86_REG_R12,
                "r13": x86_const.UC_X86_REG_R13,
                "r14": x86_const.UC_X86_REG_R14,
                "r15": x86_const.UC_X86_REG_R15,
            },
        }
    if arch_name == "x86":
        from unicorn import UC_ARCH_X86, UC_MODE_32, x86_const

        return {
            "uc_arch": UC_ARCH_X86,
            "uc_mode": UC_MODE_32,
            "pc": x86_const.UC_X86_REG_EIP,
            "sp": x86_const.UC_X86_REG_ESP,
            "lr": None,
            "return_address": 0x41414141,
            "arg_regs": [],
            "registers": {
                "eip": x86_const.UC_X86_REG_EIP,
                "esp": x86_const.UC_X86_REG_ESP,
                "eax": x86_const.UC_X86_REG_EAX,
                "ebx": x86_const.UC_X86_REG_EBX,
                "ecx": x86_const.UC_X86_REG_ECX,
                "edx": x86_const.UC_X86_REG_EDX,
                "esi": x86_const.UC_X86_REG_ESI,
                "edi": x86_const.UC_X86_REG_EDI,
                "ebp": x86_const.UC_X86_REG_EBP,
            },
        }
    if arch_name == "arm64":
        from unicorn import UC_ARCH_ARM64, UC_MODE_ARM, arm64_const

        registers = {
            "pc": arm64_const.UC_ARM64_REG_PC,
            "sp": arm64_const.UC_ARM64_REG_SP,
            "lr": arm64_const.UC_ARM64_REG_LR,
        }
        for index in range(29):
            registers[f"x{index}"] = getattr(arm64_const, f"UC_ARM64_REG_X{index}")
        return {
            "uc_arch": UC_ARCH_ARM64,
            "uc_mode": UC_MODE_ARM,
            "pc": arm64_const.UC_ARM64_REG_PC,
            "sp": arm64_const.UC_ARM64_REG_SP,
            "lr": arm64_const.UC_ARM64_REG_LR,
            "return_address": 0x4141414141414141,
            "arg_regs": [getattr(arm64_const, f"UC_ARM64_REG_X{index}") for index in range(8)],
            "registers": registers,
        }

    from unicorn import UC_ARCH_ARM, UC_MODE_ARM, arm_const

    registers = {
        "pc": arm_const.UC_ARM_REG_PC,
        "sp": arm_const.UC_ARM_REG_SP,
        "lr": arm_const.UC_ARM_REG_LR,
    }
    for index in range(13):
        registers[f"r{index}"] = getattr(arm_const, f"UC_ARM_REG_R{index}")
    return {
        "uc_arch": UC_ARCH_ARM,
        "uc_mode": UC_MODE_ARM,
        "pc": arm_const.UC_ARM_REG_PC,
        "sp": arm_const.UC_ARM_REG_SP,
        "lr": arm_const.UC_ARM_REG_LR,
        "return_address": 0x41414141,
        "arg_regs": [getattr(arm_const, f"UC_ARM_REG_R{index}") for index in range(4)],
        "registers": registers,
    }


def set_unicorn_register(mu: Any, profile: JSON, name: str, value: int) -> None:
    register = profile["registers"].get(name.strip().lower())
    if register is None:
        raise ValueError(f"Unsupported register for emulation: {name}")
    mu.reg_write(register, value)


def serialize_registers(mu: Any, profile: JSON) -> JSON:
    return {
        name: mu.reg_read(register)
        for name, register in profile["registers"].items()
    }


def permission_mask(text: str | None) -> int:
    from unicorn import UC_PROT_EXEC, UC_PROT_READ, UC_PROT_WRITE

    flags = 0
    for char in (text or "rw").lower():
        if char == "r":
            flags |= UC_PROT_READ
        elif char == "w":
            flags |= UC_PROT_WRITE
        elif char == "x":
            flags |= UC_PROT_EXEC
    return flags or (UC_PROT_READ | UC_PROT_WRITE)


def encode_argument_blob(value: Any) -> bytes:
    if isinstance(value, dict):
        kind = str(value.get("type", "")).strip().lower()
        if kind == "string":
            return str(value.get("value", "")).encode("utf-8") + b"\x00"
        if kind == "hex":
            return bytes.fromhex(str(value.get("value", "")).strip())
        raise ValueError(f"Unsupported argument object type: {kind}")
    if isinstance(value, str):
        return value.encode("utf-8") + b"\x00"
    raise ValueError("Only string or `{\"type\": \"hex\"}` arguments can be materialized as blobs.")


def emulate_apply_arguments(mu: Any, profile: JSON, arguments: list[Any], allocator: list[int], stack_pointer: int) -> int:
    integer_values: list[int] = []
    for item in arguments:
        if isinstance(item, dict) and str(item.get("type", "")).strip().lower() == "int":
            integer_values.append(parse_int(item.get("value")))
            continue
        if isinstance(item, int):
            integer_values.append(item)
            continue
        blob = encode_argument_blob(item)
        address = allocator[0]
        allocator[0] += align_up(len(blob), 0x10)
        mu.mem_write(address, blob)
        integer_values.append(address)

    arg_regs = profile["arg_regs"]
    if arg_regs:
        for index, value in enumerate(integer_values[: len(arg_regs)]):
            mu.reg_write(arg_regs[index], value)
        if len(integer_values) > len(arg_regs):
            extra = integer_values[len(arg_regs) :]
            if profile["pc"] == profile["registers"].get("rip"):
                cursor = stack_pointer + 8
            elif profile["pc"] == profile["registers"].get("eip"):
                cursor = stack_pointer + 4
            else:
                cursor = stack_pointer
            width = 8 if profile["pc"] == profile["registers"].get("rip") or profile["pc"] == profile["registers"].get("pc") and "x0" in profile["registers"] else 4
            for value in extra:
                mu.mem_write(cursor, int(value).to_bytes(width, "little"))
                cursor += width
    else:
        # x86 function entry expects the return address at SP and stack args above it.
        cursor = stack_pointer + 4
        for value in integer_values:
            mu.mem_write(cursor, int(value).to_bytes(4, "little"))
            cursor += 4
    return stack_pointer


def run_emulate_blob(config: JSON) -> JSON:
    from unicorn import Uc, UC_HOOK_CODE

    arch_name = normalize_arch(config.get("arch"))
    profile = unicorn_arch_profile(arch_name)
    code, metadata = read_blob_bytes(config)
    base_address = int(metadata["base_address"])
    entry_address = parse_int(config.get("entry_address"), default=base_address)
    stack_address = parse_int(config.get("stack_address"), default=0x7000000)
    stack_size = parse_int(config.get("stack_size"), default=0x20000)
    max_instructions = parse_int(config.get("max_instructions"), default=100000)
    stop_address = config.get("stop_address")
    stop_target = parse_int(stop_address) if stop_address is not None else int(profile["return_address"])
    extra_maps = config.get("memory_map") if isinstance(config.get("memory_map"), list) else []
    arguments = config.get("arguments") if isinstance(config.get("arguments"), list) else []
    registers = config.get("registers") if isinstance(config.get("registers"), dict) else {}

    mu = Uc(profile["uc_arch"], profile["uc_mode"])
    code_start = align_down(base_address)
    code_size = align_up((entry_address - code_start) + len(code) + PAGE_SIZE)
    mu.mem_map(code_start, code_size, permission_mask("rwx"))
    mu.mem_write(base_address, code)
    mu.mem_map(align_down(stack_address), align_up(stack_size), permission_mask("rw"))

    allocator = [align_up(code_start + code_size, 0x1000)]
    mu.mem_map(allocator[0], align_up(0x10000), permission_mask("rw"))

    for item in extra_maps:
        if not isinstance(item, dict):
            continue
        address = parse_int(item.get("address"))
        size = parse_int(item.get("size"))
        region_start = align_down(address)
        region_size = align_up((address - region_start) + size)
        mu.mem_map(region_start, region_size, permission_mask(str(item.get("permissions", "rw"))))
        if item.get("bytes_hex"):
            mu.mem_write(address, bytes.fromhex(str(item["bytes_hex"]).strip()))

    initial_sp = stack_address + stack_size - 0x100
    if profile["pc"] == profile["registers"].get("rip"):
        initial_sp -= 8
        mu.mem_write(initial_sp, int(profile["return_address"]).to_bytes(8, "little"))
    elif profile["pc"] == profile["registers"].get("eip"):
        initial_sp -= 4
        mu.mem_write(initial_sp, int(profile["return_address"]).to_bytes(4, "little"))
    elif profile["lr"] is not None:
        mu.reg_write(profile["lr"], int(profile["return_address"]))
    mu.reg_write(profile["sp"], initial_sp)
    initial_sp = emulate_apply_arguments(mu, profile, arguments, allocator, initial_sp)

    for register_name, register_value in registers.items():
        set_unicorn_register(mu, profile, str(register_name), parse_int(register_value))

    state = {
        "executed_instructions": 0,
        "stop_reason": "completed",
        "last_address": entry_address,
    }

    def on_code(uc: Any, address: int, size: int, _user_data: Any) -> None:
        state["executed_instructions"] += 1
        state["last_address"] = address
        if address == stop_target:
            state["stop_reason"] = "stop_address"
            uc.emu_stop()
            return
        if state["executed_instructions"] >= max_instructions:
            state["stop_reason"] = "max_instructions"
            uc.emu_stop()

    mu.hook_add(UC_HOOK_CODE, on_code)
    try:
        mu.emu_start(entry_address, stop_target)
    except Exception as exc:  # noqa: BLE001
        state["stop_reason"] = f"exception: {exc}"

    return {
        "arch": arch_name,
        "source": metadata,
        "entry_address": entry_address,
        "stop_address": stop_target,
        "executed_instructions": state["executed_instructions"],
        "stop_reason": state["stop_reason"],
        "last_address": state["last_address"],
        "registers": serialize_registers(mu, profile),
    }


def set_angr_register(state: Any, name: str, value: int) -> None:
    register_name = name.strip().lower()
    if not hasattr(state.regs, register_name):
        raise ValueError(f"Unsupported register for symbolic execution: {name}")
    setattr(state.regs, register_name, value)


def run_symbolic_execute(config: JSON) -> JSON:
    import angr
    import archinfo
    import claripy

    raw_path = str(config.get("path", "")).strip()
    blob_hex = config.get("blob_hex")
    arch_name = normalize_arch(config.get("arch"))
    base_address = parse_int(config.get("base_address"), default=0x1000000)
    function_address_value = config.get("function_address")
    entry_address_value = config.get("entry_address")
    find_addresses = [parse_int(value) for value in (config.get("find_addresses") or [])]
    avoid_addresses = [parse_int(value) for value in (config.get("avoid_addresses") or [])]
    if not find_addresses:
        raise ValueError("Pass at least one `find_addresses` value.")

    if blob_hex is not None and str(blob_hex).strip():
        blob = bytes.fromhex(str(blob_hex).strip())
        project = angr.load_shellcode(blob, arch=archinfo.arch_from_id(arch_name), load_address=base_address)
        start = parse_int(function_address_value or entry_address_value, default=base_address)
        source = {"source": "blob_hex", "base_address": base_address, "size": len(blob)}
    else:
        if not raw_path:
            raise ValueError("Pass either `path` or `blob_hex`.")
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"Input file does not exist: {path}")
        project = angr.Project(str(path), auto_load_libs=False)
        start = parse_int(function_address_value or entry_address_value, default=int(project.entry))
        main_object = project.loader.main_object
        loaded_base_address = int(
            getattr(main_object, "mapped_base", getattr(main_object, "min_addr", project.entry))
        )
        source = {"source": "binary", "path": str(path), "base_address": loaded_base_address}

    state = project.factory.blank_state(
        addr=start,
        add_options={
            angr.options.ZERO_FILL_UNCONSTRAINED_MEMORY,
            angr.options.ZERO_FILL_UNCONSTRAINED_REGISTERS,
        },
    )
    symbolic_inputs = config.get("symbolic_inputs")
    if not isinstance(symbolic_inputs, list) or not symbolic_inputs:
        raise ValueError("Pass a non-empty `symbolic_inputs` list.")

    registers = config.get("registers") if isinstance(config.get("registers"), dict) else {}
    for register_name, register_value in registers.items():
        set_angr_register(state, str(register_name), parse_int(register_value))

    models: dict[str, Any] = {}
    for item in symbolic_inputs:
        if not isinstance(item, dict):
            raise ValueError("Each symbolic input must be an object.")
        name = str(item.get("name", "")).strip()
        length = parse_int(item.get("length"))
        if not name:
            raise ValueError("Each symbolic input needs a `name`.")
        symbolic_value = claripy.BVS(name, length * 8)
        models[name] = symbolic_value
        if item.get("address") is not None:
            address = parse_int(item.get("address"))
            state.memory.store(address, symbolic_value)
            if bool(item.get("null_terminate", False)):
                state.memory.store(address + length, claripy.BVV(0, 8))
            if item.get("pointer_register"):
                set_angr_register(state, str(item["pointer_register"]), address)
        elif item.get("register"):
            set_angr_register(state, str(item["register"]), symbolic_value)
        else:
            raise ValueError("Each symbolic input needs either `address` or `register`.")

    simulation = project.factory.simgr(state)
    simulation.explore(find=find_addresses, avoid=avoid_addresses)
    if not simulation.found:
        raise RuntimeError("No satisfying state reached the requested find address.")
    found = simulation.found[0]
    solutions: JSON = {}
    for name, symbolic_value in models.items():
        data = found.solver.eval(symbolic_value, cast_to=bytes)
        solutions[name] = {
            "hex": data.hex(),
            "text": decode_text(data),
            "length": len(data),
        }

    return {
        "arch": arch_name,
        "source": source,
        "start_address": start,
        "found_address": int(found.addr),
        "find_addresses": find_addresses,
        "avoid_addresses": avoid_addresses,
        "solutions": solutions,
    }


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if args.operation == "read-data":
        payload = run_read_data(config)
    elif args.operation == "emulate-blob":
        payload = run_emulate_blob(config)
    else:
        payload = run_symbolic_execute(config)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
