#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_NAME="ctf"
CONDA_BIN=""
TARGET_USER=""
TARGET_HOME=""
TARGET_PATH=""
ALL_TOOLS=0
EXPLICIT_ENV=0
SELECTION_FILE=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--env NAME] [--all-tools]
EOF
}

cleanup() {
  [[ -n "$SELECTION_FILE" && -f "$SELECTION_FILE" ]] && rm -f "$SELECTION_FILE"
}

resolve_target_identity() {
  local passwd_home

  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    TARGET_USER="$SUDO_USER"
  else
    TARGET_USER="$(id -un)"
  fi

  passwd_home="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
  if [[ -n "$passwd_home" ]]; then
    TARGET_HOME="$passwd_home"
  else
    TARGET_HOME="$HOME"
  fi

  TARGET_PATH="$TARGET_HOME/.local/bin:$PATH"
}

capture_as_target() {
  if [[ "$(id -un)" != "$TARGET_USER" ]]; then
    sudo -u "$TARGET_USER" env HOME="$TARGET_HOME" PATH="$TARGET_PATH" "$@"
  else
    env HOME="$TARGET_HOME" PATH="$TARGET_PATH" "$@"
  fi
}

find_conda() {
  local candidate

  if [[ "$(id -un)" == "$TARGET_USER" ]] && command -v conda >/dev/null 2>&1; then
    CONDA_BIN="$(command -v conda)"
    return 0
  fi

  for candidate in \
    "$TARGET_HOME/miniconda3/bin/conda" \
    "$TARGET_HOME/anaconda3/bin/conda" \
    "/opt/miniconda3/bin/conda" \
    "/opt/anaconda3/bin/conda"
  do
    if [[ -x "$candidate" ]]; then
      CONDA_BIN="$candidate"
      return 0
    fi
  done

  return 1
}

print_conda_install_help() {
  cat >&2 <<'EOF'
Anaconda or Miniconda is required, but no conda installation was found.

Download links:
  Miniconda: https://docs.conda.io/en/latest/miniconda.html
  Anaconda:  https://www.anaconda.com/download
EOF
}

module_present() {
  local module_name="$1"
  capture_as_target "$CONDA_BIN" run -n "$ENV_NAME" python -c "import importlib.util as u, sys; sys.exit(0 if u.find_spec('$module_name') else 1)"
}

conda_command_present() {
  local command_name="$1"
  capture_as_target "$CONDA_BIN" run -n "$ENV_NAME" python -c "import shutil, sys; sys.exit(0 if shutil.which('$command_name') else 1)"
}

target_command_present() {
  local command_name="$1"
  capture_as_target bash -lc "command -v \"$command_name\" >/dev/null 2>&1"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENV_NAME="$2"
      EXPLICIT_ENV=1
      shift 2
      ;;
    --all-tools)
      ALL_TOOLS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

resolve_target_identity

if ! find_conda; then
  print_conda_install_help
  exit 1
fi

STATE_PATH="$(OPENCROW_HOME="$TARGET_HOME" python3 "$ROOT_DIR/scripts/tool_catalog.py" state-path)"

if [[ "$ALL_TOOLS" -eq 1 ]]; then
  SELECTION_FILE="$(mktemp)"
  trap cleanup EXIT
  OPENCROW_HOME="$TARGET_HOME" python3 "$ROOT_DIR/scripts/tool_catalog.py" resolve-selection --profile full --output "$SELECTION_FILE"
elif [[ -f "$STATE_PATH" ]]; then
  SELECTION_FILE="$STATE_PATH"
  if [[ "$EXPLICIT_ENV" -eq 0 ]]; then
    ENV_NAME="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["env_name"])' "$STATE_PATH")"
  fi
else
  SELECTION_FILE="$(mktemp)"
  trap cleanup EXIT
  OPENCROW_HOME="$TARGET_HOME" python3 "$ROOT_DIR/scripts/tool_catalog.py" resolve-selection --profile headless --output "$SELECTION_FILE"
fi

echo "[managed OpenCROW tools in ${ENV_NAME}]"
while IFS='|' read -r tool_id display_name install_kind verify_kind verify_value; do
  [[ -n "$tool_id" ]] || continue
  case "$verify_kind" in
    module)
      if module_present "$verify_value" >/dev/null 2>&1; then
        echo "${display_name}: yes"
      else
        echo "${display_name}: no"
      fi
      ;;
    command)
      if [[ "$install_kind" == "pip" ]]; then
        if conda_command_present "$verify_value" >/dev/null 2>&1; then
          echo "${display_name}: yes"
        else
          echo "${display_name}: no"
        fi
      else
        if target_command_present "$verify_value" >/dev/null 2>&1; then
          echo "${display_name}: yes"
        else
          echo "${display_name}: no"
        fi
      fi
      ;;
    *)
      echo "${display_name}: unknown verify kind ${verify_kind}"
      ;;
  esac
done < <(OPENCROW_HOME="$TARGET_HOME" python3 "$ROOT_DIR/scripts/tool_catalog.py" export-verify-table --selection "$SELECTION_FILE")

echo
echo "[system python modules]"
if python3 -c 'import Xlib' >/dev/null 2>&1; then
  echo "python3-xlib: yes"
else
  echo "python3-xlib: no"
fi
