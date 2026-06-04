#!/usr/bin/env bash
# Install as a system LaunchDaemon: starts at macOS boot, before login, and restarts on crash.
# This requires sudo because /Library/LaunchDaemons is system-managed.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Run this script as your normal user, not with sudo. It will ask sudo only for launchd installation steps." >&2
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.suraj.lianli-hydroshift"
TMP_PLIST="$PROJECT_DIR/.generated.$LABEL.plist"
DEST_PLIST="/Library/LaunchDaemons/$LABEL.plist"
TEMPLATE="$PROJECT_DIR/launchd/$LABEL.plist.template"
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
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/render-plist.py "$TEMPLATE" "$TMP_PLIST"
plutil -lint "$TMP_PLIST"

sudo launchctl bootout system "$DEST_PLIST" >/dev/null 2>&1 || true
sudo cp "$TMP_PLIST" "$DEST_PLIST"
sudo chown root:wheel "$DEST_PLIST"
sudo chmod 0644 "$DEST_PLIST"
sudo launchctl bootstrap system "$DEST_PLIST"
sudo launchctl enable "system/$LABEL"
sudo launchctl kickstart -k "system/$LABEL"

sudo launchctl print "system/$LABEL"
