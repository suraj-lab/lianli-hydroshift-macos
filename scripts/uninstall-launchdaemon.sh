#!/usr/bin/env bash
set -euo pipefail

LABEL="com.suraj.lianli-hydroshift"
PLIST="/Library/LaunchDaemons/$LABEL.plist"

sudo launchctl bootout system "$PLIST" >/dev/null 2>&1 || true
sudo rm -f "$PLIST"
echo "Uninstalled system LaunchDaemon $LABEL"
