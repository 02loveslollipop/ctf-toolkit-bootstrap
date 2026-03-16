# CTF Toolkit Bootstrap

Bootstrap a CTF workstation around an existing Anaconda or Miniconda installation.

This setup creates or updates a `ctf` conda environment, installs the Python solver/exploit stack used in this workspace, and installs the native reversing and pwn tools that were added here.
It also vendors Codex skill folders and injects them into `~/.codex/skills`.

## Requirements

- An existing Anaconda or Miniconda installation
- Ubuntu or another Debian-like system with `apt-get`
- `sudo` access for system package installation
- Network access

If Conda is not installed, the installer now stops with official download links:

- Miniconda: <https://docs.conda.io/en/latest/miniconda.html>
- Anaconda: <https://www.anaconda.com/download>

The installer assumes:

- your user-local binaries live in `~/.local/bin`
- your optional large tools live in `~/.local/opt`
- the main working environment is named `ctf`

## What It Installs

### Python packages in `ctf`

Pinned in [requirements-ctf.txt](/home/zerotwo/ctf-toolkit-bootstrap/requirements-ctf.txt):

- `angr`
- `pwntools`
- `z3-solver`
- `claripy`
- `capstone`
- `unicorn`
- `keystone-engine`
- `ropper`
- `r2pipe`
- `lief`
- `scapy`
- `fpylll`
- supporting packages needed by this exact stack

### System packages

- `checksec`
- `gdbserver`
- `ltrace`
- `nasm`
- `patchelf`
- `python3-xlib`
- `qemu-user`
- `qemu-user-static`
- `radare2`
- `rsync`
- `unzip`
- `ruby`
- `curl`
- `git`
- `openjdk-21-jre`

### User-local tools

- `pwndbg`
- `pwninit`
- `seccomp-tools`
- `ghidra-headless`
- `ghidra`

### Codex skills injected into `~/.codex/skills`

- `ctf-tools`
- `minecraft-async`
- `netcat-async`
- `sagemath`
- `ssh-async`

## Included Skills

### `ctf-tools`

Use this skill for the normal Python-heavy CTF workflow outside SageMath. It is meant for exploit development, pwning, binary analysis, protocol scripting, symbolic solving, and quick one-off helpers that should run inside the repo's `ctf` conda environment instead of the ambient shell environment.

The skill is the main bridge between Codex and the installed toolkit. In practice that means it gives the agent a reliable path for using packages such as `pwntools`, `angr`, `claripy`, `z3-solver`, `capstone`, `unicorn`, `keystone`, `ropper`, `r2pipe`, `scapy`, and `fpylll`, along with native tools like `gdb`, `checksec`, `patchelf`, `qemu-user`, `radare2`, `strace`, `ltrace`, `objdump`, and `nasm`.

Typical use cases:

- build and run exploit or solver scripts in the `ctf` environment
- inspect ELF protections and patch binaries
- debug a local or remote pwn target
- automate reversing or packet-analysis workflows

### `minecraft-async`

Use this skill when a challenge depends on driving a locally installed Minecraft client. It launches the existing `~/.minecraft` Java install directly, favors offline-mode identities that are common in Minecraft CTF infrastructure, and manages the running client asynchronously instead of relying on the official launcher UI.

It also exposes fast X11-backed actions for focusing the game window, sending chat, issuing slash commands, capturing screenshots, and checking Minecraft logs. That makes it useful for tasks where the agent needs both process-level control and visual state inspection, such as joining a server, entering a world, teleporting, validating on-screen state, or diagnosing disconnects and startup failures.

Typical use cases:

- launch directly into a multiplayer server or singleplayer world
- operate with alternate offline usernames
- send in-game commands quickly without manual typing
- inspect `latest.log` and capture screenshots while debugging state

### `netcat-async`

Use this skill when a target speaks a line-oriented or raw TCP protocol and the connection needs to stay open across multiple agent actions. Instead of one-shot `nc` invocations, it keeps a named session alive in the background, lets the agent send input incrementally, and preserves a read log for later inspection.

This is useful for interactive CTF services, menu-driven binaries behind `socat`, custom challenge daemons, or any network flow where reads and writes happen at different times. The session model is intentionally simple: start, send, read, inspect status, and stop.

Typical use cases:

- interact with a remote challenge service over TCP
- keep a connection open while exploring protocol behavior
- capture and tail responses without losing session state
- avoid repeatedly reconnecting while testing payloads

### `sagemath`

Use this skill for math-heavy or algebra-heavy tasks that need real Sage instead of plain Python. It is intended for cryptography and CTF problem classes where finite fields, elliptic curves, modular arithmetic, lattices, polynomial algebra, small-root attacks, or PRNG analysis are easier or only practical in SageMath.

The skill complements `ctf-tools` rather than replacing it. If the work is ordinary Python scripting, exploit logic, or generic reverse engineering, `ctf-tools` is the better default. If the work depends on Sage objects, symbolic number theory, lattice reduction patterns, or reusable `.sage` templates, this skill is the right choice.

Typical use cases:

- solve RSA, ECC, lattice, or hidden-number style challenges
- work with finite-field arithmetic and curve points
- prototype number-theory attacks in a `.sage` file
- use bundled Sage templates for common crypto attack setups

### `ssh-async`

Use this skill when the agent needs a persistent SSH shell rather than a single `ssh host command` call. Like `netcat-async`, it keeps a named session open and lets the agent send commands, inspect output later, and reuse the same authenticated shell context across multiple steps.

It is suited to remote debugging, deployment, log inspection, long-lived administrative sessions, and workflows where the current working directory, shell environment, or prompt state matters. It is deliberately focused on line-oriented shell usage, not full-screen TUI applications.

Typical use cases:

- keep one remote shell open for a whole debugging session
- inspect logs and rerun commands on the same host without reconnecting
- work through a remote challenge environment incrementally
- preserve shell context while iterating on fixes or commands

## Install

Run:

```bash
bash /home/zerotwo/ctf-toolkit-bootstrap/scripts/install.sh
```

Optional:

```bash
bash /home/zerotwo/ctf-toolkit-bootstrap/scripts/install.sh --env myctf
bash /home/zerotwo/ctf-toolkit-bootstrap/scripts/install.sh --dry-run
```

## Verify

Run:

```bash
bash /home/zerotwo/ctf-toolkit-bootstrap/scripts/verify.sh
```

Or verify a different environment:

```bash
bash /home/zerotwo/ctf-toolkit-bootstrap/scripts/verify.sh --env myctf
```

## Skill Injection

The repo carries vendored skills under [skills](/home/zerotwo/ctf-toolkit-bootstrap/skills) and copies them into `~/.codex/skills` during install.

Manual sync:

```bash
bash /home/zerotwo/ctf-toolkit-bootstrap/scripts/sync_skills.sh
```

Manual removal:

```bash
bash /home/zerotwo/ctf-toolkit-bootstrap/scripts/remove_skills.sh
```

## Make Targets

Run:

```bash
make -C /home/zerotwo/ctf-toolkit-bootstrap install ENV=ctf
make -C /home/zerotwo/ctf-toolkit-bootstrap dry-run ENV=ctf
make -C /home/zerotwo/ctf-toolkit-bootstrap verify ENV=ctf
make -C /home/zerotwo/ctf-toolkit-bootstrap uninstall ENV=ctf
make -C /home/zerotwo/ctf-toolkit-bootstrap sync-skills
make -C /home/zerotwo/ctf-toolkit-bootstrap remove-skills
```

## Uninstall

By default, the uninstall script removes the user-local tools and symlinks it created:

```bash
bash /home/zerotwo/ctf-toolkit-bootstrap/scripts/uninstall.sh
```

Optional:

```bash
bash /home/zerotwo/ctf-toolkit-bootstrap/scripts/uninstall.sh --env ctf --remove-env
bash /home/zerotwo/ctf-toolkit-bootstrap/scripts/uninstall.sh --purge-apt
bash /home/zerotwo/ctf-toolkit-bootstrap/scripts/uninstall.sh --dry-run
```

## Notes

- The installer does not remove existing environments or tools.
- The installer checks `conda` on `PATH` first, then common install locations such as `~/miniconda3` and `~/anaconda3`.
- The uninstall script is conservative by default. It does not remove the conda environment or apt packages unless asked.
- The vendored skill sync uses `rsync --delete` per managed skill directory, so repo copies become the source of truth for `ctf-tools`, `minecraft-async`, `netcat-async`, `sagemath`, and `ssh-async` under `~/.codex/skills`.
- `minecraft-async` launches the existing `~/.minecraft` Java client directly for offline usernames and uses X11 automation through `python3-xlib` for fast in-game actions.
- `pwndbg` is installed with the upstream rootless installer.
- `ghidra` is downloaded from the official NSA GitHub release and unpacked under `~/.local/opt/ghidra`.
- `seccomp-tools` is installed with `gem --user-install` and symlinked into `~/.local/bin`.
- The GitHub Actions workflow is a smoke test for script validity and dry-run behavior. It does not download the full toolchain in CI.
