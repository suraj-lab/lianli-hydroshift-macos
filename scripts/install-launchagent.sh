#!/usr/bin/env bash
# Install as a user LaunchAgent: starts at login, runs as your user, and restarts on crash.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Run this script as your normal user, not with sudo." >&2
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.suraj.lianli-hydroshift"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$PROJECT_DIR/launchd/$LABEL.agent.plist.template"
CONFIG="$HOME/.config/lianli-hydroshift/config.json"

cd "$PROJECT_DIR"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt

if [[ ! -f "$CONFIG" ]]; then
  .venv/bin/python -m lianli_hydroshift.daemon --write-default-config --config "$CONFIG"
fi

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile lianli_hydroshift/daemon.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/render-plist.py "$TEMPLATE" "$PLIST"
plutil -lint "$PLIST"

launchctl bootout "gui/$UID" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl enable "gui/$UID/$LABEL"
launchctl kickstart -k "gui/$UID/$LABEL"

launchctl print "gui/$UID/$LABEL"
