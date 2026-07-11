#!/usr/bin/env bash
# SwiftBar plugin: Lian Li HydroShift live stats in the menu bar.
#
# Install:  brew install --cask swiftbar
# then symlink this file into your SwiftBar plugin folder, e.g.:
#   ln -s ~/Projects/lianli-hydroshift-macos/scripts/hydroshift.5s.sh ~/Documents/SwiftBarPlugins/
# The ".5s" in the filename is the refresh interval.
set -uo pipefail

LOG="$HOME/Library/Logs/lianli-hydroshift/com.suraj.lianli-hydroshift.err"
PROJECT="$HOME/Projects/lianli-hydroshift-macos"
CONFIG="$HOME/.config/lianli-hydroshift/config.json"

line=$(tail -c 65536 "$LOG" 2>/dev/null | grep 'telemetry=' | tail -1)
if [[ -z "$line" ]]; then
  echo "🌡 —"
  echo "---"
  echo "no telemetry in daemon log | color=red"
  echo "Run doctor | bash=$PROJECT/scripts/doctor.sh terminal=true"
  exit 0
fi

coolant=$(sed -n 's/.*coolant=\([0-9.]*\)C.*/\1/p' <<<"$line")
state=$(sed -n 's/.*telemetry=\([a-z_]*\).*/\1/p' <<<"$line")
pwm=$(sed -n 's/.*fan_pwm=\([0-9]*\/255\).*/\1/p' <<<"$line")
target=$(sed -n 's/.*pump_target=\([0-9]*\)rpm.*/\1/p' <<<"$line")
rpms=$(sed -n 's/.*rpm=\[\(.*\)\].*/\1/p' <<<"$line" | tr -d ' ')
IFS=',' read -r fan1 fan2 fan3 pump <<<"$rpms"

stamp=$(cut -d, -f1 <<<"$line")
then_s=$(date -j -f "%Y-%m-%d %H:%M:%S" "$stamp" +%s 2>/dev/null || echo 0)
age=$(( $(date +%s) - then_s ))

warn=""
[[ "$state" != "ok" || $age -gt 120 ]] && warn="⚠︎"
echo "🌡 ${coolant}°${warn}"
echo "---"
echo "Fan   ${fan1} / ${fan2} / ${fan3} rpm (pwm ${pwm}) | font=Menlo"
echo "Pump  ${pump} rpm (target ${target}) | font=Menlo"
echo "Telemetry ${state} · ${age}s ago"
echo "---"

theme=$(sed -n 's/.*"theme_index": *\([0-9]*\).*/\1/p' "$CONFIG" 2>/dev/null | head -1)
echo "Theme (current ${theme:-?})"
for n in $(seq 0 12); do
  mark=" "
  [[ "$n" == "${theme:-}" ]] && mark="✓"
  echo "-- ${mark} ${n} | bash=$PROJECT/scripts/set-theme.sh param1=${n} param2=--reload font=Menlo"
done
echo "Run doctor | bash=$PROJECT/scripts/doctor.sh terminal=true"
echo "Restart daemon | bash=$PROJECT/scripts/restart-daemon.sh"
