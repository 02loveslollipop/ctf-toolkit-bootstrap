#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_HOME="${OPENCROW_HOME:-$HOME}"
TARGET_DIR="${TARGET_HOME}/.codex/skills"
DRY_RUN=0
RETIRED_SKILLS=(
  "ctf-tools"
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [--dry-run]

Remove the skills vendored by this repo from ~/.codex/skills.
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

while [[ $# -gt 0 ]]; do
  case "$1" in
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

for skill_name in "${RETIRED_SKILLS[@]}"; do
  run rm -rf "$TARGET_DIR/$skill_name"
done

for skill_dir in "$ROOT_DIR"/skills/*; do
  [[ -d "$skill_dir" ]] || continue
  skill_name="$(basename "$skill_dir")"
  run rm -rf "$TARGET_DIR/$skill_name"
done

echo "Vendored skills removed from $TARGET_DIR"
