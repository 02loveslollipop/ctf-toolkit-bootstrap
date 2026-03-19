# OpenCROW Reversing Toolbox

Use this reference when the problem is binary understanding and you need to choose the lightest effective analysis path.

## Python in `ctf`

- `angr`: symbolic execution, CFG recovery, state exploration, and path search.
- `claripy`: symbolic expression building without a full `angr` project.
- `capstone`: disassembly.
- `keystone`: assembly.
- `unicorn`: CPU emulation.
- `ropper`: gadget search.
- `ROPGadget`: alternate gadget finder with architecture-oriented output.
- `r2pipe`: drive `radare2` from Python.
- `lief`: parse and patch executable formats.
- `qiling`: full-system-style emulation framework for complex binaries and firmware components.

## Native tools

- `ghidra-headless`: scripted import, analysis, and decompilation.
- `r2 -A <binary>`: interactive analysis in radare2.
- `frida-ps` / `frida-trace`: dynamic instrumentation and API tracing.
- `objdump -d <binary>`: fast disassembly.
- `strace <binary>`: syscall tracing.
- `ltrace <binary>`: library-call tracing.
- `binwalk -e <blob>`: firmware extraction and blob triage.

## Practical selection

- Start with `strings`, `objdump`, or `r2` for fast triage.
- Use `angr` only when manual inspection or a small solver is not enough.
- Use `claripy` when the logic is symbolic but full program lifting is unnecessary.
- Use `qiling` when the target needs richer emulation than raw `unicorn`.
- Use `frida-tools` when the fastest answer comes from tracing live behavior instead of reading assembly.
- Use `ghidra-headless` when you need reproducible decompilation output in scripts.
- Use `strace` or `ltrace` when runtime behavior reveals more than static analysis.
