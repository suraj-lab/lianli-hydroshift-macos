# Milestones and roadmap

Project: `lianli-hydroshift-macos`

Purpose: native-ish macOS/Hackintosh control of the Lian Li HydroShift II wireless AIO using the reverse-engineered Lian Li wireless USB/RF protocol.

## Status summary

As of 2026-06-04:

- The daemon works on Suraj's Hackintosh.
- It runs persistently at macOS boot via LaunchDaemon.
- Fan and pump control are coolant-temperature based.
- Theme index 5 is selected in the live config.
- Load-test tuning is accepted as good enough for daily use.
- OpenRGB / lighting work has not started yet.

## Milestone 0 — Reverse engineering / proof of concept ✅

Source of truth / evidence:

- Arch Linux used to observe and derive USB/RF comms.
- `sgtaziz/lian-li-linux` used as protocol reference.
- Working macOS prototype preserved as `lianli_daemon_v2.original.py`.

Completed:

- Identified TX/RX dongle VID/PID pairs.
- Implemented master MAC/channel discovery.
- Implemented RX device discovery and telemetry parsing.
- Identified HydroShift II / WaterBlock2 device type.
- Verified RF packet chunking: 240-byte RF frame split into 4x 60-byte USB payload chunks.
- Verified AIO params command and offset.
- Verified fan PWM command.
- Verified WaterBlock2 pump RPM -> firmware timer mapping.

## Milestone 1 — Project structure ✅

Completed:

- Created project at `~/Projects/lianli-hydroshift-macos`.
- Converted one-off script into package-style module:
  - `lianli_hydroshift/daemon.py`
- Added:
  - `README.md`
  - `MILESTONES.md`
  - `pyproject.toml`
  - `requirements.txt`
  - `config/config.example.json`
  - `tests/test_daemon.py`
  - install/status/uninstall scripts
- Preserved original prototype as `lianli_daemon_v2.original.py`.

## Milestone 2 — Protocol correctness cleanup ✅

Completed:

- Stable AIO sequence/index behavior instead of rolling sequence counter.
- Device discovery filtered by the TX dongle's master MAC.
- Fixed off-by-one in sliding record parser.
- Added validation tests for:
  - pump timer samples
  - AIO params RF offset 18
  - bound master MAC filtering
  - parser final-offset regression
- Added defensive USB release/cleanup.

## Milestone 3 — Persistent macOS service ✅

Completed:

- Created LaunchDaemon template:
  - `launchd/com.suraj.lianli-hydroshift.plist.template`
- Created optional LaunchAgent template:
  - `launchd/com.suraj.lianli-hydroshift.agent.plist.template`
- Created install/uninstall/status scripts.
- Installed boot-time LaunchDaemon:
  - `/Library/LaunchDaemons/com.suraj.lianli-hydroshift.plist`
- Configured LaunchDaemon to:
  - start at boot
  - restart on crash
  - run from project venv
  - use `ProcessType=Standard`
  - use `Nice=-5`
- Verified service runs under launchd.

Operational commands:

```bash
cd ~/Projects/lianli-hydroshift-macos
./scripts/status.sh
sudo launchctl kickstart -k system/com.suraj.lianli-hydroshift
./scripts/uninstall-launchdaemon.sh
```

## Milestone 4 — Fan/pump curve tuning ✅

Goal:

- Proper daily-driver curve based on coolant/water temperature.
- Quiet at idle/desktop.
- Smooth under load.
- Protective above mid/high 40s coolant.

Completed current fan curve:

```text
26C -> 35/255
30C -> 36/255
32C -> 40/255
34C -> 48/255
36C -> 62/255
38C -> 82/255
40C -> 108/255
42C -> 138/255
44C -> 172/255
46C -> 205/255
48C -> 235/255
50C -> 255/255
```

Completed current pump curve:

```text
28C -> 1800 rpm
34C -> 1800 rpm
38C -> 1900 rpm
42C -> 2200 rpm
46C -> 2600 rpm
48C -> 2900 rpm
50C -> 3200 rpm
```

Completed comfort/safety logic:

- Coolant smoothing.
- Bogus low telemetry rejection.
- Sudden drop rejection.
- Time-aware cooldown allowance so sustained real cooldown can recover from soft-stale.
- Staged stale telemetry handling.
- Fan PWM and pump RPM slew limiting.
- Load-test helper script.

Accepted behavior:

- 5-minute CPU load with 30 workers completed.
- No hard-stale/full-blast behavior in the final accepted run.
- User reported remaining ramp as acceptable.

## Milestone 5 — Theme discovery ⏳

Status: helper tooling done; systematic scan not yet run.

Known:

- `theme_index = 5` currently selected.
- Upstream clamps to 12, but local config allows probing up to 31.
- User suspects at least 10 themes, possibly more.

Completed:

- `--set-theme N` CLI flag: updates `theme_index` in config.json and exits. Clamps to `theme_index_max`.
- `scripts/set-theme.sh <N> [--reload]`: convenience wrapper; `--reload` sends SIGHUP to the running daemon so the change takes effect without a full restart.

Next tasks:

- Run a systematic scan:

```bash
cd ~/Projects/lianli-hydroshift-macos
.venv/bin/python -m lianli_hydroshift.daemon \
  --config ~/.config/lianli-hydroshift/config.json \
  --scan-themes 0 31 \
  --theme-dwell-s 4
```

- Record visible behavior per index:
  - valid / invalid
  - appearance
  - whether it affects LCD/pump head
  - brightness/rotation interactions
- Add `docs/themes.md` once mapped.
- Switch to a discovered theme using the new helper:

```bash
./scripts/set-theme.sh 7 --reload
```

## Milestone 6 — Packaging / hardening ⏳

Status: planned.

Current caveat:

- LaunchDaemon runs code directly from the project checkout under `~/Projects`.

Possible improvements:

- Install runtime files into a root-owned path:
  - `/Library/Application Support/LianLiHydroShift/`
- Keep config in:
  - `/Users/suraj/.config/lianli-hydroshift/config.json`
- Keep logs in:
  - `/Users/suraj/Library/Logs/lianli-hydroshift/`
- Add `scripts/install-runtime.sh` to copy code/venv safely.
- Consider a lightweight `.app` wrapper only for config/status UX.
- Consider code signing later if distributing beyond this machine.

## Milestone 7 — OpenRGB / lighting integration ⏳

Status: planned / research needed.

Goal:

- Explore whether HydroShift II wireless lighting can be exposed to OpenRGB or coordinated with it.

Open questions:

- Does OpenRGB have a suitable plugin/device model for this wireless RF path?
- Should integration be direct OpenRGB support, an OpenRGB plugin, or a bridge process?
- Which lighting surfaces are controllable independently?
  - pump head LEDs
  - LCD theme / brightness / rotation
  - fan LEDs if attached through the wireless group
- How to avoid contention between this daemon and OpenRGB over the TX dongle?

Likely next steps:

1. Catalogue available theme/RGB commands from `lian-li-linux`.
2. Determine whether RGB packet format is stable for HydroShift II.
3. Prototype one static RGB command from this daemon or a separate test script.
4. Only then consider OpenRGB integration.

## Known issues / observations

- RX telemetry can be noisy under heavy CPU load and sometimes reports implausibly low coolant values.
- Under heavy load, invalid low readings are filtered rather than trusted.
- `raw=` in logs means the latest accepted raw coolant reading, not every rejected raw frame.
- LaunchDaemon logs are root-owned because launchd runs the daemon as root; they are normally readable by the user.
- Load-test script intentionally leaves two logical CPU threads free by default to keep PyUSB/launchd responsive.

## Current acceptance state

Daily-driver state is accepted for now:

- Boot autostart works.
- Fan/pump control works.
- Curve is quiet enough and protective enough.
- Further tuning is optional rather than blocking.

Next recommended work, in order:

1. Use the system normally and observe logs/noise for a few days.
2. Catalogue theme indexes.
3. Harden install location if this becomes permanent.
4. Research RGB/OpenRGB integration.
