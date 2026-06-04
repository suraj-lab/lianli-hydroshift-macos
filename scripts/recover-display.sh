#!/usr/bin/env bash
# Send repeated wireless-switch commands to recover a corrupted AIO display state.
# Stops the running daemon first so it isn't holding the USB dongles.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.suraj.lianli-hydroshift"

echo "Stopping daemon (if running)..."
sudo launchctl kill SIGTERM "system/$LABEL" 2>/dev/null || true
sleep 1

cd "$PROJECT_DIR"
sudo .venv/bin/python scripts/recover-display.py "$@"
