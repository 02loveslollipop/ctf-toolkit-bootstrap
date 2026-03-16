#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_NAME="ctf"
CONDA_BIN=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--env NAME]
EOF
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
Anaconda or Miniconda is required, but no conda installation was found.

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

if ! find_conda; then
  print_conda_install_help
  exit 1
fi

echo "[python modules in ${ENV_NAME}]"
"$CONDA_BIN" run -n "$ENV_NAME" python -c '
import importlib.util as u
mods = [
    "z3",
    "pwn",
    "angr",
    "claripy",
    "capstone",
    "unicorn",
    "keystone",
    "ropper",
    "r2pipe",
    "lief",
    "scapy",
    "fpylll",
]
for mod in mods:
    print(f"{mod}: {bool(u.find_spec(mod))}")
'

echo
echo "[native tools]"
for tool in pwndbg pwninit seccomp-tools ghidra-headless ghidra gdb checksec patchelf r2 qemu-aarch64 objdump strace ltrace binwalk nasm; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "$tool: yes"
  else
    echo "$tool: no"
  fi
done

echo
echo "[system python modules]"
if python3 -c 'import Xlib' >/dev/null 2>&1; then
  echo "python3-xlib: yes"
else
  echo "python3-xlib: no"
fi
