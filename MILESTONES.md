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
- Valid range confirmed as 0–12. Indexes 13+ corrupt the device display state
  and require a physical USB dongle replug to recover. `theme_index_max` is now
  hard-clamped to 12 in the daemon.

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

## Display recovery incident (2026-06-04)

Theme scan probed index 13 which corrupted the AIO display state. The display
now shows the Lian Li logo and hardware temp overlay simultaneously (hardware
monitoring mode) instead of the wireless theme. Fan/pump control and telemetry
are completely unaffected — the daemon is running normally.

Software recovery attempts (wireless switch command bursts, CMD_RESET on RX
dongle) did not restore the display. A full PSU power cycle also did not fix
it, suggesting the bad state was persisted to device flash.

Root cause: the corrupted theme index (13+) is stored in the LCD controller's
flash. On startup the LCD controller firmware loads the bad settings, crashes,
and stops servicing its USB bulk endpoint. The wireless module is on a separate
code path and continues to work normally (fan/pump control unaffected).

Recovery path: use Lian Li L-Connect 3 on Windows to reflash the LCD receiver
firmware (Settings → Firmware → Local Update → select "Receiver"). The firmware
file for the LCD-S Receiver (v1.2) is available at:
  AWS:    https://lianli-update-2025.s3.ap-southeast-1.amazonaws.com/FW_0618/lianliH2SRF_1_22
  Aliyun: https://lianli-update.oss-cn-beijing.aliyuncs.com/FW_0618/lianliH2SRF_1_22

L-Connect 3 has access to a USB bootloader mode on the LCD controller that
bypasses the crashed application firmware. This cannot be replicated from macOS
with the current reverse-engineered protocol — the LCD's USB bulk endpoint does
not service writes when the application firmware has crashed.

Device USB identity: VID=1CBE PID=A034 ("LIANLI / lianli-H2S-1.7")
DES-CBC key/IV for LCD command protocol: "slv3tuzx" (from lian-li-linux)
Command codes: CMD_REBOOT=0x0B, CMD_SWITCH_TO_DESKTOP=0x96, CMD_STOP_PLAY=0x7B

`theme_index_max` has been hard-clamped to 12 to prevent recurrence.

### Recovery attempts — all failed (2026-06-16/17)

Every software path to reach the crashed LCD controller was exhausted:

- **macOS (pyusb/IOKit)**: bulk OUT (EP 0x01) writes time out — even raw zeros.
  Control transfers on EP0 return STALL (Errno 32 pipe error), confirming the
  USB hardware is alive but the firmware does not service the bulk endpoint.
- **Arch Linux (usbfs)**: same failure — bulk endpoint never ACKs. Rules out a
  macOS/IOKit-specific limitation.
- **Windows + L-Connect 3**: the wireless controller fails to initialise on
  Windows, so L-Connect 3 never reaches the LCD firmware-flash screen at all.
- **Windows + Zadig/WinUSB + lcd-reboot.py**: planned as last resort (assign
  WinUSB to 1CBE:A034 directly, bypassing the dongle). Not attempted after the
  RMA was agreed — see below.

Conclusion: the LCD controller firmware is genuinely crashed in flash and is
unreachable by any host OS. This is a firmware defect — a user-writable theme
index should never be able to permanently brick the controller.

### RMA outcome (2026-06-17)

Lian Li agreed to send a **replacement display** after being shown the symptom
and serial number (`5047490364c6c701w`). They treated it as a defect rather than
user error. Awaiting their answer on what happens if the replacement also fails.

Next-step guardrails for the replacement unit:

- Keep `theme_index_max` clamped to 12. Do **not** raise it. 13+ bricks the
  hardware with no software recovery path on any OS.
- Confirm the replacement's firmware version before any theme work. If it ships
  newer than `lianli-H2S-1.7`, re-verify the valid theme range conservatively
  using `--set-theme N` one index at a time — do **not** run the auto `--scan-themes`
  range, which is what walked into the bad index originally.

## Roadmap: native macOS app

Once the replacement display arrives / Lian Li responds, the project pivots from
a CLI daemon to a proper macOS app. High-level intent (to be planned in detail):

- Menu-bar app surfacing live coolant temp, fan/pump RPM, and theme.
- Config UI for the fan/pump curves instead of hand-edited config.json.
- Keep the existing daemon as the control backend; the app talks to it.
- Safe theme picker constrained to the verified 0–12 range.

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
