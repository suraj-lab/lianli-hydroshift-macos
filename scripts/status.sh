#!/usr/bin/env bash
set -euo pipefail

LABEL="com.suraj.lianli-hydroshift"

echo "== User LaunchAgent =="
launchctl print "gui/$UID/$LABEL" 2>/dev/null || echo "not loaded"

echo
if [[ -f "/Library/LaunchDaemons/$LABEL.plist" ]]; then
  echo "== System LaunchDaemon =="
  sudo launchctl print "system/$LABEL" 2>/dev/null || echo "not loaded"
else
  echo "== System LaunchDaemon =="
  echo "plist not installed"
fi

echo
pgrep -fl 'lianli_hydroshift.daemon|lianli-hydroshift' || true
