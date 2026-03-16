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
