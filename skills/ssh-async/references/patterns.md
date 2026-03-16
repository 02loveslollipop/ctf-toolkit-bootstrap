# SSH Async Patterns

## Common Flows

- Open a remote shell and run commands incrementally:
  - `scripts/sshx start --name app --host <host> --user <user>`
  - `scripts/sshx send --name app --data 'pwd' --newline`
  - `scripts/sshx read --name app --tail 80`
- Tail remote logs through the same shell:
  - `scripts/sshx send --name <name> --data 'tail -f /var/log/syslog' --newline`
  - In another terminal, `scripts/sshx read --name <name> --follow`
- Use a custom identity or SSH option:
  - `scripts/sshx start --name bastion --host <host> --user <user> --identity ~/.ssh/id_ed25519`
  - `scripts/sshx start --name strict --host <host> --option 'LogLevel=ERROR'`

## Recovery

- Session not running:
  - `scripts/sshx status --name <name>`
  - Restart with `start`.
- Authentication or host-key failure:
  - Review `/tmp/codex-ssh-async/<name>/daemon.log`.
  - Review `/tmp/codex-ssh-async/<name>/io.log` for SSH error text.
- Remote shell exited:
  - `io.log` includes `[STATE] ssh exited rc=...`.
  - Start a new session.

## Notes

- This tool assumes non-interactive authentication such as SSH keys or existing agent support.
- `io.log` escapes CR/LF as `\\r` and `\\n` for readable shell transport tracing.
- Full-screen terminal programs will not behave well through this logging-oriented interface.
