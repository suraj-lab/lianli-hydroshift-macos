#!/usr/bin/env bash
# One-shot health report for the Lian Li HydroShift setup.
#
# Classifies the system as healthy / disconnected / daemon-down / unbound /
# stale / rgb-not-applied and suggests the next safe action. Read-only: it does
# not send RF writes or take the USB dongles.
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.suraj.lianli-hydroshift"
OPENRGB_LABEL="org.openrgb"
PLIST="/Library/LaunchDaemons/$LABEL.plist"

echo "== launchd =="
if [[ -f "$PLIST" ]]; then
  echo "  LaunchDaemon plist: installed ($PLIST)"
else
  echo "  LaunchDaemon plist: NOT installed"
fi

# System-domain launchctl print needs root. Use sudo non-interactively so we
# never hang on a password prompt, and report the sudo gap explicitly instead of
# printing a misleading "not loaded".
if sudo -n true 2>/dev/null; then
  if sudo -n launchctl print "system/$LABEL" >/dev/null 2>&1; then
    echo "  LaunchDaemon state: loaded"
  else
    echo "  LaunchDaemon state: not loaded"
  fi
else
  echo "  LaunchDaemon state: needs sudo to query (run: sudo launchctl print system/$LABEL)"
fi

# OpenRGB LaunchAgent lives in the user GUI domain — no sudo required.
if launchctl print "gui/$UID/$OPENRGB_LABEL" >/dev/null 2>&1; then
  echo "  OpenRGB LaunchAgent ($OPENRGB_LABEL): loaded"
else
  echo "  OpenRGB LaunchAgent ($OPENRGB_LABEL): not loaded"
fi
echo

# The Python doctor handles USB presence, telemetry freshness, discovery/bind
# state, OpenRGB bridge reachability, and the overall verdict + suggested action.
cd "$PROJECT_DIR"
exec .venv/bin/python -m lianli_hydroshift.daemon --doctor "$@"
