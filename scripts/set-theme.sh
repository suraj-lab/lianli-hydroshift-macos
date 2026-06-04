#!/usr/bin/env bash
# Update theme_index in config and optionally signal the running daemon to reload.
# Usage: set-theme.sh <theme_index> [--reload]
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$HOME/.config/lianli-hydroshift/config.json"
LABEL="com.suraj.lianli-hydroshift"

if [[ $# -lt 1 ]]; then
  echo "Usage: set-theme.sh <theme_index> [--reload]" >&2
  exit 1
fi

THEME="$1"
RELOAD=false
if [[ "${2:-}" == "--reload" ]]; then
  RELOAD=true
fi

cd "$PROJECT_DIR"
.venv/bin/python -m lianli_hydroshift.daemon --config "$CONFIG" --set-theme "$THEME"

if $RELOAD; then
  echo "Sending SIGHUP to running daemon..."
  if sudo launchctl kill SIGHUP "system/$LABEL" 2>/dev/null; then
    echo "Reloaded via launchd."
  elif pkill -HUP -f 'lianli_hydroshift.daemon' 2>/dev/null; then
    echo "Reloaded via pkill."
  else
    echo "Warning: could not signal running daemon — restart it manually." >&2
  fi
fi
