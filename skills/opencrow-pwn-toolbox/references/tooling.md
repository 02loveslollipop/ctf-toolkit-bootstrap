# OpenCROW Pwn Toolbox

Use this reference when the target is exploit-oriented and the question is which installed tool gets you to a working primitive fastest.

## Python in `ctf`

- `pwntools`: process and remote I/O, ELF parsing, packing, cyclic patterns, shellcode helpers, and ROP.

## Native tools

- `checksec <binary>`: inspect RELRO, PIE, NX, canaries, and Fortify.
- `pwndbg <binary>`: launch GDB with exploit-focused helpers.
- `gdb <binary>` and `gdbserver :1234 <binary>`: debugger workflows.
- `pwninit`: patch a challenge binary against the shipped `libc` and loader.
- `seccomp-tools dump <binary>`: inspect seccomp filters.
- `one_gadget libc.so.6`: enumerate libc one-shot gadgets and constraints.
- `patchelf --print-interpreter <binary>`: inspect or rewrite ELF runtime metadata.
- `qemu-aarch64 ./chall`: run a non-native userland binary.
- `gcc` and `nasm`: build helper code, shellcode harnesses, or PoCs.

## Practical selection

- Start with `checksec`, `file`, and `ldd`-style inspection before writing an exploit.
- Use `pwntools` as the default scripting layer unless the job is purely static.
- Move into `pwndbg` once you need registers, heap state, or breakpoints.
- Use `pwninit` before deeper debugging when the binary ships with a custom libc or loader.
- Use `qemu-user` only when architecture mismatch is the blocker.
