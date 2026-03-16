# Minecraft Async Reference

## Install Layout

The skill assumes a normal Java edition install rooted at `~/.minecraft`:

- `versions/<id>/<id>.json`: version manifest used by the direct launcher
- `versions/<id>/<id>.jar`: client JAR
- `libraries/`: dependency JARs and native JARs
- `assets/indexes/`: asset indexes
- `logs/latest.log`: primary game log
- `launcher_log.txt`: official launcher log, useful only for cross-checking existing installs

The direct backend does not require the official launcher at runtime.

## Backend Selection

### `direct`

Use this by default.

- Launches the installed client from the existing `~/.minecraft` tree
- Supports offline usernames
- Supports launch-time multiplayer quick play via `join-server`
- Supports launch-time singleplayer quick play via `join-world`
- Keeps managed session metadata under `/tmp/codex-minecraft-async/<session>/`

### `cmd-launcher`

Use this only when `cmd-launcher` is already installed and you want its instance workflow.

- `cmd-launcher` is optional and not bundled by this skill
- It supports offline usernames via `start -u/--username`
- It manages its own instances rather than reusing the vanilla launcher state directly
- The skill only wraps basic `cmd-launcher start` launching; server quick-play is still handled by the direct backend

## X11 Control

Window automation is implemented with Python Xlib and the XTEST extension:

- `focus`: activate the Minecraft window
- `chat`: focus, press `t`, type text, press `Return`
- `command`: focus, press `/`, type text, press `Return`
- `send-text`: focus and type into the current text field
- `screenshot`: capture the current Minecraft window to a PNG with ImageMagick `import`

Assumptions:

- `DISPLAY` points at an active X11 session
- The Minecraft window title or class contains `minecraft` or `GLFW`
- The active keyboard layout is close to US for punctuation-heavy input

## Logs

`scripts/mcx read-log` supports:

- `latest`: `~/.minecraft/logs/latest.log`
- `launcher`: managed launcher stdout/stderr log under `/tmp/codex-minecraft-async/<session>/launcher.log`
- `both`: latest game log followed by managed launcher log

If a launch fails:

1. Check `launcher.log` first for missing files, bad Java paths, or bad arguments.
2. Check `latest.log` for in-game disconnects, authentication mode, and quick-play behavior.
3. Re-run `status` to confirm the process and window state.

## Save Names

`join-world --world <name>` expects the exact directory name under `~/.minecraft/saves/`.

Use `scripts/mcx status` to list discovered saves before launching into one.
