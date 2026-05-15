# Runtime Writeup Solve Test

This note records the real solve/writeup validation run performed on May 15, 2026. Unlike the earlier deep smoke test, this run required an agent to recover an exact flag and create a fresh writeup artifact that is visible through the dashboard-managed platform.

## Why This Test Was Added

The runtime-dashboard flow already persisted agent state, event streams, and final responses. Before this test, however, a `writeup.md` created by Codex would remain only in the runtime workspace. That was not enough to validate dashboard writeup functionality, because operators and coordinating agents need an API-visible artifact.

The implementation now captures runtime writeup files after an agent turn completes:

1. The runtime scans the agent workspace for `writeup.md`, `WRITEUP.md`, `solution.md`, or `SOLUTION.md`.
2. Matching files up to 2 MB are uploaded to `/api/v1/agents/{agent_id}/artifacts`.
3. The backend stores them in the `agent_artifacts` GridFS bucket.
4. The backend creates artifact metadata documents and emits `agent_artifact` events.
5. The challenge UI lists artifacts per agent and proxies downloads through `/agent-artifacts/{file_id}`.

## Test Target

The selected AlpacaHack fixture was:

```text
/home/zerotwo/.local/share/Trash/files/alpaca-permission/permission-denied-2
```

It was selected because it is small and has a real AlpacaHack flag in the available challenge notes:

```text
Alpaca{h4s_c0mpl3t3_p3rm1ss10ns_0v3r_3v3ry7h1ng}
```

The challenge files used by the runtime workspace were:

```text
Dockerfile
WRITEUP.md
chal.sh
compose.yaml
solver.py
```

## Runtime Setup

The stack was started locally:

```bash
docker compose -f docker-compose.constellation.yml up -d mongo mongo-init-replica
```

The backend used host-side Mongo access:

```bash
OPENCROW_CONSTELLATION_MONGO_URI='mongodb://127.0.0.1:27017/?directConnection=true' \
OPENCROW_CONSTELLATION_BACKEND_HOST=127.0.0.1 \
OPENCROW_CONSTELLATION_BACKEND_PORT=8787 \
python3 -m constellation.backend
```

The host runtime was:

```bash
OPENCROW_RUNTIME_CONTROL_API_BASE_URL=http://127.0.0.1:8787 \
OPENCROW_RUNTIME_CONTROL_WS_BASE_URL=ws://127.0.0.1:8787 \
OPENCROW_RUNTIME_ID=alpaca-writeup-smoke \
OPENCROW_RUNTIME_DISPLAY_NAME='Alpaca Writeup Runtime' \
OPENCROW_RUNTIME_WORKSPACE_ROOT=/tmp/opencrow-runtime-alpaca-writeup \
OPENCROW_RUNTIME_CODEX_MODEL=gpt-5.4-mini \
python3 -m constellation.runtime
```

The runtime registered with `codex_sdk: true`.

## Created Challenge

| Field | Value |
| --- | --- |
| Challenge slug | `alpaca-writeup-permission-denied-2` |
| Challenge id | `6a067b8759ceef2263b6bd0c` |
| Agent id | `6a067b8759ceef2263b6bd10` |
| Runtime id | `alpaca-writeup-smoke` |
| Uploaded archive | `opencrow-permission-denied-2-fkkoxhgq.zip` |
| Archive size | 1,835 bytes |

The solve prompt required:

- End-to-end solve.
- Exact flag recovery.
- A fresh root-level `writeup.md`.
- Sections: `Summary`, `Vulnerability`, `Exploit Steps`, `Flag`, `Reproduction`.
- A final response line beginning with `FLAG:`.

## Result

The agent completed successfully:

| Field | Value |
| --- | --- |
| Agent status | `completed` |
| Codex thread id | `019e2952-9067-76b3-accc-34e07359de07` |
| Runtime command status | `completed` |
| Runtime command id | `6a067b8759ceef2263b6bd12` |
| Command error | `null` |
| Workspace | `/tmp/opencrow-runtime-alpaca-writeup/alpaca-writeup-permission-denied-2/6a067b8759ceef2263b6bd10` |
| Captured artifacts | 2 |

The final agent response ended with:

```text
FLAG: Alpaca{h4s_c0mpl3t3_p3rm1ss10ns_0v3r_3v3ry7h1ng}
```

## Captured Artifacts

The platform captured both the newly created `writeup.md` and the original `WRITEUP.md` shipped with the fixture:

| Name | Artifact type | File id | Size | SHA-256 |
| --- | --- | --- | ---: | --- |
| `writeup.md` | `writeup` | `6a067c0e59ceef2263b6bebd` | 1,583 bytes | `1a1a635a74b55c5d6afd610a062ea479a3edd1da7851d225e61ac1e9cb8d6bfd` |
| `WRITEUP.md` | `writeup` | `6a067c0f59ceef2263b6bec0` | 829 bytes | `674909a354757f2d4c65b2dbe15169b46fcb10bff6e1357ebb29c49b71526b0a` |

The artifact API returned:

```json
{
  "ok": true,
  "artifacts": [
    {
      "name": "writeup.md",
      "artifact_type": "writeup",
      "file_id": "6a067c0e59ceef2263b6bebd",
      "size": 1583
    },
    {
      "name": "WRITEUP.md",
      "artifact_type": "writeup",
      "file_id": "6a067c0f59ceef2263b6bec0",
      "size": 829
    }
  ]
}
```

The downloaded `writeup.md` contained the required sections and the recovered flag:

```text
# Summary
The service starts `bash chal.sh` as root for every TCP connection. The challenge directory is `/home/alpaca`, which is writable by the `alpaca` user, so a shell obtained from one connection can delete and replace `chal.sh`. A later connection then runs the attacker-controlled script as root and prints the flag.

# Vulnerability
- `Dockerfile` creates the `alpaca` user, sets `WORKDIR /home/alpaca`, and only makes `chal.sh` read-only with `COPY --chmod=400`.
- `chal.sh` creates `flag.txt` as root, then drops into an `alpaca` shell with `runuser -u alpaca -- sh`.
- Because the working directory is still writable, the unprivileged shell can remove `chal.sh` and create a new file with the same name.
- The next connection executes `bash chal.sh`, so replacing that file changes the command root runs.

# Exploit Steps
1. Connect to the challenge and wait for the `alpaca` shell.
2. Remove the shipped script:
   rm -f chal.sh
3. Replace it with a script that reads the root-created flag:
   printf 'cat flag.txt\n' > chal.sh
4. Exit the shell.
5. Reconnect. The service runs the replacement `chal.sh` as root, and it prints `flag.txt`.

# Flag
`Alpaca{h4s_c0mpl3t3_p3rm1ss10ns_0v3r_3v3ry7h1ng}`

# Reproduction
1. Start the container from `compose.yaml`, which exposes the service on port `1337`.
2. Connect once with `nc localhost 1337`.
3. In the shell, replace `chal.sh` with a script that prints `flag.txt`.
4. Connect again with `nc localhost 1337`.
5. The second connection prints the flag above.
```

## Event Evidence

The final event window included:

```text
agent_state
agent_artifact
agent_artifact
writeup_artifacts_uploaded
```

This confirms the runtime turn completed, the backend persisted the artifacts, and the runtime emitted an upload summary event.

## Coverage

This test covered:

- Real AlpacaHack flag recovery.
- Runtime workspace materialization from an uploaded challenge archive.
- Codex SDK agent execution through the dashboard runtime.
- Fresh writeup creation in the workspace.
- Runtime-side writeup discovery.
- Agent artifact upload through the backend API.
- GridFS persistence for writeup artifacts.
- Artifact listing through `/api/v1/agents/{agent_id}/artifacts`.
- Artifact download through `/api/v1/agent-artifacts/{file_id}`.
- Event timeline entries for artifact capture.
- Final response flag persistence in `agents.last_response`.

This gives a concrete regression target for future writeup handling changes.
