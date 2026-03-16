#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="ctf"
REMOVE_ENV=0
PURGE_APT=0
DRY_RUN=0
CONDA_BIN=""

APT_PACKAGES=(
  checksec
  curl
  gdb
  gdbserver
  git
  ltrace
  nasm
  openjdk-21-jre
  patchelf
  python3-xlib
  qemu-user
  qemu-user-static
  radare2
  rsync
  ruby
  unzip
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [--env NAME] [--remove-env] [--purge-apt] [--dry-run]

Options:
  --env NAME     Conda environment name (default: ctf)
  --remove-env   Remove the conda environment
  --purge-apt    Remove the apt packages installed by the bootstrap
  --dry-run      Print commands without executing them
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

find_conda() {
  local candidate

  if command -v conda >/dev/null 2>&1; then
    CONDA_BIN="$(command -v conda)"
    return 0
  fi

  for candidate in \
    "$HOME/miniconda3/bin/conda" \
    "$HOME/anaconda3/bin/conda" \
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
Anaconda or Miniconda is required to remove a conda environment, but no conda installation was found.

Download links:
  Miniconda: https://docs.conda.io/en/latest/miniconda.html
  Anaconda:  https://www.anaconda.com/download
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENV_NAME="$2"
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

run rm -f "$HOME/.local/bin/pwndbg"
run rm -f "$HOME/.local/bin/pwninit"
run rm -f "$HOME/.local/bin/seccomp-tools"
run rm -f "$HOME/.local/bin/ghidra"
run rm -f "$HOME/.local/bin/ghidra-headless"

run rm -rf "$HOME/.local/lib/pwndbg-gdb"
run rm -rf "$HOME/.local/opt/ghidra"
run_shell "rm -rf '$HOME/.local/opt'/ghidra_*_PUBLIC '$HOME/.local/opt'/ghidra_*.zip"
run bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/remove_skills.sh"

if [[ "$REMOVE_ENV" -eq 1 ]]; then
  if ! find_conda; then
    print_conda_install_help
    exit 1
  fi
  run "$CONDA_BIN" env remove -n "$ENV_NAME" -y
fi

if [[ "$PURGE_APT" -eq 1 ]]; then
  run sudo apt-get purge -y "${APT_PACKAGES[@]}"
  run sudo apt-get autoremove -y
fi

echo
echo "Uninstall complete."
