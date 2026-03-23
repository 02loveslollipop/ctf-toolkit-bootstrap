---
name: opencrow-reversing-toolbox
description: Use the Anaconda `ctf` environment and installed reverse-engineering tooling for binary analysis, symbolic execution, disassembly, emulation, and binary patching. Use when Codex needs `angr`, `claripy`, `capstone`, `unicorn`, `ghidra-headless`, `radare2`, `objdump`, `strace`, `ltrace`, `binwalk`, or related tools.
---

# OpenCROW Reversing Toolbox

Prefer the `opencrow-reversing-mcp` server for typed disassembly, decompilation, VA-based data reads, emulation, symbolic execution, tracing, gadget search, and Python-driven analysis. Fall back to the direct scripts only when you need to debug the underlying `ctf`-environment execution path.

## MCP First

- Use `toolbox_info`, `toolbox_verify`, and `toolbox_capabilities` first.
- Use the typed reversing operations:
  - `reversing_python`
  - `reversing_disassemble`
  - `reversing_decompile`
  - `reversing_read_data`
  - `reversing_emulate_blob`
  - `reversing_symbolic_execute`
  - `reversing_trace`
  - `reversing_binwalk`
  - `reversing_gadget_search`
- Treat the existing helper scripts as the implementation fallback, not the primary interface.
- For long workflows, pass `execution.transcript_path` so each MCP step appends a JSONL transcript artifact instead of relying on chat history.

Use this skill for understanding binaries rather than exploiting them: disassembly, decompilation support, tracing, symbolic execution, emulation, dynamic instrumentation, gadget analysis, and binary rewriting in the `ctf` environment.

## Quick Start

Run inline Python in `ctf`:

```bash
python ~/.codex/skills/opencrow-reversing-toolbox/scripts/run_reversing_python.py --code 'import angr; print(angr.__version__)'
```

Run an analysis helper:

```bash
python ~/.codex/skills/opencrow-reversing-toolbox/scripts/run_reversing_python.py --file /absolute/path/to/analyze.py
```

Verify the mapped stack:

```bash
python ~/.codex/skills/opencrow-reversing-toolbox/scripts/verify_toolkit.py
```

## Workflow

1. Start with `toolbox_info`, `toolbox_verify`, and `toolbox_capabilities`.
2. Triage with `reversing_disassemble` using an explicit `address` or `start_address` when you already know the function entry.
3. Move to `reversing_decompile` or `reversing_read_data` before dropping into ad hoc scripts.
4. Use `reversing_emulate_blob` for deterministic execution of small code regions and `reversing_symbolic_execute` when you need to solve for an input that reaches a target address.
5. Use `reversing_python` only when the typed tools do not cover the analysis shape.
6. Read [references/tooling.md](references/tooling.md) when selecting among the installed reverse-engineering tools.

## Tool Selection

- Use `angr` for CFG recovery, path exploration, symbolic execution, and automated state search.
- Use `claripy` when you need symbolic expressions without a full `angr` workflow.
- Use `capstone`, `keystone`, and `unicorn` for disassembly, assembly, and emulation inside custom scripts.
- Use `ropper` to search gadgets during binary inspection.
- Use `ROPGadget` when you want a second gadget finder or architecture-specific output formats.
- Use `r2pipe` and `radare2` for scriptable or interactive binary analysis.
- Use `lief` for parsing and patching executable formats.
- Use `qiling` when you need a higher-level emulation environment around a foreign binary or firmware target.
- Use `frida-tools` when you need runtime API tracing or live instrumentation instead of static reversing.
- Use `ghidra-headless` for repeatable import, analysis, and decompilation tasks without the GUI.
- Use `objdump`, `strace`, `ltrace`, and `binwalk` for fast static, runtime, or firmware-oriented inspection.

## Resources

- `scripts/run_reversing_python.py`: execute inline code or a `.py` file inside the `ctf` environment.
- `scripts/verify_toolkit.py`: confirm that the mapped Python and native reversing tools are installed.
- `references/tooling.md`: quick selection notes for reverse-engineering workflows.
