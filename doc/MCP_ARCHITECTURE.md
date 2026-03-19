# OpenCROW MCP Architecture

This document defines the contract for OpenCROW toolbox MCP servers.

## Principles

- One MCP server per toolbox.
- Python stdio transport is the v1 baseline.
- Servers must be provider-neutral and consumable by Codex, Claude Code, Gemini, Copilot, and other MCP-capable clients.
- Toolbox servers must expose typed domain tools, not a generic shell-exec surface.
- Tool names, input shapes, and response envelopes must be stable across toolboxes.
- Each operation must surface the underlying command or execution summary when applicable.
- Missing dependencies, missing credentials, invalid inputs, and timeouts must return structured error envelopes instead of raw tracebacks.

## Common Tools

Every toolbox server must expose the same common tools:

- `toolbox_info`
- `toolbox_verify`
- `toolbox_capabilities`

These tools must keep the same semantics across all toolboxes.

## Response Envelope

Every MCP tool call returns a single JSON object encoded as text content with the following keys:

- `ok`
- `summary`
- `toolbox`
- `operation`
- `inputs`
- `artifacts`
- `observations`
- `command`
- `stdout`
- `stderr`
- `exit_code`
- `next_steps`

The envelope is the canonical contract. The human-readable text in `summary` is only a compact view of the structured result.

## Input Shape

- Inputs must be explicit and typed.
- Paths, URLs, hostnames, queries, wordlists, plugins, and modes must be first-class arguments.
- Tool-specific escape hatches are allowed only as constrained typed fields, not raw shell strings.
- Long-running tools may accept an optional `execution` object with:
  - `cwd`
  - `timeout_sec`

## Execution Rules

- Servers should prefer the installed native CLI or the managed `ctf` conda environment when a dependency lives there.
- Tool wrappers must preserve reproducibility by reporting the executed command.
- Server behavior must be deterministic for the same inputs and environment.
- Stdout and stderr should be captured and returned in bounded form.

## Wave 1 Order

The first migration wave uses this order:

1. `opencrow-stego-toolbox`
2. `opencrow-forensics-toolbox`
3. `opencrow-osint-toolbox`
4. `opencrow-web-toolbox`

These are the smallest current toolboxes and are suitable for establishing the shared contract before migrating the Python-heavy or exploit-heavy toolboxes.
