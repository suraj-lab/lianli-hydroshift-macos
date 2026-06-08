#!/usr/bin/env bash
# Install a user LaunchAgent that starts OpenRGB minimized at login, waits for
# this daemon's OpenRGB SDK bridge, then loads an OpenRGB profile.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Run this script as your normal user, not with sudo." >&2
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="org.openrgb"
OPENRGB_APP="${OPENRGB_APP:-/Applications/OpenRGB.app/Contents/MacOS/OpenRGB}"
PROFILE="${OPENRGB_PROFILE:-$HOME/.config/OpenRGB/MacOS.orp}"
HOST="${OPENRGB_BRIDGE_HOST:-127.0.0.1}"
PORT="${OPENRGB_BRIDGE_PORT:-6743}"
SCRIPT_DIR="$HOME/Library/Scripts"
SCRIPT="$SCRIPT_DIR/start-openrgb-profile.sh"
PLIST="$HOME/Library/LaunchAgents/OpenRGB.plist"
LOG_DIR="$HOME/Library/Logs/OpenRGB"

if [[ ! -x "$OPENRGB_APP" ]]; then
  echo "OpenRGB binary not found or not executable: $OPENRGB_APP" >&2
  exit 1
fi

mkdir -p "$SCRIPT_DIR" "$HOME/Library/LaunchAgents" "$LOG_DIR"

cat > "$SCRIPT" <<SH
#!/usr/bin/env bash
set -euo pipefail

APP="$OPENRGB_APP"
PROFILE="$PROFILE"
HOST="$HOST"
PORT="$PORT"
LOG="\$HOME/Library/Logs/OpenRGB/start-openrgb-profile.log"

mkdir -p "\$(dirname "\$LOG")"
{
  echo "--- \$(date '+%Y-%m-%d %H:%M:%S %Z') start-openrgb-profile ---"

  if [[ ! -x "\$APP" ]]; then
    echo "OpenRGB binary missing: \$APP"
    exit 1
  fi

  if [[ ! -f "\$PROFILE" ]]; then
    echo "OpenRGB profile missing: \$PROFILE"
    exit 1
  fi

  ready=0
  for _ in {1..90}; do
    if /usr/bin/nc -z "\$HOST" "\$PORT" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done

  if [[ "\$ready" != "1" ]]; then
    echo "HydroShift OpenRGB bridge not reachable at \$HOST:\$PORT after timeout"
  else
    echo "HydroShift OpenRGB bridge reachable at \$HOST:\$PORT"
  fi

  if /usr/bin/pgrep -f "/Applications/OpenRGB.app/Contents/MacOS/OpenRGB" >/dev/null 2>&1; then
    echo "OpenRGB already running; not launching duplicate"
    exit 0
  fi

  exec "\$APP" \
    --gui \
    --startminimized \
    --noautoconnect \
    --client "\$HOST:\$PORT" \
    --profile "\$PROFILE"
} >> "\$LOG" 2>&1
SH
chmod +x "$SCRIPT"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/launchagent.out.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/launchagent.err.log</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST"

UID_NUM="$(id -u)"
launchctl bootout "gui/$UID_NUM" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"
launchctl print "gui/$UID_NUM/$LABEL"

echo "Installed OpenRGB profile LaunchAgent: $PLIST"
echo "Wrapper script: $SCRIPT"
echo "Profile: $PROFILE"
echo "Bridge: $HOST:$PORT"
