#!/usr/bin/env bash
# Remove the user LaunchAgent that starts HydroShift.app at login.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Run this script as your normal user, not with sudo." >&2
  exit 1
fi

LABEL="com.suraj.hydroshift-app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

launchctl bootout "gui/$UID_NUM" "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST"

echo "Removed HydroShift.app LaunchAgent."
