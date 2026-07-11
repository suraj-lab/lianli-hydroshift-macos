#!/usr/bin/env bash
# Install a user LaunchAgent that starts HydroShift.app at login.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Run this script as your normal user, not with sudo." >&2
  exit 1
fi

LABEL="com.suraj.hydroshift-app"
APP_BIN="/Applications/HydroShift.app/Contents/MacOS/HydroShift"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ ! -x "$APP_BIN" ]]; then
  echo "HydroShift.app not found at /Applications — build/install it first (app/build-app.sh)." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$APP_BIN</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
PLIST

plutil -lint "$PLIST"

UID_NUM="$(id -u)"
launchctl bootout "gui/$UID_NUM" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

echo "Installed HydroShift.app LaunchAgent: $PLIST"
echo "It will now launch automatically at every login."
