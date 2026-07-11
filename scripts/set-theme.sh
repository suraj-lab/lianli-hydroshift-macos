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
  # ponytail: osascript admin dialog instead of terminal+sendo
  osascript -e "do shell script \"launchctl kill SIGHUP system/$LABEL\" with administrator privileges" 2>/dev/null && exit 0
  # fallback: try pkill if launchctl isn't available
  pkill -HUP -f 'lianli_hydroshift.daemon' 2>/dev/null && exit 0
  echo "Warning: could not signal running daemon — restart it manually." >&2
  exit 1
fi
