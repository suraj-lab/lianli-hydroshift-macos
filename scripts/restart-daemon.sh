#!/usr/bin/env bash
# Restart the daemon via an admin-privilege prompt (no terminal window needed).
set -euo pipefail
osascript -e 'do shell script "launchctl kickstart -k system/com.suraj.lianli-hydroshift" with administrator privileges'
