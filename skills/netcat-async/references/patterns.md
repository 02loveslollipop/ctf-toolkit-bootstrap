# Netcat Async Patterns

## Common Flows

- Banner grab:
  - `scripts/ncx start --name banner --host <host> --port <port>`
  - `scripts/ncx read --name banner --tail 50`
- Request/response loop:
  - `scripts/ncx send --name <name> --data '<payload>' --newline`
  - `scripts/ncx read --name <name> --tail 80`
- Live monitoring while sending from another terminal:
  - Terminal A: `scripts/ncx read --name <name> --follow`
  - Terminal B: `scripts/ncx send --name <name> --data '...' --newline`

## Recovery

- Session not running:
  - `scripts/ncx status --name <name>`
  - Restart with `start`.
- Connection closed by remote:
  - `io.log` includes `[STATE] remote closed`.
  - Start a new session.
- Start fails:
  - Review `/tmp/codex-nc-async/<name>/daemon.log`.

## Notes

- This tool is TCP-focused (like default `nc` mode).
- `io.log` escapes CR/LF as `\\r` and `\\n` for readable transport-level tracing.
