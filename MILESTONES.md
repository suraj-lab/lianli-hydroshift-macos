# Milestones and roadmap

Project: `lianli-hydroshift-macos`

Purpose: native-ish macOS/Hackintosh control of the Lian Li HydroShift II wireless AIO using the reverse-engineered Lian Li wireless USB/RF protocol.

## Status summary

As of 2026-07-01:

- The daemon works on Suraj's Hackintosh.
- It runs persistently at macOS boot via LaunchDaemon.
- Fan and pump control are coolant-temperature based.
- OpenRGB SDK bridge with full RGB profile support (tinyuz compression, cached frame re-send).
- Unbound-AIO recovery is manual via `recover-display.sh` (daemon auto-rebind removed 2026-07-03).
- `doctor.sh` provides one-command health classification.
- Theme index 5 is selected; theme helper (`set-theme.sh`) is available.
- Load-test tuning is accepted as good enough for daily use.
- Replacement display installed and bound (2026-07-11); theme index 5 confirmed
  rendering cleanly. Firmware version not yet checked — see the RMA guardrails.

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

- Document themes using **one index at a time** — do **not** use `--scan-themes`
  (that range walk is what probed the unsafe index 13 on the original display):

```bash
./scripts/set-theme.sh N --reload
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

## Milestone 6 — Packaging / hardening ✅

Status: complete.

Previously the LaunchDaemon ran code directly from the project checkout under
`~/Projects/`. Now runtime files are installed into a root-owned path:

- Runtime: `/Library/Application Support/LianLiHydroShift/`
- Virtual environment: `/Library/Application Support/LianLiHydroShift/.venv/`
- Config: `~/.config/lianli-hydroshift/config.json` (unchanged, user-owned)
- Logs: `~/Library/Logs/lianli-hydroshift/` (unchanged, user-owned)
- Persisted RGB frame: `~/.config/lianli-hydroshift/last_rgb_frame.json`

Completed:

- `scripts/install-runtime.sh` — copies source, creates venv, builds tinyuz
  library, installs/reloads the LaunchDaemon pointing at the hardened paths.
- `scripts/uninstall-runtime.sh` — reverts to the project-checkout path.
- `scripts/render-plist.py` — now accepts `--project-dir` so the plist can be
  generated for any install location.

Not done yet (future):

- Lightweight `.app` wrapper for config/status UX.
- Code signing for distribution beyond this machine.

## Milestone 7 — OpenRGB / lighting integration ✅

Status: complete. Implemented via an OpenRGB SDK bridge that runs inside the
daemon, avoiding any contention over the USB dongles.

Completed:

- OpenRGB SDK server embedded in the daemon (config key: `openrgb_server`).
- OpenRGB connects as a TCP client to `127.0.0.1:6743`.
- Device exposed to OpenRGB as "HydroShift II LCD-S (Wireless)", serial `wireless:<aio-mac>`.
- tinyuz compression bridge (`libtinyuz.dylib`) for direct RGB packet encoding.
- Cached RGB frame re-send on daemon reconnect (survives rebinds/USB reopens).
- `scripts/reapply-openrgb-profile.sh` reloads the OpenRGB profile into the bridge.
- `scripts/install-openrgb-launchagent.sh` / `scripts/uninstall-openrgb-launchagent.sh`
- OpenRGB process and bridge port state are reported by `doctor.sh`.

Known limitation:

- After a daemon reconnect (rebind/USB reopen), the cached frame is primed and
  re-sent, but sometimes the OpenRGB client still needs a manual profile re-kick
  via `./scripts/reapply-openrgb-profile.sh`.

## Milestone 8 — Recovery and self-healing hardening ✅

Status: complete. Started from the 2026-06-18 unbound-AIO/RGB recovery session;
all sub-tasks (8.1–8.6) are done. Test suite at 57 passing.

Context / failure mode observed:

- The daemon was running and the TX/RX dongles were present.
- RX could see the HydroShift AIO record, but the AIO advertised itself as
  unbound:
  - AIO MAC: `2d:a3:74:e5:66:e1`
  - reported master: `00:00:00:00:00:00`
  - reported rx type: `254`
  - device type: `11` / WaterBlock2
- The daemon's normal discovery path filtered by the local master MAC and
  therefore reported `no bound HydroShift/WaterBlock AIO device found` even
  though the hardware was visible.
- Recovery required sending the upstream-style bind packet, saving RF config,
  kickstarting the daemon, then reapplying the OpenRGB profile.
- After recovery, healthy state was verified by:
  - daemon entering control loop
  - `telemetry=ok`
  - fan/pump RPM telemetry present
  - OpenRGB profile matching `wireless:2d:a3:74:e5:66:e1`
  - daemon logging `applied OpenRGB RGB frame: 96 LEDs`

### 8.1 Discovery state classification ✅

Goal: make logs/status distinguish the actual failure mode instead of collapsing
all cases into "no bound AIO".

Completed:

- Added `DiscoveryState` enum and `DiscoveryResult` dataclass plus
  `classify_discovery()` in `lianli_hydroshift/daemon.py`, covering:
  - TX missing
  - RX missing
  - master unknown (TX did not answer GET_MAC scan)
  - no AIO records visible
  - AIO visible but unbound (`master=00:00:00:00:00:00`)
  - AIO visible but bound to a different master
  - AIO visible and bound to this master (the only healthy state)
- Added `aio_detail()` helper that formats the AIO MAC, advertised master,
  channel, rx type, and device type for warning logs.
- `connect_hydroshift()` now classifies the attempt and, for a visible
  unbound/foreign AIO, logs an explicit warning with the AIO identity before
  raising. The bound happy path is unchanged (refactored to share `_prefer_aio`).
- Added `DiscoveryClassificationTests` with representative RX records for each
  state (9 tests), including bound-over-unbound preference.

Acceptance criteria:

- A visible unbound AIO produces an explicit log such as
  `AIO visible but unbound`, not only `no bound AIO found`. ✅
- Existing bound-device discovery behavior remains unchanged. ✅

### 8.2 Recovery tooling: bind-aware recovery

Goal: make the manual recovery path handle both "already bound but stale" and
"visible but unbound" cases.

Completed:

- `scripts/recover-display.py` now detects an AIO record even when it is not
  bound to the local master.
- If needed, it sends the bind packet, waits for convergence, and sends
  SaveConfig before the wireless-theme switch burst.
- Refactored the bind/save RF-frame construction out of the script into reusable
  `cmd_bind_aio()` and `cmd_save_config()` builders in
  `lianli_hydroshift/daemon.py`; the script now imports them. Added the
  `RF_SAVE_CONFIG`, `BROADCAST_MAC`, and `BROADCAST_RX` constants.
- Added a non-destructive `--dry-run` flag to `recover-display.py` (and via the
  `recover-display.sh` wrapper). It diagnoses discovery/bind state and prints the
  intended bind/SaveConfig/switch actions without sending any RF writes.
- Added `BindPacketTests` covering bind packet construction:
  - target master copied to bytes 8..14
  - target rx copied to bytes 14 and 16
  - current PWM copied to bytes 17..21
  - PWM length validation
  - SaveConfig broadcast uses target `ff:ff:ff:ff:ff:ff` and rx `0xff`

Acceptance criteria:

- `./scripts/recover-display.sh` can recover a visible unbound AIO without a
  one-off Python snippet.
- The script prints before/after binding state clearly.

### 8.3 Optional daemon self-healing ✅ (removed 2026-07-03)

> Removed in the 2026-07-03 simplification pass: auto-rebind and discovery
> classification (`DiscoveryState`, `classify_discovery()`) were deleted from
> `daemon.py`. The supported recovery path is `scripts/recover-display.sh`,
> which imports `cmd_bind_aio()` / `cmd_save_config()` from `daemon.py` — it
> does **not** have its own standalone bind implementation, correcting an
> inaccurate note that used to be here. Those two functions (plus
> `RF_SAVE_CONFIG`, `BROADCAST_MAC`, `BROADCAST_RX`) were briefly deleted along
> with the rest of the auto-rebind code, which broke `recover-display.py` with
> an `ImportError`; caught and fixed 2026-07-11 during the replacement-display
> recovery (see below). The section below is kept as a historical record.

Goal: decide whether the daemon should automatically re-bind in the narrow safe
case.

Completed:

- Added config-gated auto-rebind support, defaulting off:

```json
"auto_rebind_visible_aio": false,
"auto_rebind_allow_list": []
```

- `should_auto_rebind(result, cfg)` is a pure decision function that returns
  `(True, reason)` only when auto-rebind is enabled AND all safety conditions
  hold:
  - state is `AIO_UNBOUND`
  - exactly one AIO is visible
  - it is unbound (master is all-zero)
  - no other device already holds the target rx slot under the local master
  - the AIO MAC is in `auto_rebind_allow_list` (empty list = any single unbound
    AIO once enabled)
- `perform_auto_rebind()` sends the bind frame (via `cmd_bind_aio`), re-classifies
  until bound or timeout, then broadcasts SaveConfig (`cmd_save_config`). It
  returns the final `DiscoveryResult` so `connect_hydroshift()` proceeds only if
  the AIO is now bound.
- `connect_hydroshift(cfg)` invokes the above only for `AIO_UNBOUND`; the default
  config never reaches the rebind path. When not eligible / still unbound, it
  logs the exact safe recovery command:
  `to recover, run: ./scripts/recover-display.sh (use --dry-run first)`.
- `auto_rebind_visible_aio` / `auto_rebind_allow_list` added to the config
  defaults, validation (`normalize_mac_list`), and `config/config.example.json`.
- Added `AutoRebindTests` covering the disabled default, eligibility, the
  multi-AIO and wrong-state rejections, allow-list behavior, and MAC
  normalization.

Acceptance criteria:

- Default daemon behavior remains conservative and non-mutating (flag defaults
  off; the rebind path is unreachable without opt-in). ✅
- Enabling auto-rebind makes the daemon recover the 2026-06-18 failure mode
  without manual bind snippets. ✅

### 8.4 OpenRGB/RGB reapply robustness ✅

Goal: keep RGB in sync after daemon restart, AIO rebind, or wireless-theme
re-engage.

Completed:

- Staged OpenRGB logging now distinguishes:
  - bridge listening (`OpenRGB SDK bridge listening on …`)
  - OpenRGB client connected (`OpenRGB client connected from …`)
  - profile/device matched (`OpenRGB profile matched device: … (serial
    wireless:…)`, logged once when the client first reads controller data)
  - actual RGB frame received (tracked via `OpenRgbBridge.has_received_frame`)
  - RGB frame sent (`applied OpenRGB RGB frame: N LEDs`, with a distinct
    `re-applied cached OpenRGB RGB frame: N LEDs` for re-sends)
- The last successfully sent RGB frame is cached in `main()` in a variable that
  survives reconnects. On every (re)connect — which covers AIO rebind, daemon-
  side USB reopen, and wireless-theme re-engage, since all go through the outer
  reconnect loop — the new bridge is seeded with `prime_frame()` so the cooler is
  restored to the last known colours without waiting for the client.
- `OpenRgbBridge.prime_frame()` / `has_received_frame` added; client updates mark
  `_received_frame` so only genuinely received frames are re-applied (never the
  default white state).
- Added `scripts/reapply-openrgb-profile.sh` to kickstart the `org.openrgb`
  LaunchAgent (which waits for the bridge and reloads `~/.config/OpenRGB/MacOS.orp`)
  for the case where OpenRGB itself was restarted.
- OpenRGB LaunchAgent `org.openrgb` and bridge port state are reported by the new
  `scripts/doctor.sh` from 8.5 (the doctor superseded extending `status.sh`).
- Added `OpenRgbBridgeTests` coverage for prime/re-send, received-frame tracking,
  and empty-frame guard.

Acceptance criteria:

- After recovery, status (`doctor`) shows whether RGB has actually been applied
  (last RGB frame age), not just whether OpenRGB is connected. ✅
- A daemon reconnect does not leave the cooler on mismatched RGB if a previous
  RGB frame is known — the cached frame is primed and re-sent. ✅

Live validation:

- 2026-07-03: cooler reconnected to the daemon at boot and applied the colour
  profile automatically; no manual OpenRGB profile reapply was needed. ✅

### 8.5 Status / doctor command ✅

Goal: replace ad-hoc diagnostics with one clear operator-facing health report.

Completed:

- Added `scripts/doctor.sh` plus a `--doctor` mode in
  `lianli_hydroshift/daemon.py`. The shell wrapper handles launchd/LaunchAgent
  state; the Python core handles USB/telemetry/OpenRGB and the final verdict.
- Reports:
  - LaunchDaemon plist installed and loaded state (sudo gap reported explicitly)
  - TX/RX USB presence (non-invasive enumeration, no claim)
  - LCD direct USB presence
  - daemon telemetry freshness (parsed from the log: state + age)
  - discovery/bind state (parsed from the explicit 8.1 log messages)
  - OpenRGB bridge port reachability
  - OpenRGB process running state and last applied RGB frame age
  - OpenRGB LaunchAgent `org.openrgb` state
- Sudo/password failures are explicit (`needs sudo to query …`) instead of a
  misleading `not loaded`.
- Added a pure `classify_health()` returning a `HealthStatus`
  (healthy / disconnected / daemon_down / unbound / stale / rgb_not_applied) and
  a suggested next action, with `DoctorLogParsingTests` and
  `DoctorClassificationTests` covering both the log parsers and the verdict
  precedence.

Notes:

- Master MAC/channel and a full live AIO-record scan are intentionally read from
  the daemon log rather than taken over USB, so `doctor` is non-invasive and does
  not fight the running daemon for the dongles.

Acceptance criteria:

- One command classifies the system as healthy, unbound, disconnected, or RGB
  not-applied (plus daemon-down and stale refinements). ✅
- The command suggests the next safe action for each unhealthy state. ✅
- Verified live: reports `STATUS: HEALTHY` on Suraj's Hackintosh with all signals
  matching the running daemon. ✅

### 8.6 Documentation and runbook ✅

Goal: preserve the recovery knowledge from this incident.

Completed:

- Added `docs/recovery.md` covering:
  - healthy-state doctor example
  - per-`STATUS` recovery (disconnected, daemon-down, unbound, stale,
    rgb-not-applied)
  - unbound AIO symptoms and the bind recovery flow (`recover-display.sh`,
    `--dry-run` first)
  - daemon kickstart
  - OpenRGB profile reapply (`reapply-openrgb-profile.sh`)
  - optional daemon auto-rebind opt-in
  - when to stop and physically inspect/replug or RMA (including the
    non-recoverable LCD-flash corruption guardrail)
  - a healthy-state verification checklist
- Updated `README.md` with a Troubleshooting section: doctor-first guidance, a
  `STATUS -> next step` table, the auto-rebind opt-in, and a link to
  `docs/recovery.md`.

Acceptance criteria:

- The next recurrence can be handled from docs/scripts without reconstructing
  packet details from logs or upstream source. ✅

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

## Simplification pass (2026-07-03)

Deliberate code-shrink before the native-app pivot (~1,500 lines deleted, 57 → 42 tests):

- Doctor extracted from `daemon.py` into `lianli_hydroshift/doctor.py`
  (`--doctor` flag unchanged, delegates to the new module).
- Auto-rebind, discovery classification, and the in-daemon bind packet removed
  (see 8.3 note). Manual recovery via `recover-display.sh` is the supported path;
  doctor's `UNBOUND` verdict now keys off the daemon's
  `HydroShift AIO not found bound to this master` log line.
- `auto_rebind_visible_aio` / `auto_rebind_allow_list` config keys removed;
  leftover keys in existing configs are ignored.
- Original prototype `lianli_daemon_v2.original.py` deleted (preserved in git history).
- OpenRGB bridge trimmed to Direct mode only (Static mode + mode parsing removed).
- `stale_targets` inlined into the control loop.

## Replacement display recovery (2026-07-11)

Replacement unit from the RMA arrived and was installed.

- SwiftBar menu-bar plugin's "Run doctor" / "Restart daemon" actions were
  broken: a hardcoded `open -a Ghostty --args -e ...` doesn't reliably forward
  `-e` args to an already-running Ghostty instance. Fixed by reverting
  "Run doctor" to `terminal=true` and moving "Restart daemon" into
  `scripts/restart-daemon.sh` (native admin-privilege prompt, no terminal
  window, no fragile inline-quoted AppleScript in the plugin metadata line).
- `doctor.sh` initially reported `STATUS: UNBOUND` — the new AIO
  (MAC `03:77:14:50:6c:e1`) was visible but not bound to this Mac's master,
  the expected state for hardware that has never been paired to this dongle
  (same shape as the 2026-06-18 incident in Milestone 8).
- `./scripts/recover-display.sh --dry-run` failed with an `ImportError`:
  `cmd_bind_aio` / `cmd_save_config` had been deleted from `daemon.py` in the
  2026-07-03 simplification pass (see corrected 8.3 note above). Restored both
  functions plus `RF_SAVE_CONFIG`, `BROADCAST_MAC`, `BROADCAST_RX` in
  `daemon.py`; left the rest of the removed auto-rebind machinery deleted
  since nothing else calls it. 42 tests still pass.
- Re-ran `recover-display.sh --dry-run` (confirmed visible-unbound), then
  `recover-display.sh` for real: bound the AIO to master
  `f8:06:e4:e5:66:e4` and re-applied theme index 5.
- Post-recovery `doctor.sh`: `STATUS: HEALTHY` — telemetry 0s old, discovery
  bound, OpenRGB bridge reachable, RGB frame applied 11s ago. Theme index 5
  (previously verified safe) renders cleanly on the replacement display.

Still open:

- Firmware version of the replacement unit not yet confirmed. Per the RMA
  guardrails above, if it differs from `lianli-H2S-1.7`, re-verify the theme
  range conservatively (one index at a time, never `--scan-themes`) before
  assuming 0–12 is still safe.

## Roadmap: native macOS app

Once the replacement display arrives / Lian Li responds, the project pivots from
a CLI daemon to a proper macOS app. High-level intent (to be planned in detail):

- Menu-bar app surfacing live coolant temp, fan/pump RPM, and theme.
- Config UI for the fan/pump curves instead of hand-edited config.json.
- Keep the existing daemon as the control backend; the app talks to it.
- Safe theme picker constrained to the verified 0–12 range.

Interim (2026-07-03): `scripts/hydroshift.5s.sh` SwiftBar plugin ships the
menu-bar essentials now — live coolant/fan/pump from the daemon log, 0–12 theme
picker via `set-theme.sh`, doctor and restart shortcuts. The full native app
(curve editor UI) stays gated on the replacement display.

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

1. Use the system normally and observe logs/noise for a few days; run
   `./scripts/doctor.sh` if anything looks off.
2. Catalogue safe theme indexes only within the verified 0–12 range, one index
   at a time with `--set-theme N --reload`. Do **not** run `--scan-themes`.
3. Harden install location (Milestone 6) if this becomes permanent.
4. Continue native macOS app planning (Milestone 9).
