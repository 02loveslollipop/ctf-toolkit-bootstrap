# OpenCROW Toolbox Expansion and Installer Redesign

## Summary

Expand OpenCROW in two phases by keeping new crypto, web, network, pwn, and reversing tools inside the existing toolboxes, while adding new domain-specific toolboxes for `forensics`, `stego`, `osint`, and `utility`. Redesign the bootstrap so human users get an interactive installer by default, automation defaults to a deterministic headless install, and every selected tool prints its third-party/license links before installation.

## Key Changes

### 1. Replace hardcoded package lists with a central tool catalog

Create one machine-readable catalog that drives install, verify, docs, and skill content. Each tool entry should define:

- stable tool id
- owning toolbox
- install method: `apt`, `pip`, `gem`, `go`, `direct-download`, or `git-clone`
- supported profiles: `headless`, `full`
- whether it is GUI-only, headless-capable, or restricted-download
- homepage/source URL
- license URL
- verify command or module name
- short purpose text for skill/docs generation

Use this catalog as the single source of truth for:

- `scripts/install.sh`
- `scripts/verify.sh`
- `scripts/uninstall.sh`
- toolbox skill inventories in `skills/.../SKILL.md`
- README installed-tool sections

This avoids reintroducing duplicated package lists as the matrix grows.

### 2. Redesign installer UX around `fast` and `personalized`

Keep `scripts/install.sh` as the entrypoint, but change behavior to:

- If running with a TTY and no selection flags, start an interactive installer.
- If running without a TTY and no selection flags, default to `headless` for all supported toolboxes.

Interactive flow:

- Step 1: choose `fast install` or `personalized`.
- `fast install`: user selects toolboxes, then one global profile for all selected toolboxes: `headless` or `full`.
- `personalized`: user selects tools one by one across toolboxes.

Non-interactive flags:

- `--toolbox <name>` repeatable, to limit scope
- `--profile headless|full`, for non-interactive installs
- `--all-toolboxes`, as a convenience alias
- `--interactive`, to force the prompt flow
- keep existing `--env` and `--dry-run`

Installer behavior:

- Before confirmation, print the final selection with toolbox, tool, install method, homepage, and license link.
- `full` means install every supported selected tool, including third-party or GUI software when automation is feasible.
- For tools that cannot be legally or reliably automated, the installer should stop at a clearly labeled manual step with official download/license links instead of silently skipping them.
- Persist install state under a shell-friendly state directory in `$HOME/.local/share/opencrow/` so verify/uninstall can operate on the selected set rather than assuming “everything”.

### 3. Toolbox structure and tool mapping

Keep the current toolboxes and extend them:

- `opencrow-crypto-toolbox`: `hashcat`, `john`, `pycryptodome`, FactorDB client/integration
- `opencrow-web-toolbox`: `sqlmap`, `gobuster`, `ffuf`, `dirb`, `wfuzz`, `OWASP ZAP`, `Burp Suite Community`
- `opencrow-network-toolbox`: `tshark`, `tcpdump`, `nmap`, `ncat`/`netcat`, `socat`
- `opencrow-pwn-toolbox`: `one_gadget`, `libc-database`
- `opencrow-reversing-toolbox`: `frida`, `qiling`
- Do not add `ROPGadget`; it is already present in `requirements-ctf.txt`.

Add new toolbox skills:

- `opencrow-forensics-toolbox`: `volatility3`, `autopsy`, `exiftool`, `foremost`
- `opencrow-stego-toolbox`: `steghide`, `zsteg`, `StegSolve`, `OpenStego`
- `opencrow-osint-toolbox`: `theHarvester`, `Shodan CLI`, `Sherlock`, Wayback tooling
- `opencrow-utility-toolbox`: `jq`, `yq`, `xxd`, `tmux`, `screen`, `ripgrep`, `fzf`

Skill updates:

- Each toolbox gets a scoped `SKILL.md`, a verifier, and a tool-selection reference.
- Existing placeholder web skill becomes real.
- New toolboxes should follow the same structure as the existing OpenCROW toolbox skills.

### 4. Phase rollout

Phase 1: high-value headless core

- Add the catalog and installer redesign first.
- Add all CLI/headless-capable tools that fit automation cleanly.
- Populate the new toolboxes and extend the existing ones with headless-safe tools.
- Update verify/uninstall to use install state.
- Update README and “Included Skills” to match the expanded toolbox map.

Phase 2: full-profile GUI/restricted tooling

- Add GUI or heavier tools to `full` profile paths: `Autopsy`, `Burp Suite Community`, `Wireshark`, `StegSolve`, `OpenStego`, desktop `ZAP`, and any other supported GUI tool.
- For each such tool, choose one of two outcomes only:
  - automated install in `full`, or
  - explicit manual acquisition step with official download/license links
- No silent “future TODO” handling inside `full`.

## Public Interface Changes

Installer:

- `scripts/install.sh` gains `--toolbox`, `--profile`, `--all-toolboxes`, and `--interactive`
- default behavior becomes interactive on TTY, headless-on-all-toolboxes in non-interactive contexts
- install summary must display third-party/license links before execution

Verifier:

- `scripts/verify.sh` should verify the installed selection by default using saved install state
- add an override to verify all supported tools regardless of install state

Uninstaller:

- `scripts/uninstall.sh` should remove only managed tools recorded in install state by default
- keep a broader cleanup option for “remove all managed OpenCROW tools”

## Test Plan

### Installer behavior

- `bash scripts/install.sh --dry-run` in CI/non-TTY resolves to headless install across all supported toolboxes.
- Interactive TTY path offers exactly `fast install` and `personalized`.
- Fast mode: selecting web + crypto + `headless` produces only those toolboxes and skips full-only GUI tools.
- Personalized mode: selecting a handful of individual tools installs only those tools.

### Selection and state

- Saved install state reproduces the chosen selection for verify and uninstall.
- Re-running install with a different selection updates state cleanly.
- License/homepage links are printed for every selected tool before install starts.

### Toolbox verification

- Each toolbox verifier checks only its mapped tools/modules.
- `verify.sh` reflects the selected install set by default and supports an explicit all-tools mode.
- `sync_skills.sh` still installs the expanded toolbox set and continues removing retired `ctf-tools`.

### Smoke/acceptance

- Existing smoke workflow remains non-interactive and passes via `--dry-run`.
- README examples cover:
  - non-interactive headless install
  - interactive fast install
  - interactive personalized install
  - toolbox-specific installs

## Assumptions and Defaults

- New categories become new toolboxes only when they are genuinely new domains: forensics, stego, OSINT, utility.
- Extra crypto, web, network, pwn, and reversing tools stay in the existing OpenCROW toolboxes.
- `full` includes all supported selected tools, even when they are third-party, provided the installer can show license links and either automate the install or stop at an explicit manual step.
- `headless` excludes GUI-only tools and prefers CLI/open-source paths.
- `personalized` is full per-tool selection.
- `fast install` asks for selected toolboxes plus one global profile that applies to all of them.
