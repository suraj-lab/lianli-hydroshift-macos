#!/usr/bin/env bash
# Remove the hardened runtime install and fall back to the project-checkout path.
#
# Run as your normal user. It will ask sudo for system-level steps.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.suraj.lianli-hydroshift"
RUNTIME="/Library/Application Support/LianLiHydroShift"
DEST_PLIST="/Library/LaunchDaemons/$LABEL.plist"
TMP_PLIST="$PROJECT_DIR/.generated.$LABEL.plist"
TEMPLATE="$PROJECT_DIR/launchd/$LABEL.plist.template"

echo "=== Uninstalling runtime install ==="

# 1. Remove runtime LaunchDaemon
echo "== Removing LaunchDaemon =="
sudo launchctl bootout system "$DEST_PLIST" >/dev/null 2>&1 || true
sudo rm -f "$DEST_PLIST"
echo "  done"

# 2. Reinstall from project checkout
echo "== Reinstalling from project checkout =="
cd "$PROJECT_DIR"
.venv/bin/python scripts/render-plist.py "$TEMPLATE" "$TMP_PLIST"
plutil -lint "$TMP_PLIST"
sudo cp "$TMP_PLIST" "$DEST_PLIST"
sudo chown root:wheel "$DEST_PLIST"
sudo chmod 0644 "$DEST_PLIST"
sudo launchctl bootstrap system "$DEST_PLIST"
sudo launchctl enable "system/$LABEL"
sudo launchctl kickstart -k "system/$LABEL"
rm -f "$TMP_PLIST"
echo "  done"

# 3. Remove runtime directory
echo "== Removing runtime directory =="
sudo rm -rf "$RUNTIME"
echo "  done"

echo
echo "=== Complete ==="
echo "Daemon now running from $PROJECT_DIR"
echo "Run ./scripts/doctor.sh to verify."
