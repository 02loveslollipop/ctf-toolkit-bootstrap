#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
if [[ -z "$TARGET_HOME" ]]; then
  TARGET_HOME="$HOME"
fi

INSTALLER_VENV="$TARGET_HOME/.cache/opencrow/installer-venv"
INSTALLER_PY="$INSTALLER_VENV/bin/python"
STAMP_FILE="$INSTALLER_VENV/.requirements.sha256"
REQ_FILE="$ROOT_DIR/requirements-installer.txt"
REQ_HASH="$(sha256sum "$REQ_FILE" | awk '{print $1}')"

run_as_target() {
  if [[ "$(id -un)" != "$TARGET_USER" ]]; then
    sudo -u "$TARGET_USER" env HOME="$TARGET_HOME" PATH="$TARGET_HOME/.local/bin:$PATH" "$@"
  else
    env HOME="$TARGET_HOME" PATH="$TARGET_HOME/.local/bin:$PATH" "$@"
  fi
}

run_shell_as_target() {
  if [[ "$(id -un)" != "$TARGET_USER" ]]; then
    sudo -u "$TARGET_USER" env HOME="$TARGET_HOME" PATH="$TARGET_HOME/.local/bin:$PATH" bash -lc "$*"
  else
    env HOME="$TARGET_HOME" PATH="$TARGET_HOME/.local/bin:$PATH" bash -lc "$*"
  fi
}

ensure_installer_venv() {
  run_as_target mkdir -p "$TARGET_HOME/.cache/opencrow"
  if [[ ! -x "$INSTALLER_PY" ]]; then
    run_as_target python3 -m venv "$INSTALLER_VENV"
  fi

  if [[ ! -f "$STAMP_FILE" ]] || [[ "$(cat "$STAMP_FILE")" != "$REQ_HASH" ]]; then
    run_as_target "$INSTALLER_PY" -m pip install --upgrade pip
    run_as_target "$INSTALLER_PY" -m pip install -r "$REQ_FILE"
    run_shell_as_target "printf '%s' '$REQ_HASH' > '$STAMP_FILE'"
  fi
}

run_installer_subcommand() {
  local subcommand="$1"
  shift
  ensure_installer_venv
  exec "$INSTALLER_PY" "$ROOT_DIR/scripts/install_cli.py" "$subcommand" "$@"
}
