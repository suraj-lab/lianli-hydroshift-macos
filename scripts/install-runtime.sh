#!/usr/bin/env bash
# Harden the daemon install: copy runtime files to a root-owned location
# so the daemon is not tied to the user's project checkout.
#
# Keeps config at ~/.config/lianli-hydroshift/config.json and logs at
# ~/Library/Logs/lianli-hydroshift/ — those are user-owned and portable.
#
# Run as your normal user. It will ask sudo only for system-level steps.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.suraj.lianli-hydroshift"
RUNTIME="/Library/Application Support/LianLiHydroShift"
VENV="$RUNTIME/.venv"
PYTHON="$VENV/bin/python"
CONFIG="$HOME/.config/lianli-hydroshift/config.json"
TMP_PLIST="$PROJECT_DIR/.generated.$LABEL.plist"
DEST_PLIST="/Library/LaunchDaemons/$LABEL.plist"
TEMPLATE="$PROJECT_DIR/launchd/$LABEL.plist.template"

echo "=== Lian Li HydroShift — runtime install ==="
echo "Source:   $PROJECT_DIR"
echo "Runtime:  $RUNTIME"
echo "Config:   $CONFIG"
echo "Logs:     $HOME/Library/Logs/lianli-hydroshift"
echo

# ---------- 1. Copy runtime files ----------
echo "== Creating runtime directory =="
sudo rm -rf "$RUNTIME"
sudo mkdir -p "$RUNTIME/lianli_hydroshift"
sudo mkdir -p "$RUNTIME/scripts"
sudo mkdir -p "$RUNTIME/launchd"
sudo mkdir -p "$RUNTIME/vendor/tinyuz-bridge"
sudo mkdir -p "$RUNTIME/tests"

echo "Copying source code..."
for dir in lianli_hydroshift scripts launchd vendor/tinyuz-bridge tests; do
  sudo cp -R "$PROJECT_DIR/$dir/" "$RUNTIME/$dir/"
done
sudo cp "$PROJECT_DIR/pyproject.toml" "$RUNTIME/"
sudo cp "$PROJECT_DIR/requirements.txt" "$RUNTIME/"
sudo cp "$PROJECT_DIR/README.md" "$RUNTIME/"
sudo chown -R root:wheel "$RUNTIME"
echo "  done"

# ---------- 2. Build venv ----------
echo "== Creating virtual environment =="
sudo "$(command -v python3)" -m venv "$VENV"
sudo "$PYTHON" -m pip install -r "$RUNTIME/requirements.txt" --quiet
echo "  done"

# ---------- 3. Build tinyuz library ----------
echo "== Building tinyuz compression library =="
if [[ -d "$RUNTIME/vendor/tinyuz-bridge" ]]; then
  CXX=c++ sudo -E "$PROJECT_DIR/scripts/build-tinyuz-lib.sh" "$RUNTIME/lianli_hydroshift/libtinyuz.dylib"
  sudo chown root:wheel "$RUNTIME/lianli_hydroshift/libtinyuz.dylib"
  echo "  done"
else
  echo "  skipped (vendor/tinyuz-bridge not found)"
fi

# ---------- 4. Compile-check ----------
echo "== Compile-check =="
PYTHONDONTWRITEBYTECODE=1 sudo -E "$PYTHON" -m py_compile "lianli_hydroshift/daemon.py"
echo "  ok"

# ---------- 5. Install LaunchDaemon ----------
echo "== Installing LaunchDaemon =="
cd "$PROJECT_DIR"
.venv/bin/python scripts/render-plist.py \
  "$TEMPLATE" "$TMP_PLIST" \
  --project-dir "$RUNTIME"
plutil -lint "$TMP_PLIST"

sudo launchctl bootout system "$DEST_PLIST" >/dev/null 2>&1 || true
sudo cp "$TMP_PLIST" "$DEST_PLIST"
sudo chown root:wheel "$DEST_PLIST"
sudo chmod 0644 "$DEST_PLIST"
sudo launchctl bootstrap system "$DEST_PLIST"
sudo launchctl enable "system/$LABEL"
sudo launchctl kickstart -k "system/$LABEL"
rm -f "$TMP_PLIST"
echo "  done"

echo
echo "=== Runtime install complete ==="
echo "Daemon is running from $RUNTIME"
echo "Run ./scripts/doctor.sh to verify."
