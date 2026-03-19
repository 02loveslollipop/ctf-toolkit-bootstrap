#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_NAME="ctf"
REMOVE_ENV=0
PURGE_APT=0
DRY_RUN=0
ALL_MANAGED=0
EXPLICIT_ENV=0
CONDA_BIN=""
TARGET_USER=""
TARGET_HOME=""
TARGET_PATH=""
SELECTION_FILE=""

PWNDBG_VERSION="2026.02.18"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --env NAME       Conda environment name (default: ctf)
  --remove-env     Remove the selected conda environment
  --purge-apt      Purge the apt packages from the saved selection
  --all-managed    Remove all managed OpenCROW tools even without saved state
  --dry-run        Print commands without executing them
EOF
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %q' "$1"
    shift
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
  else
    "$@"
  fi
}

run_shell() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %s\n' "$*"
  else
    bash -lc "$*"
  fi
}

run_as_target() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %q' "$1"
    shift
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi

  if [[ "$(id -un)" != "$TARGET_USER" ]]; then
    sudo -u "$TARGET_USER" env HOME="$TARGET_HOME" PATH="$TARGET_PATH" "$@"
  else
    env HOME="$TARGET_HOME" PATH="$TARGET_PATH" "$@"
  fi
}

capture_as_target() {
  if [[ "$(id -un)" != "$TARGET_USER" ]]; then
    sudo -u "$TARGET_USER" env HOME="$TARGET_HOME" PATH="$TARGET_PATH" "$@"
  else
    env HOME="$TARGET_HOME" PATH="$TARGET_PATH" "$@"
  fi
}

run_as_root() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %q' "$1"
    shift
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi

  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
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
Anaconda or Miniconda is required to remove conda-managed packages, but no conda installation was found.

Download links:
  Miniconda: https://docs.conda.io/en/latest/miniconda.html
  Anaconda:  https://www.anaconda.com/download
EOF
}

unlink_gem_executable() {
  local executable="$1"
  run_as_target rm -f "$TARGET_HOME/.local/bin/${executable}"
}

uninstall_gem_spec() {
  local spec="$1"
  local name="${spec%%:*}"

  unlink_gem_executable "$name"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] gem uninstall %q -aIx\n' "$name"
  else
    capture_as_target gem uninstall "$name" -aIx >/dev/null 2>&1 || true
  fi
}

uninstall_direct_handler() {
  local handler="$1"

  case "$handler" in
    pwninit)
      run_as_target rm -f "$TARGET_HOME/.local/bin/pwninit"
      ;;
    ghidra)
      run_as_target rm -f "$TARGET_HOME/.local/bin/ghidra"
      run_as_target rm -f "$TARGET_HOME/.local/bin/ghidra-headless"
      run_as_target rm -rf "$TARGET_HOME/.local/opt/ghidra"
      if [[ "$DRY_RUN" -eq 1 ]]; then
        printf "[dry-run] rm -rf '%s/.local/opt'/ghidra_*_PUBLIC '%s/.local/opt'/ghidra_*.zip\n" "$TARGET_HOME" "$TARGET_HOME"
      else
        capture_as_target bash -lc "rm -rf '$TARGET_HOME/.local/opt'/ghidra_*_PUBLIC '$TARGET_HOME/.local/opt'/ghidra_*.zip"
      fi
      ;;
    pwndbg)
      run_as_target rm -f "$TARGET_HOME/.local/bin/pwndbg"
      run_as_target rm -rf "$TARGET_HOME/.local/lib/pwndbg-gdb"
      ;;
    opencrow-autosetup)
      run_as_target rm -f "$TARGET_HOME/.local/bin/opencrow-autosetup"
      run_as_target rm -f "$TARGET_HOME/.local/share/bash-completion/completions/opencrow-autosetup"
      run_as_target rm -rf "$TARGET_HOME/.local/opt/opencrow-autosetup"
      ;;
    opencrow-exploit)
      run_as_target rm -f "$TARGET_HOME/.local/bin/opencrow-exploit"
      run_as_target rm -f "$TARGET_HOME/.local/share/bash-completion/completions/opencrow-exploit"
      run_as_target rm -rf "$TARGET_HOME/.local/opt/opencrow-exploit"
      ;;
    opencrow-stego-mcp)
      run_as_target rm -f "$TARGET_HOME/.local/bin/opencrow-stego-mcp"
      run_as_target rm -rf "$TARGET_HOME/.local/opt/opencrow-stego-mcp"
      ;;
    opencrow-forensics-mcp)
      run_as_target rm -f "$TARGET_HOME/.local/bin/opencrow-forensics-mcp"
      run_as_target rm -rf "$TARGET_HOME/.local/opt/opencrow-forensics-mcp"
      ;;
    opencrow-osint-mcp)
      run_as_target rm -f "$TARGET_HOME/.local/bin/opencrow-osint-mcp"
      run_as_target rm -rf "$TARGET_HOME/.local/opt/opencrow-osint-mcp"
      ;;
    opencrow-web-mcp)
      run_as_target rm -f "$TARGET_HOME/.local/bin/opencrow-web-mcp"
      run_as_target rm -rf "$TARGET_HOME/.local/opt/opencrow-web-mcp"
      ;;
    opencrow-netcat-mcp)
      run_as_target rm -f "$TARGET_HOME/.local/bin/opencrow-netcat-mcp"
      run_as_target rm -rf "$TARGET_HOME/.local/opt/opencrow-netcat-mcp"
      ;;
    opencrow-ssh-mcp)
      run_as_target rm -f "$TARGET_HOME/.local/bin/opencrow-ssh-mcp"
      run_as_target rm -rf "$TARGET_HOME/.local/opt/opencrow-ssh-mcp"
      ;;
    opencrow-minecraft-mcp)
      run_as_target rm -f "$TARGET_HOME/.local/bin/opencrow-minecraft-mcp"
      run_as_target rm -rf "$TARGET_HOME/.local/opt/opencrow-minecraft-mcp"
      ;;
    *)
      echo "Unknown direct uninstall handler: $handler" >&2
      exit 2
      ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENV_NAME="$2"
      EXPLICIT_ENV=1
      shift 2
      ;;
    --remove-env)
      REMOVE_ENV=1
      shift
      ;;
    --purge-apt)
      PURGE_APT=1
      shift
      ;;
    --all-managed)
      ALL_MANAGED=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
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

STATE_PATH="$(OPENCROW_HOME="$TARGET_HOME" python3 "$ROOT_DIR/scripts/tool_catalog.py" state-path)"

if [[ "$ALL_MANAGED" -eq 1 ]]; then
  SELECTION_FILE="$(mktemp)"
  trap cleanup EXIT
  OPENCROW_HOME="$TARGET_HOME" python3 "$ROOT_DIR/scripts/tool_catalog.py" resolve-selection --profile full --output "$SELECTION_FILE"
elif [[ -f "$STATE_PATH" ]]; then
  SELECTION_FILE="$STATE_PATH"
  if [[ "$EXPLICIT_ENV" -eq 0 ]]; then
    ENV_NAME="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["env_name"])' "$STATE_PATH")"
  fi
else
  echo "No saved OpenCROW install state was found. Use --all-managed for a broad cleanup." >&2
  exit 1
fi

eval "$(OPENCROW_HOME="$TARGET_HOME" python3 "$ROOT_DIR/scripts/tool_catalog.py" export-plan --selection "$SELECTION_FILE")"

for handler in "${DIRECT_HANDLERS[@]}"; do
  uninstall_direct_handler "$handler"
done

for spec in "${GEM_SPECS[@]}"; do
  uninstall_gem_spec "$spec"
done

if find_conda; then
  if [[ ${#PIP_PACKAGES[@]} -gt 0 ]]; then
    PIP_REMOVE=()
    for spec in "${PIP_PACKAGES[@]}"; do
      PIP_REMOVE+=("${spec%%==*}")
    done
    run_as_target "$CONDA_BIN" run -n "$ENV_NAME" pip uninstall -y "${PIP_REMOVE[@]}"
  fi
  if [[ "$REMOVE_ENV" -eq 1 ]]; then
    run_as_target "$CONDA_BIN" env remove -n "$ENV_NAME" -y
  fi
elif [[ "$REMOVE_ENV" -eq 1 || ${#PIP_PACKAGES[@]} -gt 0 ]]; then
  print_conda_install_help
fi

run_as_target env OPENCROW_HOME="$TARGET_HOME" bash "$ROOT_DIR/scripts/remove_skills.sh"

if [[ "$PURGE_APT" -eq 1 ]]; then
  run_as_root apt-get purge -y "${APT_PACKAGES[@]}"
  run_as_root apt-get autoremove -y
fi

if [[ -f "$STATE_PATH" && "$DRY_RUN" -eq 0 ]]; then
  run_as_target rm -f "$STATE_PATH"
fi

echo
echo "Uninstall complete."
