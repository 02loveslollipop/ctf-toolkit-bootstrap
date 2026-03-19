# OpenCROW

Open Codex Runtime for Offensive Workflows.

OpenCROW bootstraps a CTF workstation around an existing Anaconda or Miniconda installation, then syncs the repo-managed Codex skills into `~/.codex/skills`. The current implementation is catalog-driven, stateful, backed by a Python Typer CLI, and able to install a broad headless toolbox set plus most full-profile tools directly.

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

- `install.sh` is the stable public entrypoint, but now bootstraps a small Python installer venv and delegates to a Typer CLI.
- On a TTY with no selection flags, `install.sh` starts a full-screen Textual installer that handles selection, resize, proprietary prompts, and confirmation inside one TUI flow.
- Without a TTY, `install.sh` defaults to a headless install across all OpenCROW toolboxes.
- The installer prints homepage and license links for every selected tool before it starts.
- Proprietary packages marked in the catalog require an explicit terms acceptance prompt during interactive installs.
- Install state is saved under `~/.local/share/opencrow/install-state.json`.

Interactive modes:

- `fast install`: choose toolboxes, then choose one global profile: `headless` or `full`
- `personalized`: choose individual tools directly

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

## Install

From the repo root:

```bash
bash ./scripts/install.sh
```

Common non-interactive examples:

```bash
bash ./scripts/install.sh --dry-run
bash ./scripts/install.sh --profile headless
bash ./scripts/install.sh --toolbox opencrow-crypto-toolbox --toolbox opencrow-web-toolbox --profile headless
bash ./scripts/install.sh --tool one_gadget --tool zsteg
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
