#!/usr/bin/env bash
# Send CMD_REBOOT to the HydroShift II LCD via its direct USB connection.
# Uses the WinUSB LCD protocol (DES-CBC) reverse-engineered by sgtaziz/lian-li-linux.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
exec sudo .venv/bin/python scripts/lcd-reboot.py "$@"
