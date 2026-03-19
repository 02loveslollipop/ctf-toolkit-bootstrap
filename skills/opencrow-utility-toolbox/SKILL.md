---
name: opencrow-utility-toolbox
description: Use the OpenCROW utility stack for shell-heavy CTF workflows. Use when Codex needs `jq`, `yq`, `xxd`, `tmux`, `screen`, `rg`, or `fzf` to glue a larger workflow together.
---

# OpenCROW Utility Toolbox

Use this skill when the blocker is shell ergonomics or structured-data processing rather than a challenge-specific exploit primitive. It covers the “glue” layer that makes larger CTF workflows faster: `jq`, `yq`, `xxd`, `tmux`, `screen`, `ripgrep`, and `fzf`.

## Quick Start

Verify the mapped stack:

```bash
python ~/.codex/skills/opencrow-utility-toolbox/scripts/verify_toolkit.py
```

## Workflow

1. Use `jq` or `yq` when API responses, config files, or challenge metadata need slicing before deeper analysis.
2. Use `xxd` when you need a fast hex dump or round-trip conversion in shell pipelines.
3. Use `tmux` or `screen` when the task benefits from persistent panes or background sessions.
4. Use `rg` and `fzf` to navigate large workspaces or challenge bundles quickly.

## Resources

- `scripts/verify_toolkit.py`: confirm that the mapped workflow helpers are installed.
- `references/tooling.md`: quick guidance for common shell utility choices.
