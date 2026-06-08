#!/usr/bin/env bash
# Remove the user LaunchAgent that starts OpenRGB with the HydroShift profile.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Run this script as your normal user, not with sudo." >&2
  exit 1
fi

LABEL="org.openrgb"
PLIST="$HOME/Library/LaunchAgents/OpenRGB.plist"
SCRIPT="$HOME/Library/Scripts/start-openrgb-profile.sh"
UID_NUM="$(id -u)"

launchctl bootout "gui/$UID_NUM" "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST" "$SCRIPT"

echo "Removed OpenRGB profile LaunchAgent and wrapper."
