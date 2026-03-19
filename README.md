# OpenCROW

Open Codex Runtime for Offensive Workflows.

OpenCROW bootstraps a CTF workstation around an existing Anaconda or Miniconda installation, then syncs the repo-managed Codex skills into `~/.codex/skills`. The current implementation is catalog-driven, stateful, backed by a Python Typer CLI, and able to install a broad headless toolbox set plus most full-profile tools directly.

The first MCP migration wave is also in progress. OpenCROW now ships provider-neutral stdio MCP servers for the smaller toolboxes, with the shared contract defined in [doc/MCP_ARCHITECTURE.md](doc/MCP_ARCHITECTURE.md).

## Requirements

- An existing Anaconda or Miniconda installation
- Ubuntu or another Debian-like system with `apt-get`
- `sudo` access for system package installation
- Network access

If Conda is missing, the installer stops and prints official download links:

- Miniconda: <https://docs.conda.io/en/latest/miniconda.html>
- Anaconda: <https://www.anaconda.com/download>

## Installer Model

OpenCROW now resolves installs from the machine-readable catalog at `scripts/tool_catalog.json`.

Behavior:

- `install.sh` is the interactive public entrypoint. It bootstraps a small Python installer venv and opens the full-screen Textual installer.
- `install_headless.sh` is the non-interactive install entrypoint and uses the same flag-driven selection model as the old shell installer.
- `update_headless.sh` is the non-interactive additive update entrypoint and merges the requested selection into the saved managed state.
- The installer prints homepage and license links for every selected tool before it starts.
- Proprietary packages marked in the catalog require an explicit terms acceptance prompt during interactive installs.
- Install state is saved under `~/.local/share/opencrow/install-state.json`.
- By default, re-running the installer merges the new selection into the saved managed set and installs only the missing delta.
- Use `--replace-selection` when you want the installer to save exactly the current selection instead of performing an additive update.

Interactive modes:

- fresh installs: `fast install` or `personalized`
- existing managed installs: `update` or `modify`
- `update`: add new toolboxes to the current managed install
- `modify`: replace the saved managed selection interactively

Current profiles:

- `headless`: installs the CLI and automation-friendly tool set
- `full`: includes the headless set plus GUI/heavier tools such as OWASP ZAP, Autopsy, StegSolve, OpenStego, and theHarvester

## Implemented Toolboxes

The current phase 1 implementation covers:

- `opencrow-crypto-toolbox`: `z3-solver`, `fpylll`, `pycryptodome`, `hashcat`, `john`, `factordb-pycli`
- `opencrow-pwn-toolbox`: `pwntools`, `checksec`, `gdb`, `gdbserver`, `patchelf`, `qemu-user`, `qemu-user-static`, `nasm`, `gcc`, `pwninit`, `pwndbg`, `seccomp-tools`, `one_gadget`
- `opencrow-reversing-toolbox`: `angr`, `claripy`, `capstone`, `unicorn`, `keystone-engine`, `ropper`, `ROPGadget`, `r2pipe`, `lief`, `qiling`, `frida-tools`, `ghidra`, `radare2`, `strace`, `ltrace`, `binwalk`, `binutils`
- `opencrow-network-toolbox`: `scapy`, `tshark`, `tcpdump`, `netcat-openbsd`, `socat`, `nmap`
- `opencrow-web-toolbox`: `sqlmap`, `gobuster`, `ffuf`, `dirb`, `wfuzz`
- `opencrow-forensics-toolbox`: `volatility3`, `exiftool`, `foremost`
- `opencrow-stego-toolbox`: `steghide`, `zsteg`
- `opencrow-osint-toolbox`: `shodan`, `sherlock`, `waybackpy`
- `opencrow-utility-toolbox`: `jq`, `yq`, `xxd`, `tmux`, `screen`, `ripgrep`, `fzf`

Wave 1 MCP servers:

- `opencrow-stego-mcp`
- `opencrow-forensics-mcp`
- `opencrow-osint-mcp`
- `opencrow-web-mcp`

Tracked as manual full-profile steps today:

- `Burp Suite Community`

## Attribution

OpenCROW installs and orchestrates third-party software, but it does not claim ownership of those tools.

- Each third-party package remains under its own upstream license and terms.
- OpenCROW does not relicense, modify, or redistribute those tools as part of this project.
- The installer only downloads packages from their official or explicitly configured upstream sources.
- Homepage and license links for selected tools are shown during installation so the operator can review them before proceeding.

The current OpenCROW toolbox stack credits the upstream projects it installs or manages through the catalog, including:

- `z3-solver`, `fpylll`, `PyCryptodome`, `hashcat`, `John the Ripper`, `factordb-pycli`
- `pwntools`, `checksec`, `gdb`, `gdbserver`, `patchelf`, `qemu-user`, `qemu-user-static`, `nasm`, `gcc`, `pwninit`, `pwndbg`, `seccomp-tools`, `one_gadget`
- `angr`, `claripy`, `capstone`, `unicorn`, `keystone-engine`, `ropper`, `ROPGadget`, `r2pipe`, `lief`, `qiling`, `frida-tools`, `ghidra`, `radare2`, `strace`, `ltrace`, `binwalk`, `binutils`
- `scapy`, `tshark`, `tcpdump`, `netcat-openbsd`, `socat`, `nmap`
- `sqlmap`, `gobuster`, `ffuf`, `dirb`, `wfuzz`, `OWASP ZAP`, `OpenStego`, `StegSolve`, `Autopsy`, `theHarvester`
- `volatility3`, `exiftool`, `foremost`, `steghide`, `zsteg`, `shodan`, `sherlock`, `waybackpy`
- `jq`, `yq`, `xxd`, `tmux`, `screen`, `ripgrep`, `fzf`

## Codex Skills

Repo-managed skills synced into `~/.codex/skills`:

- `opencrow-crypto-toolbox`
- `opencrow-pwn-toolbox`
- `opencrow-reversing-toolbox`
- `opencrow-network-toolbox`
- `opencrow-web-toolbox`
- `opencrow-forensics-toolbox`
- `opencrow-stego-toolbox`
- `opencrow-osint-toolbox`
- `opencrow-utility-toolbox`
- `minecraft-async` (`OpenCROW I/O - Minecraft Async`)
- `netcat-async` (`OpenCROW I/O - Netcat Async`)
- `sagemath` (`OpenCROW Runner - SageMath`)
- `ssh-async` (`OpenCROW I/O - SSH Async`)

High-level skill roles:

- `opencrow-crypto-toolbox`: Python-first crypto solving, cracking, and quick factoring checks
- `opencrow-pwn-toolbox`: exploit development, ELF/runtime triage, and libc-oriented helpers
- `opencrow-reversing-toolbox`: binary analysis, emulation, tracing, and instrumentation
- `opencrow-network-toolbox`: packet work, PCAP analysis, and network/service triage
- `opencrow-web-toolbox`: endpoint discovery, fuzzing, and automated SQLi workflows
- `opencrow-forensics-toolbox`: metadata extraction, memory analysis, and file carving
- `opencrow-stego-toolbox`: hidden-data triage in media files
- `opencrow-osint-toolbox`: public-source reconnaissance and archive lookups
- `opencrow-utility-toolbox`: shell and workflow helpers
- `minecraft-async` (`OpenCROW I/O - Minecraft Async`): asynchronous control of a local Minecraft client for CTF tasks
- `netcat-async` (`OpenCROW I/O - Netcat Async`): persistent asynchronous TCP sessions
- `sagemath` (`OpenCROW Runner - SageMath`): Sage-based math and cryptanalysis
- `ssh-async` (`OpenCROW I/O - SSH Async`): persistent asynchronous SSH sessions

## MCP Architecture

OpenCROW toolbox MCP servers follow one shared contract:

- one stdio MCP server per toolbox
- provider-neutral typed tools, not Codex-specific shell wrappers
- common tools on every server: `toolbox_info`, `toolbox_verify`, `toolbox_capabilities`
- shared response envelope with `ok`, `summary`, `toolbox`, `operation`, `inputs`, `artifacts`, `observations`, `command`, `stdout`, `stderr`, `exit_code`, and `next_steps`

Architecture details and contract rules live in [doc/MCP_ARCHITECTURE.md](doc/MCP_ARCHITECTURE.md).

## Install

From the repo root:

```bash
bash ./scripts/install.sh
```

Common non-interactive examples:

```bash
bash ./scripts/install_headless.sh --dry-run
bash ./scripts/install_headless.sh --profile headless
bash ./scripts/install_headless.sh --toolbox opencrow-crypto-toolbox --toolbox opencrow-web-toolbox --profile headless
bash ./scripts/install_headless.sh --tool one_gadget --tool zsteg
bash ./scripts/install_headless.sh --toolbox opencrow-network-toolbox --replace-selection --profile headless
bash ./scripts/update_headless.sh --toolbox opencrow-web-toolbox --profile headless
```

## `opencrow-autosetup`

`opencrow-autosetup` is an installed CLI utility from the utility toolbox. It seeds a challenge workspace with reconnaissance artifacts and then launches a nested Codex pass that only performs challenge reconnaissance.

Generated artifacts:

- `HANDOFF.md`
- `SKILL.md`
- `RECONNAISSANCE.md`
- `HYPOTHESIS.md`
- `AGENTS.md` selected and written by the reconnaissance agent at the end of the pass

Behavior:

- reads `DESCRIPTION.md` when present and uses it as the challenge description seed
- defaults to `pwn` when no stronger category signal is found
- detects common remote connection strings such as `nc`, `ssh`, and `telnet`
- if the challenge is a pure remote black-box target, it tells the agent to focus reconnaissance on that connection instead of unrelated local speculation
- writes artifacts in the current directory by default, or a custom path with `--output-dir`
- does not attempt exploitation, flag capture, or final solve validation
- writes the operational contract, TODOs, and unresolved questions to `HANDOFF.md`
- makes the recon agent choose the final challenge category and write the matching category-specific `AGENTS.md` at the end of the pass
- runs the nested Codex agent with `danger-full-access` plus full inherited shell environment by default
- supports `--interactive` to launch the recon pass as an interactive Codex session instead of `codex exec`
- supports `--disable-sandbox` to launch the nested Codex run without sandboxing

Examples:

```bash
opencrow-autosetup --dry-run
opencrow-autosetup --interactive
opencrow-autosetup --category web
opencrow-autosetup --output-dir ./artifacts
opencrow-autosetup --disable-sandbox
opencrow-autosetup --ack-missing-description
```

Shell completion:

- bash completion is installed at `~/.local/share/bash-completion/completions/opencrow-autosetup`
- for the current shell session you can load it with:

```bash
source ~/.local/share/bash-completion/completions/opencrow-autosetup
```

Run without sandboxing:

```bash
opencrow-autosetup --disable-sandbox --ack-missing-description
```

## `opencrow-exploit`

`opencrow-exploit` is the follow-up CLI utility for the solve phase. It reads the current workspace handoff artifacts, builds a prompt for the exploitation agent, and launches Codex in the current directory.

Behavior:

- reads the current workspace documents in this order when present:
  `AGENTS.md`, `HANDOFF.md`, `DESCRIPTION.md`, `SKILL.md`, `RECONNAISSANCE.md`, `HYPOTHESIS.md`
- treats `AGENTS.md` as the authoritative category-specific exploit contract when it exists
- treats `HANDOFF.md` as the operational contract and exploit TODO list from reconnaissance
- defaults to an interactive Codex session for the exploitation pass
- supports `--full-auto` to run the solve pass through `codex exec`
- runs with `danger-full-access` plus full inherited shell environment by default
- supports `--disable-sandbox` to launch the nested Codex run without sandboxing

Examples:

```bash
opencrow-exploit
opencrow-exploit --model gpt-5.4
opencrow-exploit --full-auto
opencrow-exploit --disable-sandbox
```

## Verify

Verify the saved install selection:

```bash
bash ./scripts/verify.sh
```

Verify all catalogued tools instead of just the saved selection:

```bash
bash ./scripts/verify.sh --all-tools
```

Verify a different conda environment explicitly:

```bash
bash ./scripts/verify.sh --env myctf
```

## Skill Sync

Manual sync:

```bash
bash ./scripts/sync_skills.sh
```

Manual removal:

```bash
bash ./scripts/remove_skills.sh
```

The skill sync removes the retired `ctf-tools` directory before copying the current OpenCROW toolbox skills.

## Uninstall

Remove the currently saved OpenCROW selection:

```bash
bash ./scripts/uninstall.sh
```

Useful options:

```bash
bash ./scripts/uninstall.sh --dry-run
bash ./scripts/uninstall.sh --purge-apt
bash ./scripts/uninstall.sh --remove-env
bash ./scripts/uninstall.sh --all-managed
```

## Make Targets

```bash
make install ENV=ctf
make dry-run ENV=ctf
make verify ENV=ctf
make uninstall ENV=ctf
make sync-skills
make remove-skills
make smoke ENV=ctf
```

## Notes

- The installer still checks `conda` on `PATH` first, then common locations like `~/miniconda3` and `~/anaconda3`.
- `minecraft-async` still relies on `python3-xlib`, which remains a base system dependency.
- `ghidra` is downloaded under `~/.local/opt/ghidra`.
- `pwndbg` is installed with the upstream rootless installer.
- The GitHub Actions workflow remains a smoke test around syntax and dry-run behavior; it does not install the full workstation in CI.
