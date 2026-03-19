#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_installer_bootstrap.sh"

run_installer_subcommand headless-install "$@"
