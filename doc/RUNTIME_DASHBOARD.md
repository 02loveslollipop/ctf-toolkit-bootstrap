# OpenCROW Runtime Dashboard

This document describes the new dashboard-managed execution path.

For the full software design, code-level implementation notes, and Mermaid
C4/class/sequence diagrams, see
[RUNTIME_DASHBOARD_ARCHITECTURE.md](RUNTIME_DASHBOARD_ARCHITECTURE.md).
For the three-category runtime validation run, see
[RUNTIME_DEEP_SMOKE_TEST.md](RUNTIME_DEEP_SMOKE_TEST.md).
For the real AlpacaHack solve and writeup artifact validation, see
[RUNTIME_WRITEUP_SOLVE_TEST.md](RUNTIME_WRITEUP_SOLVE_TEST.md).

## Runtime Model

Constellation remains the control plane. MongoDB/GridFS store challenges, files,
runtimes, agents, commands, and agent events. The Codex runtime is a separate
host process and is intentionally not containerized.

Start the control plane as before:

```bash
docker compose -f docker-compose.constellation.yml up --build
```

Start a host runtime on a machine with Codex, OpenCROW skills, and MCP tools
installed:

```bash
opencrow-constellation-runtime
```

The runtime connects outbound to Constellation over `/runtime/ws`, registers
its capabilities, receives command messages, creates host workspaces, and runs
Codex through the Python SDK.

The Python SDK is intentionally loaded lazily by the runtime because the SDK is
currently experimental and may be installed from an SDK checkout or a published
wheel when available. If `openai_codex` is missing, the runtime registers
successfully but agent execution fails with an explicit SDK-missing error.

Key runtime environment variables:

- `OPENCROW_RUNTIME_CONTROL_API_BASE_URL`
- `OPENCROW_RUNTIME_CONTROL_WS_BASE_URL`
- `OPENCROW_RUNTIME_TOKEN`
- `OPENCROW_RUNTIME_ID`
- `OPENCROW_RUNTIME_DISPLAY_NAME`
- `OPENCROW_RUNTIME_WORKSPACE_ROOT`
- `OPENCROW_RUNTIME_CODEX_MODEL`
- `OPENCROW_RUNTIME_CODEX_BIN`

## Challenge Types

- `single_agent`: creating the challenge starts one solo agent that plans and
  executes.
- `constellation`: creating the challenge starts a master agent. Additional
  slave agents can be spawned from the dashboard.

Single-agent challenges can be converted to Constellation mode. The existing
agent is kept and promoted to master.

## Compatibility Commands

The legacy terminal entrypoints are now thin dashboard clients:

```bash
opencrow-autosetup --category web
opencrow-exploit --model gpt-5.4
opencrow-constellation-join challenge-web-1
```

They create dashboard-managed challenges through the Constellation API and let
the registered runtime own Codex execution. Use `--dry-run` to inspect the
payload without creating a challenge, or `--no-upload` to skip uploading the
current workspace archive.
