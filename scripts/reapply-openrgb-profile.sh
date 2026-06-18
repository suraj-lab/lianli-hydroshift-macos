#!/usr/bin/env bash
# Re-apply the OpenRGB profile after a daemon reconnect/rebind or if the cooler
# ends up on mismatched RGB. Kickstarts the org.openrgb LaunchAgent, which waits
# for this daemon's SDK bridge and then loads the profile.
#
# The daemon also re-sends its last cached RGB frame on reconnect, so this is the
# escalation when that is not enough (e.g. OpenRGB itself was restarted).
set -uo pipefail

LABEL="org.openrgb"
PROFILE="${OPENRGB_PROFILE:-$HOME/.config/OpenRGB/MacOS.orp}"

if [[ ! -f "$PROFILE" ]]; then
  echo "OpenRGB profile not found: $PROFILE" >&2
  echo "Set OPENRGB_PROFILE or save a profile in OpenRGB first." >&2
  exit 1
fi

if ! launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
  echo "LaunchAgent $LABEL is not loaded." >&2
  echo "Install it first: ./scripts/install-openrgb-launchagent.sh" >&2
  exit 1
fi

echo "Kickstarting $LABEL to reload profile: $PROFILE"
launchctl kickstart -k "gui/$UID/$LABEL"
echo "Done. Check status with ./scripts/doctor.sh"
